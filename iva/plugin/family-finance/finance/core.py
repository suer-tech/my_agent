import calendar
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


def money(value):
    """Strict RUB string -> integer kopecks; never accept binary floats."""
    if not isinstance(value, str):
        raise ValueError('Деньги передавайте строкой, например "1234.56"')
    try:
        number = Decimal(value.replace(' ', '').replace('\u00a0', '').replace('\u202f', '').replace(',', '.'))
        if not number.is_finite() or number.as_tuple().exponent < -2:
            raise ValueError('Нужна конечная сумма с максимум двумя знаками после запятой')
        if abs(number) > Decimal('1000000000000'):
            raise ValueError('Слишком большая сумма')
        result = int(number * 100)
        return result
    except InvalidOperation as exc:
        raise ValueError('Некорректная сумма') from exc


def rub(kopecks):
    return f'{Decimal(kopecks) / 100:.2f}'


def day(value):
    return date.fromisoformat(value).isoformat()


def ident():
    return uuid.uuid4().hex


def local_today():
    return datetime.now(timezone(timedelta(hours=5))).date().isoformat()


def month_day(year, month, requested):
    return date(year, month, min(requested, calendar.monthrange(year, month)[1]))


def next_month(value):
    return month_day(value.year + (value.month == 12), value.month % 12 + 1, value.day)


class Ledger:
    def __init__(self, path):
        if str(path) != ':memory:':
            runtime_path=Path(__file__).resolve().parents[3]/'data/iva-runtime.json'
            if runtime_path.exists():
                runtime=json.loads(runtime_path.read_text(encoding='utf-8'))
                if runtime.get('mode')=='telegram' and Path(path).resolve() in {Path(p).resolve() for p in runtime.get('retired_finance_databases',[])}:
                    raise ValueError('Эта копия реестра выведена из работы. Используйте единую рабочую финансовую базу из data/iva-runtime.json.')
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA user_version').fetchone()[0] not in (0,1):
            self.db.close()
            raise ValueError('Неизвестная версия базы: нужна совместимая версия модуля')
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,opening INTEGER NOT NULL,as_of TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS account_policies(account TEXT PRIMARY KEY REFERENCES accounts(id),available INTEGER NOT NULL,opening_at_start INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,kind TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),on_date TEXT NOT NULL,account TEXT NOT NULL REFERENCES accounts(id),target TEXT REFERENCES accounts(id),note TEXT NOT NULL,source TEXT UNIQUE NOT NULL,occurrence TEXT UNIQUE,void INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,amount INTEGER NOT NULL,account TEXT NOT NULL REFERENCES accounts(id),monthday INTEGER NOT NULL,start TEXT NOT NULL,end TEXT,lead_days INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,amount INTEGER NOT NULL,account TEXT NOT NULL REFERENCES accounts(id),on_date TEXT NOT NULL,lead_days INTEGER NOT NULL,cancelled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,payload TEXT NOT NULL,source TEXT UNIQUE NOT NULL,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,action TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS loans(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reminders(id TEXT PRIMARY KEY,due TEXT NOT NULL,text TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0);
        PRAGMA user_version=1;
        ''')
        from . import bank
        bank.setup(self.db)
        from . import cards
        cards.setup(self.db)
        from . import reported_debts
        reported_debts.setup(self.db)
        from . import utilities
        utilities.setup(self.db)
        from . import planning
        planning.setup(self.db)
        from . import reconciliation
        reconciliation.setup(self.db)
        from . import savings
        savings.setup(self.db)

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    @contextmanager
    def transaction(self):
        """Nested ledger changes must not commit an outer confirmed batch early."""
        nested = self.db.in_transaction
        savepoint = 'sp_'+ident()
        self.db.execute('SAVEPOINT '+savepoint if nested else 'BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            if nested:
                self.db.execute('ROLLBACK TO '+savepoint)
                self.db.execute('RELEASE '+savepoint)
            else: self.db.rollback()
            raise
        else:
            if nested: self.db.execute('RELEASE '+savepoint)
            else: self.db.commit()

    def audit(self, action, payload):
        self.db.execute('INSERT INTO audit(at,action,payload) VALUES(?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), action, json.dumps(payload, ensure_ascii=False)))

    def account(self, name, opening, as_of):
        key = ident()
        with self.transaction():
            self.db.execute('INSERT INTO accounts VALUES(?,?,?,?)', (key, name, money(opening), day(as_of)))
            self.audit('account', {'id': key, 'name': name})
        return {'id': key}

    def account_policy(self, account, available=True, opening_at_start=False):
        if type(available) is not bool or type(opening_at_start) is not bool:
            raise ValueError('Policy flags must be booleans')
        if not self.rows('SELECT id FROM accounts WHERE id=?',(account,)):
            raise ValueError('Unknown account')
        with self.transaction():
            self.db.execute('INSERT INTO account_policies VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET available=excluded.available,opening_at_start=excluded.opening_at_start',(account,int(available),int(opening_at_start)))
            self.audit('account_policy',{'account':account,'available':available,'opening_at_start':opening_at_start})
        return {'account':account,'available':available,'opening_at_start':opening_at_start}

    def available_balances(self, on_date):
        blocked={r['account'] for r in self.rows('SELECT account FROM account_policies WHERE available=0')}
        return {k:v for k,v in self.balances(on_date).items() if k not in blocked}

    def validate_operation(self, data):
        kind = data.get('kind')
        if kind not in ('income', 'expense', 'transfer'):
            raise ValueError('kind: income, expense или transfer')
        amount = money(data.get('amount'))
        if amount <= 0:
            raise ValueError('Сумма должна быть положительной')
        when = day(data['date'])
        account = data['account']
        target = data.get('target') if kind == 'transfer' else None
        if kind == 'transfer' and (not target or target == account):
            raise ValueError('Для перевода нужны два разных счёта')
        for key in [account] + ([target] if target else []):
            found = self.rows('SELECT * FROM accounts WHERE id=?', (key,))
            intraday=self.rows('SELECT account FROM family_balance_reports WHERE account=?',(key,)) or self.rows('SELECT account FROM account_policies WHERE account=? AND opening_at_start=1',(key,))
            if not found or when < found[0]['as_of'] or when == found[0]['as_of'] and not intraday:
                raise ValueError('Неизвестный счёт или дата не позже начального остатка на конец дня')
        occurrence = data.get('occurrence')
        if data.get('budget_ref'):
            if kind!='expense' or occurrence or not self.rows('SELECT ref FROM family_expenses WHERE ref=?',(data['budget_ref'],)):
                raise ValueError('budget_ref: только существующий семейный расход без occurrence; частичные траты уменьшают месячный остаток')
        if occurrence:
            expected_date = day(occurrence.rsplit(':',1)[-1])
            if occurrence.startswith('family-expense:'):
                from .planning import expense_events
                last=month_day(int(expected_date[:4]),int(expected_date[5:7]),31).isoformat()
                expected=expense_events(self,expected_date,last)
            elif occurrence.startswith('income:'):
                from .planning import income_events
                # Nominal identity is stable even when weekend rules move the date.
                first=expected_date[:7]+'-01';last=month_day(int(expected_date[:4]),int(expected_date[5:7]),31).isoformat()
                expected=income_events(self,first,last)
            else:expected = self.events(expected_date, expected_date, include_paid=True)
            event = next((e for e in expected if e['occurrence'] == occurrence), None)
            aggregate=self.rows('SELECT account FROM family_balance_reports WHERE account=?',(account,))
            event_account=event.get('account') if event else None
            if not event or event['kind'] != kind or event_account != account and not (event_account is None and aggregate):
                raise ValueError('occurrence не соответствует ожидаемой операции/счёту')
            if occurrence.startswith('family-expense:') and event.get('amount')!=amount:
                raise ValueError('Для частичной траты используйте budget_ref; occurrence закрывает весь остаток месяца')
        return kind, amount, when, account, target

    def stage(self, data, source):
        """OCR/transcript candidates are untrusted. No balance mutation here."""
        if not isinstance(source, str) or not source.strip():
            raise ValueError('Нужен стабильный source: telegram:<chat>:<message>[:item]')
        old = self.rows('SELECT * FROM drafts WHERE source=?', (source,))
        if old:
            return old[0]
        questions = []
        for key in ('kind', 'amount', 'date', 'account'):
            if not data.get(key):
                questions.append(f'Уточните {key}')
        if data.get('ambiguities'):
            questions.extend(data['ambiguities'])
        if not questions:
            self.validate_operation(data)
        key = ident()
        payload = {**data, 'questions': questions}
        with self.transaction():
            self.db.execute('INSERT INTO drafts VALUES(?,?,?,?)', (key, json.dumps(payload, ensure_ascii=False), source, 'pending'))
            self.audit('stage', {'id': key, 'source': source})
        return {'id': key, 'candidate': payload, 'status': 'pending'}

    def confirm(self, draft_id, confirmed, resolved=None, allow_similar=False):
        if confirmed is not True:
            raise ValueError('Нужно явное подтверждение пользователя')
        with self.transaction():
            draft = self.rows('SELECT * FROM drafts WHERE id=?', (draft_id,))
            if not draft:
                raise ValueError('Черновик не найден')
            draft = draft[0]
            old = self.rows('SELECT id FROM operations WHERE source=?', (draft['source'],))
            if old:
                return {'id': old[0]['id'], 'duplicate': True}
            data = json.loads(draft['payload'])
            if resolved:
                data.update(resolved)
            if data.get('ambiguities') or any(not data.get(k) for k in ('kind','amount','date','account')):
                raise ValueError('Сначала уточните сумму, дату, счёт и ambiguities (пустой список после уточнения)')
            kind, amount, when, account, target = self.validate_operation(data)
            similar = self.rows('SELECT id FROM operations WHERE kind=? AND amount=? AND on_date=? AND account=? AND target IS ? AND void=0', (kind, amount, when, account, target))
            if similar and not allow_similar:
                return {'possible_duplicate': similar, 'requires_user_confirmation': True}
            key = ident()
            self.db.execute('INSERT INTO operations(id,kind,amount,on_date,account,target,note,source,occurrence) VALUES(?,?,?,?,?,?,?,?,?)',
                            (key,kind,amount,when,account,target,str(data.get('note','')),draft['source'],data.get('occurrence')))
            if data.get('budget_ref'):
                self.db.execute('INSERT INTO family_expense_receipts VALUES(?,?,?)',(key,data['budget_ref'],when[:7]))
            self.db.execute("UPDATE drafts SET status='confirmed' WHERE id=?", (draft_id,))
            self.audit('confirm', {'id': key, 'draft': draft_id, 'resolved': resolved})
        return {'id': key, 'duplicate': False}

    def void(self, operation_id, reason):
        if not reason.strip():
            raise ValueError('Нужна причина исправления')
        with self.transaction():
            if not self.db.execute('UPDATE operations SET void=1,occurrence=NULL WHERE id=? AND void=0', (operation_id,)).rowcount:
                raise ValueError('Активная операция не найдена')
            self.audit('void', {'id': operation_id, 'reason': reason})
        return {'voided': operation_id, 'next': 'Создайте и подтвердите исправленную операцию с новым source'}

    def balances(self, on_date):
        when = day(on_date)
        result = {}
        for a in self.rows('SELECT * FROM accounts'):
            if when < a['as_of']:
                raise ValueError('Прогноз до даты начального остатка невозможен')
            value = a['opening']
            value += self.db.execute('SELECT COALESCE(SUM(delta),0) FROM balance_adjustments WHERE account=? AND as_of<=?',(a['id'],when)).fetchone()[0]
            for op in self.rows('SELECT * FROM operations WHERE void=0 AND on_date<=? AND (account=? OR target=?)', (when,a['id'],a['id'])):
                value += op['amount'] if op['kind']=='income' or op['target']==a['id'] else -op['amount']
            result[a['id']] = value
        return result

    def schedule(self, name, kind, amount, account, date=None, monthday=None, start=None, end=None, lead_days=1):
        from .bank import label
        if kind=='expense' and any(label(r['name'])==label(name) for r in self.rows('SELECT name FROM utility_bills UNION SELECT name FROM reported_loans WHERE active=1')):
            raise ValueError('У этого обязательства уже есть месячный план. Уточните его вместо второго расхода.')
        if kind=='expense' and any(label(r['name'])==label(name) for r in self.rows('SELECT name FROM credit_cards WHERE active=1')):
            raise ValueError('По этой карте уже есть единый месячный процентный план. Второй расход не создаётся.')
        if kind=='expense' and any(label(r['loan_name'])==label(name) for r in self.rows('SELECT loan_name FROM bank_schedules WHERE active=1')):
            raise ValueError('Для этого кредита уже есть точный банковский график. Второе приблизительное обязательство не создаётся.')
        if kind not in ('income','expense') or money(amount)<=0:
            raise ValueError('План: положительный income/expense')
        if not self.rows('SELECT id FROM accounts WHERE id=?',(account,)):
            raise ValueError('Счёт не найден')
        if not isinstance(lead_days,int) or not 0<=lead_days<=365:
            raise ValueError('lead_days: 0..365')
        key = ident()
        with self.transaction():
            if date:
                self.db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,0)', (key,name,kind,money(amount),account,day(date),lead_days))
            else:
                if not start or (monthday is not None and (not isinstance(monthday,int) or not 1<=monthday<=31)):
                    raise ValueError('Нужны start и monthday 1..31; null допустим для несрочного расхода')
                if monthday is None and kind!='expense':raise ValueError('Для ожидаемого дохода нужна определённая дата')
                if end and day(end)<day(start):
                    raise ValueError('end раньше start')
                self.db.execute('INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?)', (key,name,kind,money(amount),account,monthday or 0,day(start),day(end) if end else None,lead_days))
            self.audit('schedule', {'id':key,'name':name})
        return {'id':key}

    def cancel_schedule(self, schedule_id, from_date):
        when = day(from_date)
        with self.transaction():
            self.db.execute('UPDATE rules SET end=? WHERE id=?', ((date.fromisoformat(when)-timedelta(days=1)).isoformat(),schedule_id))
            self.db.execute('UPDATE plans SET cancelled=1 WHERE id=? AND on_date>=?', (schedule_id,when))
            self.audit('cancel_schedule', {'id':schedule_id,'from':when})
        return {'cancelled':schedule_id}

    def events(self, start, end, include_paid=False):
        start, end = day(start), day(end)
        if start>end or (date.fromisoformat(end)-date.fromisoformat(start)).days>3660:
            raise ValueError('Период: от 0 до 3660 дней')
        events = []
        for p in self.rows('SELECT * FROM plans WHERE cancelled=0 AND on_date BETWEEN ? AND ?', (start,end)):
            events.append({**p,'date':p['on_date'],'occurrence':p['id']+':'+p['on_date']})
        cursor = date.fromisoformat(start).replace(day=1)
        while cursor.isoformat()<=end:
            for r in self.rows('SELECT * FROM rules'):
                when = (max(cursor,date.fromisoformat(r['start'])).isoformat() if r['monthday']==0 else month_day(cursor.year,cursor.month,r['monthday']).isoformat())
                if when[:7]==cursor.strftime('%Y-%m') and start<=when<=end and r['start']<=when and (not r['end'] or when<=r['end']):
                    events.append({**r,'date':when,'undated':r['monthday']==0,'occurrence':r['id']+':'+when})
            cursor = next_month(cursor)
        paid = {r['occurrence'] for r in self.rows('SELECT occurrence FROM operations WHERE void=0 AND occurrence IS NOT NULL')}
        from .cards import events as card_events
        events.extend(card_events(self,start,end))
        from .reported_debts import events as debt_events
        events.extend(debt_events(self,start,end,include_paid))
        from .utilities import events as utility_events
        events.extend(utility_events(self,start,end))
        from .planning import income_events
        events.extend(income_events(self,start,end))
        today=local_today()
        for row in self.rows('SELECT p.*,s.account,s.loan_name,s.document_id FROM bank_payments p JOIN bank_schedules s ON s.id=p.schedule_id WHERE s.active=1 AND p.amount>0 AND (p.on_date BETWEEN ? AND ? OR (p.unpaid_confirmed=1 AND p.on_date<?))',(start,end,start)):
            days=max(0,(date.fromisoformat(today)-date.fromisoformat(row['on_date'])).days)
            events.append({**row,'kind':'expense','date':row['on_date'],'status':'overdue' if days and row['unpaid_confirmed'] else 'planned','days_overdue':days if row['unpaid_confirmed'] else 0,'name':row['loan_name']+' — банковский график','lead_days':1,'occurrence':'bank:'+row['id']+':'+row['on_date'],'source_document':row['document_id']})
        return sorted([e for e in events if include_paid or e['occurrence'] not in paid], key=lambda e:(e['date'],e['kind']=='income',e['occurrence']))

    def configure(self, reserve='0.00', daily='0.00', daily_account=None):
        values = {'reserve':money(reserve),'daily':money(daily),'daily_account':daily_account}
        if values['reserve']<0 or values['daily']<0:
            raise ValueError('Резерв и ежедневный бюджет неотрицательны')
        if values['daily'] and not self.rows('SELECT id FROM accounts WHERE id=?',(daily_account,)):
            raise ValueError('Нужен счёт ежедневных расходов')
        with self.transaction():
            for key,value in values.items():
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key,json.dumps(value)))
            self.audit('configure',values)
        return values

    def forecast(self, as_of, until, purchase=None):
        if self.rows('SELECT ref FROM family_incomes WHERE amount IS NULL'):
            raise ValueError('Суммы ожидаемых доходов неизвестны: используйте family_forecast для частичного семейного прогноза.')
        if self.rows("SELECT id FROM utility_bills WHERE account IS NULL"):raise ValueError("Для денежного прогноза нужно выбрать счёт оплаты коммунальных услуг/связи (utility_bind).")
        if self.rows('SELECT id FROM reported_loans WHERE active=1 AND account IS NULL'):raise ValueError('Месячный план отдельного кредита сохранён. Для денежного прогноза нужно выбрать счёт оплаты (debt_bind).')
        if self.rows('SELECT id FROM credit_cards WHERE active=1 AND account IS NULL'):raise ValueError('План кредитной карты сохранён. Для денежного прогноза нужно выбрать счёт оплаты (card_bind).')
        if self.rows('SELECT id FROM bank_schedules WHERE active=1 AND account IS NULL'):raise ValueError('Точный банковский график сохранён, но для прогноза нужно привязать его к счёту (bank_bind).')
        as_of, until = day(as_of), day(until)
        if until<as_of or (date.fromisoformat(until)-date.fromisoformat(as_of)).days>3660:
            raise ValueError('Некорректный горизонт')
        settings = {r['key']:json.loads(r['value']) for r in self.rows('SELECT * FROM settings')}
        reserve,daily = settings.get('reserve',0),settings.get('daily',0)
        balances = self.available_balances(as_of)
        events = [e for e in self.events((date.fromisoformat(as_of)+timedelta(days=1)).isoformat(),until) if e['date']>as_of] if until>as_of else []
        # Unsettled historical obligations since each account's opening are carried
        # conservatively into today; past expected income is never spendable cash.
        earliest = min((r['as_of'] for r in self.rows('SELECT as_of FROM accounts')),default=as_of)
        overdue = self.events(earliest,as_of)
        current_undated=[e for e in self.events(as_of[:7]+'-01',as_of) if e.get('undated')]
        existing_occurrences={e['occurrence'] for e in overdue}
        overdue += [e for e in current_undated if e['occurrence'] not in existing_occurrences]
        openings = {r['id']:r['as_of'] for r in self.rows('SELECT id,as_of FROM accounts')}
        overdue = [e for e in overdue if e.get('period_status')!='unknown' and (e['date']>openings[e['account']] or e.get('undated') or e.get('unpaid_confirmed'))]
        for event in overdue:
            if event.get('unpaid_confirmed'):
                event['days_overdue']=max(0,(date.fromisoformat(as_of)-date.fromisoformat(event['date'])).days)
                event['status']='overdue' if event['days_overdue'] else 'unpaid'
        for e in overdue:
            if e['kind']=='expense':
                events.append({**e,'original_date':e['date'],'date':as_of,'overdue':True})
        # Future booked facts also count, linked plans have already been removed.
        for op in self.rows('SELECT * FROM operations WHERE void=0 AND on_date>? AND on_date<=?', (as_of,until)):
            events.append({**op,'date':op['on_date'],'name':op['note']})
        if purchase:
            amount = money(purchase['amount'])
            if amount<=0 or purchase['account'] not in balances or not as_of<=day(purchase['date'])<=until:
                raise ValueError('Покупка: положительная сумма, известный счёт, дата в горизонте')
            events.append({'kind':'expense','amount':amount,'account':purchase['account'],'date':purchase['date'],'name':'Покупка'})
        days = []
        cursor = date.fromisoformat(as_of)
        minimum = sum(balances.values())
        min_accounts = dict(balances)
        first_gap = as_of if minimum<reserve or any(v<0 for v in balances.values()) else None
        while cursor.isoformat()<=until:
            when = cursor.isoformat()
            if when>as_of and daily:
                events.append({'kind':'expense','amount':daily,'account':settings['daily_account'],'date':when,'name':'Повседневный бюджет'})
            # Conservative intraday order: all debits before credits on same day.
            today = sorted([e for e in events if e['date']==when],key=lambda e:e['kind']=='income')
            for event in today:
                acc = event['account']
                if acc in balances: balances[acc] += event['amount'] if event['kind']=='income' else -event['amount']
                if event['kind']=='transfer' and event['target'] in balances:
                    balances[event['target']] += event['amount']
                minimum = min(minimum,sum(balances.values()))
                for key in balances: min_accounts[key] = min(min_accounts[key],balances[key])
                if first_gap is None and (sum(balances.values())<reserve or any(v<0 for v in balances.values())):
                    first_gap = when
            minimum = min(minimum,sum(balances.values()))
            for key in balances: min_accounts[key] = min(min_accounts[key],balances[key])
            if first_gap is None and (sum(balances.values())<reserve or any(v<0 for v in balances.values())):
                first_gap = when
            days.append({'date':when,'total':sum(balances.values()),'balances':dict(balances),'events':today})
            cursor += timedelta(days=1)
        configured = all(k in settings for k in ('reserve','daily','daily_account'))
        from .reported_debts import unresolved
        unknown_periods=unresolved(self,as_of)
        from .utilities import unresolved as utility_unresolved
        unknown_periods+=utility_unresolved(self,as_of)
        estimated_events=[e for e in events if e.get('amount_is_estimate')]
        return {'unit':'kopecks','as_of_end_of_day':as_of,'until':until,'reserve':reserve,'daily_budget':daily,'budget_configured':configured,
                'has_estimated_amounts':bool(estimated_events),'estimated_obligations':estimated_events,
                'minimum':minimum,'minimum_accounts':min_accounts,'first_gap':first_gap,'overdue_unsettled':overdue,
                'uncertain_obligations':unknown_periods,'affordable':configured and bool(balances) and not unknown_periods and minimum>=reserve and all(v>=0 for v in min_accounts.values()),'days':days,
                'assumption':'Начальные остатки и as_of — конец дня. Расходы раньше доходов внутри дня. Ежедневный бюджет добавляется ко всем обязательствам. Для расходов без даты полная месячная сумма резервируется консервативно сразу; это расчётная бронь, не выдуманная дата списания.'}

    def calendar(self, start, end):
        return [{**event,'date':None,'period':event['date'][:7]} if event.get('undated') else event for event in self.events(day(start)[:7]+'-01',end) if event['date']>=start or event.get('undated') or event.get('unpaid_confirmed') or event.get('period_status')=='unknown']

    def queue_reminders(self, today, until):
        today = day(today)
        earliest = min((r['as_of'] for r in self.rows('SELECT as_of FROM accounts')),default=today)
        openings = {r['id']:r['as_of'] for r in self.rows('SELECT id,as_of FROM accounts')}
        events = [e for e in self.events(earliest,until) if e.get('period_status')!='unknown' and not e.get('undated') and (e['account'] not in openings or e['date']>openings[e['account']] or e.get('unpaid_confirmed'))]
        from .savings import reminder_events
        events += reminder_events(self, until, today)
        with self.transaction():
            for e in events:
                if e.get('amount') is None:continue
                due = (date.fromisoformat(e['date'])-timedelta(days=e['lead_days'])).isoformat()
                key = e['occurrence']
                self.db.execute("INSERT INTO reminders(id,due,text) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET due=excluded.due,text=excluded.text,state=CASE WHEN reminders.state='cancelled' THEN 'pending' ELSE reminders.state END",
                                (key,due,f"{e['date']}: {e['name']} — {'оценка ' if e.get('amount_is_estimate') else ''}{rub(e['amount'])} RUB. Если уже оплачено/получено, подтвердите операцию в реестре."))
            for e in events:
                if e['occurrence'].startswith('savings:'):
                    self.db.execute('UPDATE reminders SET text=? WHERE id=?',(f"{e['date']}: {e['name']} — {rub(e['amount'])} RUB. Это план накопления, не списание. Сообщите Иве, сколько фактически отложили, или попросите пересчитать план.",e['occurrence']))
            active = {e['occurrence'] for e in events}
            for old in self.rows("SELECT id FROM reminders WHERE state='pending'"):
                if old['id'].rsplit(':',1)[-1]<=until and old['id'] not in active:
                    self.db.execute("UPDATE reminders SET state='cancelled' WHERE id=?",(old['id'],))
        return self.rows("SELECT * FROM reminders WHERE state='pending' AND due<=?",(today,))

    def loan(self, name, principal, annual_rate, payment, first_due, monthday, remaining_months, fees='0.00'):
        p, pay, fee = money(principal),money(payment),money(fees)
        rate = Decimal(annual_rate)
        if not rate.is_finite() or not 0<=rate<=200 or min(p,pay)<=0 or fee<0 or not 1<=monthday<=31 or not 1<=remaining_months<=1200:
            raise ValueError('Некорректные условия кредита')
        payload = dict(name=name,principal=p,annual_rate=str(rate),payment=pay,first_due=day(first_due),monthday=monthday,remaining_months=remaining_months,fees=fee)
        key = ident()
        with self.transaction():
            self.db.execute('INSERT INTO loans VALUES(?,?)',(key,json.dumps(payload,ensure_ascii=False)))
            self.audit('loan',{'id':key,**payload})
        return {'id':key}

    def loan_scenario(self, loan_id, extra='0.00', lump_sum='0.00', mode='term'):
        found = self.rows('SELECT payload FROM loans WHERE id=?',(loan_id,))
        if not found: raise ValueError('Кредит не найден')
        loan = json.loads(found[0]['payload'])
        extra,lump = money(extra),money(lump_sum)
        if min(extra,lump)<0 or lump>loan['principal'] or mode not in ('term','payment'):
            raise ValueError('Некорректное досрочное погашение/режим')
        balance = loan['principal']-lump
        rate = Decimal(loan['annual_rate'])/1200
        payment = loan['payment']
        if mode=='payment' and balance:
            n = loan['remaining_months']
            payment = int((Decimal(balance)*rate/(1-(1+rate)**(-n)) if rate else Decimal(balance)/n).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) + loan['fees']
        rows,interest_total,fees_total = [],0,0
        due = date.fromisoformat(loan['first_due'])
        for index in range(1200):
            if balance<=0: break
            interest = int((Decimal(balance)*rate).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            reduction = payment+extra-interest-loan['fees']
            if mode=='payment' and index+1==loan['remaining_months']:
                reduction = balance  # Last-payment kopeck adjustment preserves the term.
            if reduction<=0: raise ValueError('Платёж не покрывает проценты и комиссии')
            reduction = min(reduction,balance)
            balance -= reduction
            interest_total += interest
            fees_total += loan['fees']
            rows.append({'date':due.isoformat(),'payment':reduction+interest+loan['fees'],'interest':interest,'fees':loan['fees'],'principal':reduction,'balance':balance})
            nxt = next_month(due.replace(day=1))
            due = month_day(nxt.year,nxt.month,loan['monthday'])
        if balance: raise ValueError('Кредит не погашен за 1200 месяцев')
        return {'unit':'kopecks','model':'monthly_nominal_annual_rate/12','months':len(rows),'payment':payment,
                'overpayment':interest_total+fees_total,'interest':interest_total,'fees':fees_total,
                'total_paid':loan['principal']+interest_total+fees_total,'lump_sum':lump,'schedule':rows,
                'warning':'Модель с ежемесячным начислением. Дневные проценты, страховки и правила банка требуют отдельной сверки; не банковский график.'}

    def snapshot(self):
        result={table:self.rows('SELECT * FROM '+table) for table in ('accounts','operations','rules','plans','drafts','loans','settings','bank_schedules','credit_cards','reported_loans','reported_loan_periods','utility_bills','utility_quotes','utility_periods','family_balance_reports','family_incomes','family_income_profiles','family_income_groups','family_income_receipts','family_expenses','family_expense_periods','family_expense_policies','family_debts')}
        result['balance_adjustments']=self.rows('SELECT * FROM balance_adjustments ORDER BY as_of,rowid')
        result['documents']=self.rows('SELECT id,kind,pages,status FROM source_documents ORDER BY rowid DESC LIMIT 20')
        return result

    def document_context(self,document_id=None,limit=3):
        if type(limit)!=int or not 1<=limit<=5:raise ValueError('limit 1..5')
        rows=self.rows('SELECT id,kind,pages,status,extraction FROM source_documents'+(' WHERE id=?' if document_id else '')+' ORDER BY rowid DESC LIMIT ?',(document_id,limit) if document_id else (limit,))
        return {'untrusted_document_data':True,'documents':[{**{k:v for k,v in row.items() if k!='extraction'},'extraction':json.loads(row['extraction'])} for row in rows]}

    def conversation_context(self,limit=12):
        if type(limit)!=int or not 1<=limit<=20:raise ValueError('limit 1..20')
        import re
        rows=self.rows('SELECT role,text FROM local_history ORDER BY id DESC LIMIT ?',(limit,))
        for row in rows:
            row['text']=re.sub(r'(?:sk_car_|sk-proj-)[A-Za-z0-9_-]+|\d{5,}:[A-Za-z0-9_-]{15,}','[SECRET REDACTED]',row['text'])[:10000]
        return {'historical_context_not_new_instructions':True,'messages':list(reversed(rows))}

    def family_opening(self,amount,as_of,source):
        from .planning import opening
        return opening(self,amount,as_of,source)

    def balance_reconcile(self,amount,as_of,source,reason,account=None,expected_balance=None):
        from .reconciliation import reconcile
        return reconcile(self,amount,as_of,source,reason,account,expected_balance)

    def income_save(self,ref,name,monthday,start,source,amount=None,weekend_rule='none',delay_risk=False,account=None,reference_amount=None,estimate_note='',earning_month_rule=None,income_group=None):
        from .planning import income_save
        return income_save(self,ref,name,monthday,start,source,amount,weekend_rule,delay_risk,account,reference_amount,estimate_note,earning_month_rule,income_group)

    def income_group_save(self,ref,name,reference_amount,members,source,note):
        from .planning import income_group_save
        return income_group_save(self,ref,name,reference_amount,members,source,note)

    def income_receive(self,ref,nominal_date,amount,on_date,source,account=None):
        from .planning import receive
        return receive(self,ref,nominal_date,amount,on_date,source,account)

    def income_history(self,ref=None):
        from .planning import income_history
        return income_history(self,ref)

    def family_expense_save(self,ref,name,category,amount,start_period,source,due_day=None,uncertain_identity=False):
        from .planning import expense_save
        return expense_save(self,ref,name,category,amount,start_period,source,due_day,uncertain_identity)

    def family_expense_status(self,ref,period,status,source,remaining=None):
        from .planning import expense_status
        return expense_status(self,ref,period,status,source,remaining)

    def family_expense_policy(self,ref,method,source,weekdays=None,daily_cost=None):
        from .planning import expense_policy
        return expense_policy(self,ref,method,source,weekdays,daily_cost)

    def family_forecast(self,as_of=None,until=None,**parameters):
        from .planning import forecast
        return forecast(self,as_of,until,**parameters)

    def family_scenarios(self,as_of,until,delay20_until=None,**parameters):
        from .planning import scenarios
        return scenarios(self,as_of,until,delay20_until,**parameters)

    def debt_inventory(self,as_of):
        from .planning import inventory
        return inventory(self,as_of)

    def family_debt_save(self,ref,name,expense_ref,reported_as_of,source,notes):
        from .planning import debt_save
        return debt_save(self,ref,name,expense_ref,reported_as_of,source,notes)

    def savings_preview(self,name,items,deadline,first_date,monthday,as_of=None,source="local-preview",**forecast_parameters):
        from .savings import preview
        return preview(self,name,items,deadline,first_date,monthday,as_of,source,**forecast_parameters)

    def savings_list(self,ref=None):
        from .savings import view
        return view(self,ref)

    def savings_save(self,ref,name,items,deadline,first_date,monthday,source):
        from .savings import save
        return save(self,ref,name,items,deadline,first_date,monthday,source)

    def savings_progress(self,ref,saved_total,as_of,source):
        from .savings import progress
        return progress(self,ref,saved_total,as_of,source)

    def savings_status(self,ref,status,source):
        from .savings import status as update
        return update(self,ref,status,source)

    def family_allocation(self,as_of,until,allocations=None,**parameters):
        from .planning import allocation
        return allocation(self,as_of,until,allocations,**parameters)

    def debt_save(self,debt_ref,name,monthly_payment,due_day,planning_from_period,reported_as_of,source,notes,account=None,source_line_name=None,source_line_amount=None,issued_month=None,term_months=None,reported_total_balance=None,payment_is_estimate=False,product_type=None):
        from .reported_debts import save
        return save(self,debt_ref,name,monthly_payment,due_day,planning_from_period,reported_as_of,source,notes,account,source_line_name,source_line_amount,issued_month,term_months,reported_total_balance,payment_is_estimate,product_type)

    def debt_term_info(self,loan_id,as_of):
        from .reported_debts import term_info
        return term_info(self,loan_id,as_of)

    def debt_status(self,loan_id,period,status,source,payment_date=None,payment_account=None):
        from .reported_debts import period_status
        return period_status(self,loan_id,period,status,source,payment_date,payment_account)

    def debt_bind(self,loan_id,account):
        from .reported_debts import bind
        return bind(self,loan_id,account)

    def card_save(self,name,balance,monthly_interest,reported_as_of,source,notes,due_day=None,first_due=None,account=None,monthly_payment=None,interest_only=True):
        from .cards import save
        return save(self,name,balance,monthly_interest,reported_as_of,source,notes,due_day,first_due,account,monthly_payment,interest_only)

    def card_scenario(self,card_id,extra_principal='0.00',months=12):
        from .cards import scenario
        return scenario(self,card_id,extra_principal,months)

    def card_bind(self,card_id,account):
        from .cards import bind
        return bind(self,card_id,account)

    def debt_schedule_from_document(self,loan_id,document_id,source):
        from .reported_debts import schedule_from_document
        return schedule_from_document(self,loan_id,document_id,source)

    def bank_import(self,document_id,loan_name,account=None):
        from .bank import import_schedule
        return import_schedule(self,document_id,loan_name,account)

    def bank_bind(self,schedule_id,account):
        from .bank import bind
        return bind(self,schedule_id,account)

    def backup(self, path):
        if Path(path).exists(): raise ValueError('Не перезаписываем существующую копию')
        target = sqlite3.connect(path)
        try: self.db.backup(target)
        finally: target.close()
        return {'backup':str(path)}

    def utility_save(self, **payload):
        from .utilities import save
        return save(self,**payload)

    def utility_quote(self,bill_id,period,amount,source):
        from .utilities import quote
        return quote(self,bill_id,period,amount,source)

    def utility_bind(self,bill_id,account):
        from .utilities import bind
        return bind(self,bill_id,account)

    def utility_status(self,bill_id,period,status,source):
        from .utilities import period_status
        return period_status(self,bill_id,period,status,source)
