"""Aggregate family cash planning. Expectations never post money or reduce debt."""
import json
import re
import calendar
from datetime import date, datetime, timedelta, timezone
from .core import day, money, rub, month_day, next_month


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS family_balance_reports(source TEXT PRIMARY KEY,as_of TEXT NOT NULL,amount INTEGER NOT NULL,account TEXT NOT NULL REFERENCES accounts(id),semantics TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_incomes(ref TEXT PRIMARY KEY,name TEXT NOT NULL,amount INTEGER,monthday INTEGER NOT NULL,start TEXT NOT NULL,weekend_rule TEXT NOT NULL,delay_risk INTEGER NOT NULL,account TEXT REFERENCES accounts(id),source TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_income_reports(source TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_income_profiles(ref TEXT PRIMARY KEY REFERENCES family_incomes(ref),reference_amount INTEGER,estimate_note TEXT NOT NULL,earning_month_rule TEXT,income_group TEXT);
    CREATE TABLE IF NOT EXISTS family_income_groups(ref TEXT PRIMARY KEY,name TEXT NOT NULL,reference_amount INTEGER NOT NULL,members TEXT NOT NULL,source TEXT NOT NULL,note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_income_receipts(source TEXT PRIMARY KEY,ref TEXT NOT NULL,nominal_date TEXT NOT NULL,actual_date TEXT NOT NULL,amount INTEGER NOT NULL,earning_month TEXT,operation_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_expenses(ref TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT NOT NULL,amount INTEGER,start_period TEXT NOT NULL,due_day INTEGER,uncertain_identity INTEGER NOT NULL,source TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_expense_periods(ref TEXT NOT NULL REFERENCES family_expenses(ref),period TEXT NOT NULL,status TEXT NOT NULL,remaining INTEGER,source TEXT NOT NULL,PRIMARY KEY(ref,period));
    CREATE TABLE IF NOT EXISTS family_expense_receipts(operation_id TEXT PRIMARY KEY REFERENCES operations(id),ref TEXT NOT NULL REFERENCES family_expenses(ref),period TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_expense_reports(source TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_expense_policies(ref TEXT PRIMARY KEY REFERENCES family_expenses(ref),method TEXT NOT NULL,source TEXT NOT NULL,parameters TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS family_debts(ref TEXT PRIMARY KEY,name TEXT NOT NULL,expense_ref TEXT NOT NULL UNIQUE REFERENCES family_expenses(ref),reported_as_of TEXT NOT NULL,source TEXT NOT NULL,notes TEXT NOT NULL);
    ''')
    if 'cutoff_rowid' not in {row[1] for row in db.execute('PRAGMA table_info(family_expense_periods)')}:
        db.execute('ALTER TABLE family_expense_periods ADD COLUMN cutoff_rowid INTEGER NOT NULL DEFAULT 0');db.commit()


def opening(ledger, amount, as_of, source):
    amount=money(amount);as_of=day(as_of)
    if amount<0 or not source:raise ValueError('Общий доступный остаток неотрицателен; нужен источник')
    old=ledger.rows('SELECT * FROM family_balance_reports WHERE source=?',(source,))
    if old:
        if old[0]['amount']!=amount or old[0]['as_of']!=as_of:raise ValueError('Источник уже использован для другого остатка')
        return {'account':old[0]['account'],'duplicate':True}
    if ledger.rows('SELECT id FROM accounts'):raise ValueError('Счета уже существуют: нельзя прибавить к ним общий остаток как ещё один счёт')
    with ledger.transaction():
        account=ledger.account('Общий доступный остаток — без распределения по банкам',rub(amount),as_of)['id']
        ledger.db.execute('INSERT INTO family_balance_reports VALUES(?,?,?,?,?)',(source,as_of,amount,account,'reported_available_on_date; not historical cash posting; bank allocation unknown'))
        ledger.audit('family_opening',{'source':source,'as_of':as_of,'amount':amount,'aggregate_only':True})
    return {'account':account,'duplicate':False,'cash_posting_created':False,'semantics':'Общий сообщённый доступный остаток на дату; распределение по банкам неизвестно'}


def income_save(ledger, ref, name, monthday, start, source, amount=None, weekend_rule='none', delay_risk=False, account=None,reference_amount=None,estimate_note='',earning_month_rule=None,income_group=None):
    start=day(start)
    if not ref or not source or type(monthday)!=int or not 1<=monthday<=31 or weekend_rule not in ('none','previous_friday'):raise ValueError('Нужны ref, источник, день 1..31 и правило none/previous_friday')
    value=None if amount is None else money(amount)
    if value is not None and value<=0:raise ValueError('Доход положителен или неизвестен')
    if type(delay_risk)!=bool:raise ValueError('delay_risk должен быть boolean')
    if account and not ledger.rows('SELECT id FROM accounts WHERE id=?',(account,)):raise ValueError('Счёт не найден')
    reference=None if reference_amount is None else money(reference_amount)
    if reference is not None and reference<=0 or earning_month_rule not in (None,'previous_completed_month'):raise ValueError('Некорректный ориентир или расчётный месяц')
    payload=dict(ref=ref,name=name,monthday=monthday,start=start,source=source,amount=value,weekend_rule=weekend_rule,delay_risk=delay_risk,account=account,reference_amount=reference,estimate_note=estimate_note,earning_month_rule=earning_month_rule,income_group=income_group)
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True)
    old=ledger.rows('SELECT payload FROM family_income_reports WHERE source=?',(source,))
    if old:
        if old[0]['payload']!=encoded:raise ValueError('Источник уже содержит другое сообщение о доходе')
        return {'ref':ref,'duplicate':True}
    with ledger.transaction():
        ledger.db.execute('INSERT INTO family_income_reports VALUES(?,?)',(source,encoded))
        ledger.db.execute('INSERT INTO family_incomes VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET name=excluded.name,amount=excluded.amount,monthday=excluded.monthday,start=excluded.start,weekend_rule=excluded.weekend_rule,delay_risk=excluded.delay_risk,account=excluded.account,source=excluded.source',(ref,name,value,monthday,start,weekend_rule,int(delay_risk),account,source))
        ledger.db.execute('INSERT INTO family_income_profiles VALUES(?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET reference_amount=excluded.reference_amount,estimate_note=excluded.estimate_note,earning_month_rule=excluded.earning_month_rule,income_group=excluded.income_group',(ref,reference,estimate_note,earning_month_rule,income_group))
        ledger.audit('family_income_save',payload)
    return {'ref':ref,'duplicate':False,'cash_posting_created':False}


def income_group_save(ledger,ref,name,reference_amount,members,source,note):
    value=money(reference_amount)
    if value<=0 or not source or not isinstance(members,list) or len(set(members))!=len(members) or not members:raise ValueError('Нужны источник, положительный ориентир и разные участники группы')
    streams={r['ref'] for r in ledger.rows('SELECT ref FROM family_incomes')}
    if set(members)-streams:raise ValueError('Участник группы не найден')
    with ledger.transaction():
        ledger.db.execute('INSERT INTO family_income_groups VALUES(?,?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET name=excluded.name,reference_amount=excluded.reference_amount,members=excluded.members,source=excluded.source,note=excluded.note',(ref,name,value,json.dumps(members),source,note))
        ledger.audit('income_group_save',{'ref':ref,'source':source,'reference_amount':value,'members':members,'not_per_receipt':True})
    return {'ref':ref,'cash_posting_created':False,'split_known':False}


def receive(ledger,ref,nominal_date,amount,on_date,source,account=None):
    nominal_date=day(nominal_date);on_date=day(on_date);value=money(amount)
    if value<=0 or not source:raise ValueError('Нужны положительная фактическая сумма и стабильный источник')
    streams=ledger.rows('SELECT * FROM family_incomes WHERE ref=?',(ref,))
    if not streams:raise ValueError('Ожидаемый доход не найден')
    stream=streams[0]
    if int(nominal_date[-2:])!=min(stream['monthday'],month_day(int(nominal_date[:4]),int(nominal_date[5:7]),31).day):raise ValueError('Дата не соответствует номинальному дню зарплаты')
    if nominal_date<stream['start']:raise ValueError('Ожидание раньше начала сохранённого плана')
    account=account or stream['account']
    payload=json.dumps(dict(ref=ref,nominal_date=nominal_date,amount=value,on_date=on_date,account=account),sort_keys=True)
    old=ledger.rows('SELECT * FROM family_income_receipts WHERE source=?',(source,))
    if old:
        if old[0]['payload']!=payload:raise ValueError('Этот источник уже записан с другими значениями')
        if ledger.rows('SELECT id FROM operations WHERE id=? AND void=1',(old[0]['operation_id'],)):raise ValueError('Поступление отменено; исправление требует нового источника')
        return {'operation_id':old[0]['operation_id'],'duplicate':True}
    profiles=ledger.rows('SELECT * FROM family_income_profiles WHERE ref=?',(ref,));profile=profiles[0] if profiles else {}
    earned=(date.fromisoformat(nominal_date).replace(day=1)-timedelta(days=1)).strftime('%Y-%m') if profile.get('earning_month_rule')=='previous_completed_month' else None
    with ledger.transaction():
        draft=ledger.stage({'kind':'income','amount':rub(value),'date':on_date,'account':account,'note':stream['name'],'occurrence':'income:'+ref+':'+nominal_date,'ambiguities':[]},source)
        result=ledger.confirm(draft['id'],True)
        if result.get('possible_duplicate'):raise ValueError('Похожее фактическое поступление уже есть; нужна проверка владельца')
        operation=ledger.rows('SELECT * FROM operations WHERE id=?',(result['id'],))[0]
        if operation['kind']!='income' or operation['amount']!=value or operation['on_date']!=on_date or operation['account']!=account or operation['occurrence']!='income:'+ref+':'+nominal_date or operation['void']:
            raise ValueError('Источник уже связан с другой денежной операцией')
        ledger.db.execute('INSERT INTO family_income_receipts VALUES(?,?,?,?,?,?,?,?)',(source,ref,nominal_date,on_date,value,earned,result['id'],payload))
        ledger.audit('income_received',{'ref':ref,'nominal_date':nominal_date,'actual_date':on_date,'earning_month':earned,'source':source,'operation_id':result['id']})
    return {'operation_id':result['id'],'duplicate':False,'earning_month':earned,'balance_recalculated':True,'actual_balance':sum(ledger.balances(on_date).values()),'next':'family_forecast with actual on_date'}


def income_history(ledger,ref=None):
    records=ledger.rows('SELECT r.*,o.void FROM family_income_receipts r JOIN operations o ON o.id=r.operation_id'+(' WHERE r.ref=?' if ref else '')+' ORDER BY r.actual_date,r.source',(ref,) if ref else ())
    for row in records:row.pop('payload',None)
    return {'unit':'kopecks','receipts':records,'earning_month_rule':'salary20 is previous completed nominal month','working_day_rate':None,'working_day_counts':None,'calendar_country':None,'formula_established':False,'needed_for_day_based_formula':['actual receipts across completed months','payroll working-day definition and calendar','daily rate or employer calculation rule']}


def income_events(ledger,start,end):
    result=[];paid={r['occurrence'] for r in ledger.rows('SELECT occurrence FROM operations WHERE void=0 AND occurrence IS NOT NULL')}
    for stream in ledger.rows('SELECT * FROM family_incomes'):
        profiles=ledger.rows('SELECT * FROM family_income_profiles WHERE ref=?',(stream['ref'],));profile=profiles[0] if profiles else {}
        cursor=date.fromisoformat(start).replace(day=1)
        while cursor.isoformat()<=end:
            nominal=month_day(cursor.year,cursor.month,stream['monthday']);when=nominal
            if stream['weekend_rule']=='previous_friday' and when.weekday()>=5:when-=timedelta(days=when.weekday()-4)
            occurrence='income:'+stream['ref']+':'+nominal.isoformat()
            if stream['start']<=when.isoformat() and start<=when.isoformat()<=end and occurrence not in paid:
                earned=(nominal.replace(day=1)-timedelta(days=1)).strftime('%Y-%m') if profile.get('earning_month_rule')=='previous_completed_month' else None
                result.append({**stream,**profile,'kind':'income','date':when.isoformat(),'nominal_date':nominal.isoformat(),'earning_month':earned,'occurrence':occurrence,'lead_days':1,'status':'expected','cash_posting_created':False})
            cursor=next_month(cursor)
    return result


def expense_save(ledger,ref,name,category,amount,start_period,source,due_day=None,uncertain_identity=False):
    date.fromisoformat(start_period+'-01');value=None if amount is None else money(amount)
    if not ref or not source or (value is not None and value<0) or (due_day is not None and (type(due_day)!=int or not 1<=due_day<=31)):raise ValueError('Некорректный расход семейного бюджета')
    if type(uncertain_identity)!=bool:raise ValueError('uncertain_identity должен быть boolean')
    encoded=json.dumps(dict(ref=ref,name=name,category=category,amount=value,start_period=start_period,due_day=due_day,uncertain_identity=uncertain_identity),ensure_ascii=False,sort_keys=True)
    old=ledger.rows('SELECT payload FROM family_expense_reports WHERE source=?',(source,))
    if old:
        if old[0]['payload']!=encoded:raise ValueError('Источник уже описывает другой расход')
        return {'ref':ref,'duplicate':True,'cash_posting_created':False}
    with ledger.transaction():
        ledger.db.execute('INSERT INTO family_expense_reports VALUES(?,?)',(source,encoded))
        ledger.db.execute('INSERT INTO family_expenses VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET name=excluded.name,category=excluded.category,amount=excluded.amount,start_period=excluded.start_period,due_day=excluded.due_day,uncertain_identity=excluded.uncertain_identity,source=excluded.source',(ref,name,category,value,start_period,due_day,int(uncertain_identity),source))
        ledger.audit('family_expense_save',{'ref':ref,'source':source})
    return {'ref':ref,'cash_posting_created':False}


def expense_status(ledger,ref,period,status,source,remaining=None):
    date.fromisoformat(period+'-01')
    if not ledger.rows('SELECT ref FROM family_expenses WHERE ref=?',(ref,)):raise ValueError('Расход не найден')
    if status not in ('unknown','unpaid','external_paid','remaining'):raise ValueError('Статус unknown/unpaid/external_paid/remaining')
    value=None if remaining is None else money(remaining)
    if (status=='remaining')!=(value is not None) or value is not None and value<0:raise ValueError('remaining нужен только для статуса remaining и неотрицателен')
    if not source:raise ValueError('Нужен источник')
    previous=ledger.rows('SELECT * FROM family_expense_periods WHERE ref=? AND period=?',(ref,period))
    if previous and previous[0]['source']==source:
        if previous[0]['status']!=status or previous[0]['remaining']!=value:raise ValueError('Источник уже содержит другую оценку периода')
        return {'ref':ref,'period':period,'status':status,'duplicate':True,'cash_posting_created':False}
    with ledger.transaction():
        cutoff=ledger.db.execute('SELECT COALESCE(MAX(rowid),0) FROM operations').fetchone()[0]
        ledger.db.execute('INSERT INTO family_expense_periods(ref,period,status,remaining,source,cutoff_rowid) VALUES(?,?,?,?,?,?) ON CONFLICT(ref,period) DO UPDATE SET status=excluded.status,remaining=excluded.remaining,source=excluded.source,cutoff_rowid=excluded.cutoff_rowid',(ref,period,status,value,source,cutoff))
        ledger.audit('family_expense_status',{'ref':ref,'period':period,'status':status,'source':source})
    return {'ref':ref,'period':period,'status':status,'cash_posting_created':False}


def expense_policy(ledger,ref,method,source,weekdays=None,daily_cost=None):
    """Explicit classification only: never infer a spending pattern from a name."""
    rows=ledger.rows('SELECT * FROM family_expenses WHERE ref=?',(ref,))
    if not rows or not source:raise ValueError('Нужны существующая статья и источник классификации')
    if method not in ('calendar_daily','weekly_travel','monthly_only'):raise ValueError('Неизвестный метод оценки')
    if method!='monthly_only' and (rows[0]['due_day'] is not None or rows[0]['uncertain_identity']):
        raise ValueError('Обязательство с датой или неизвестной идентичностью нельзя делить по дням')
    parameters={}
    if method=='weekly_travel':
        if not isinstance(weekdays,list) or not weekdays or any(type(v)!=int or v not in range(7) for v in weekdays):raise ValueError('Дни недели 0..6')
        value=money(daily_cost)
        if value<=0:raise ValueError('Стоимость дня должна быть положительной')
        parameters={'weekdays':sorted(set(weekdays)),'daily_cost':value}
    elif weekdays is not None or daily_cost is not None:raise ValueError('График нужен только для weekly_travel')
    encoded=json.dumps(parameters,sort_keys=True)
    previous=ledger.rows('SELECT * FROM family_expense_policies WHERE ref=?',(ref,))
    if previous and previous[0]['method']==method and previous[0]['source']==source and previous[0]['parameters']==encoded:
        return {'ref':ref,'method':method,'duplicate':True,'cash_posting_created':False}
    with ledger.transaction():
        ledger.db.execute('INSERT INTO family_expense_policies VALUES(?,?,?,?) ON CONFLICT(ref) DO UPDATE SET method=excluded.method,source=excluded.source,parameters=excluded.parameters',(ref,method,source,encoded))
        ledger.audit('family_expense_policy',{'ref':ref,'method':method,'source':source})
    return {'ref':ref,'method':method,'cash_posting_created':False}


def remaining_estimate(ledger,expense,start,end,policy):
    """A read-only demand scenario, not a decreasing cash/budget ledger."""
    if not policy or policy['method']=='monthly_only' or expense['amount'] is None:return None
    first=date.fromisoformat(start);last=date(first.year,first.month,calendar.monthrange(first.year,first.month)[1])
    through=min(last,date.fromisoformat(end));days=(through-first).days+1
    parameters=json.loads(policy['parameters']);travel_dates=[]
    if policy['method']=='weekly_travel':
        travel_dates=[(first+timedelta(days=i)).isoformat() for i in range(days) if (first+timedelta(days=i)).weekday() in parameters['weekdays']]
        gross=len(travel_dates)*parameters['daily_cost'];formula=f"{len(travel_dates)} travel days * {parameters['daily_cost']} kopecks/day"
    else:
        # Integer half-up rounding, once at the end, with actual month length.
        denominator=last.day;gross=(expense['amount']*days*2+denominator)//(denominator*2)
        formula=f"{expense['amount']} kopecks * {days} calendar days / {denominator}, rounded half up"
    # Past spending is NOT subtracted from a remaining-days projection. Only
    # overlapping recorded demand is removed; future cash postings are added once
    # by forecast(), same-day postings are already in actual_balance.
    overlap=ledger.db.execute('SELECT COALESCE(SUM(o.amount),0) FROM family_expense_receipts r JOIN operations o ON o.id=r.operation_id WHERE r.ref=? AND r.period=? AND o.void=0 AND o.on_date>=? AND o.on_date<=?',(expense['ref'],start[:7],start,through.isoformat())).fetchone()[0]
    return {'amount':max(0,gross-overlap),'source':policy['source'],'method':policy['method'],'confidence':'estimated','calculated_as_of':start,'through':through.isoformat(),'includes_current_day':True,'days_in_month':last.day,'remaining_calendar_days':days,'formula':formula,'gross_estimate':gross,'overlapping_linked_facts':overlap,'travel_dates':travel_dates,'precision':'kopeck, rounded half up; planning estimate','limitations':['Uniform daily demand is an assumption' if policy['method']=='calendar_daily' else 'Assumes stated work trips and fare continue; holidays/absences not inferred','No past spending inferred; no cash posting','Unlinked purchases cannot be reconciled automatically'],'expires_with_period':True}


def expense_events(ledger,start,end):
    result=[];paid={r['occurrence'] for r in ledger.rows('SELECT occurrence FROM operations WHERE void=0 AND occurrence IS NOT NULL')}
    pending=ledger.rows("SELECT MIN(period) period FROM family_expense_periods WHERE status IN ('unpaid','remaining')")[0]['period']
    first=min(start[:7],pending) if pending else start[:7]
    cursor=date.fromisoformat(first+'-01')
    while cursor.isoformat()<=end:
        period=cursor.strftime('%Y-%m')
        for expense in ledger.rows('SELECT * FROM family_expenses WHERE start_period<=?',(period,)):
            records=ledger.rows('SELECT * FROM family_expense_periods WHERE ref=? AND period=?',(expense['ref'],period));record=records[0] if records else {}
            provenance={}
            if record.get('source','').startswith('{'):
                try:provenance=json.loads(record['source'])
                except ValueError:pass
                if not isinstance(provenance,dict):provenance={}
            if provenance.get('reported_as_of','')>start:
                record={};provenance={}
            status=record.get('status','unknown' if period==start[:7] and start[-2:]!='01' else 'planned')
            if period<start[:7] and provenance.get('expires_with_period'):continue
            if period<start[:7] and status not in ('unpaid','remaining'):continue
            when=month_day(cursor.year,cursor.month,expense['due_day']).isoformat() if expense['due_day'] else max(start,cursor.isoformat())
            occurrence='family-expense:'+expense['ref']+':'+period+'-01'
            if status=='external_paid' or occurrence in paid:continue
            if when<start and status not in ('unknown','unpaid','remaining'):continue
            if when>end:continue
            value=record.get('remaining') if status=='remaining' else expense['amount']
            cutoff=record.get('cutoff_rowid',0) if status=='remaining' else 0
            spent=ledger.db.execute('SELECT COALESCE(SUM(o.amount),0) FROM family_expense_receipts r JOIN operations o ON o.id=r.operation_id WHERE r.ref=? AND r.period=? AND o.void=0 AND o.rowid>? AND o.on_date<=?',(expense['ref'],period,cutoff,end)).fetchone()[0]
            if value is not None:value=max(0,value-spent)
            estimate=None
            # Explicit remaining/execution always wins. Future full months use
            # the monthly plan, not today's partial-month ratio.
            if not record and period==start[:7] and start[-2:]!='01' and expense['due_day'] is None and not expense['uncertain_identity']:
                policies=ledger.rows('SELECT * FROM family_expense_policies WHERE ref=?',(expense['ref'],))
                estimate=remaining_estimate(ledger,expense,start,end,policies[0] if policies else None)
                if estimate:value=estimate['amount'];status='estimated_remaining'
            if not estimate and provenance.get('confidence')=='estimated':
                estimate={'source':provenance.get('source',record.get('source')),'confidence':'estimated','calculated_as_of':start,'reported_as_of':provenance.get('reported_as_of'),'formula':f"Reported remaining {record.get('remaining')} kopecks - {spent} linked subsequent confirmed kopecks, floor zero",'precision':'Owner estimate, ledger arithmetic in kopecks','limitations':['Does not decrease just because a day passed','Unlinked purchases cannot be reconciled automatically'],'expires_with_period':provenance.get('expires_with_period',False)}
            result.append({**expense,'date':max(start,when),'original_date':when,'kind':'expense','amount':value,'linked_actual_spend':spent,'status':status,'period_status':status,'unpaid_confirmed':status=='unpaid','occurrence':occurrence,'undated':expense['due_day'] is None,'budget_hold':expense['due_day'] is None,'lead_days':1,'amount_is_estimate':bool(estimate) or provenance.get('confidence')=='estimated','remaining_provenance':provenance or None,'estimate':estimate,'amount_basis':'remaining_demand_scenario' if status=='estimated_remaining' else 'reported_remaining' if status=='remaining' else 'monthly_plan'})
        cursor=next_month(cursor)
    return result


def inventory(ledger,as_of):
    as_of=day(as_of);result=[]
    for row in ledger.rows('SELECT * FROM bank_schedules WHERE active=1'):
        upcoming=ledger.rows('SELECT on_date,principal,interest,balance,balance_before,page,row_ref FROM bank_payments WHERE schedule_id=? AND on_date>=? ORDER BY on_date LIMIT 1',(row['id'],as_of))
        docs=ledger.rows('SELECT extraction FROM source_documents WHERE id=?',(row['document_id'],));extraction=json.loads(docs[0]['extraction']) if docs else {}
        first_page=next((p.get('text','') for p in extraction.get('page_texts',[]) if p.get('page')==1),'')
        figures={}
        for field,label in [('principal','Сумма основного долга'),('total_debt','Общая задолженность')]:
            match=re.search(re.escape(label)+r'\s+([\d \u00a0\u202f]+[,\.]\d{2})\s*(?:RUR|RUB|руб)',first_page,re.I)
            if match:figures[field]=money(match.group(1))
        rate=re.search(r'Процентная ставка по кредиту\s+([\d.,]+)\s*%',first_page)
        if rate:figures['stated_annual_rate']=rate.group(1).replace(',','.')
        result.append({'ref':'bank:'+row['id'],'name':row['loan_name'],'type':'bank_schedule','current_principal':None,'reported_total_balance':None,'scheduled_principal_before':upcoming[0]['balance_before'] if upcoming else None,'schedule_evidence':upcoming[0] if upcoming else None,'source_document':row['document_id'],'document_figures':{'values':figures,'page':1 if figures else None,'statement_as_of':None,'current_payoff_verified':False},'annual_rate':None,'early_repayment_terms':None,'basis':'Imported payment schedule and document figures; statement date/current payoff not verified; future payments are not principal'})
    for row in ledger.rows('SELECT * FROM credit_cards WHERE active=1'):
        result.append({'ref':'card:'+row['id'],'name':row['name'],'type':'credit_card','current_principal':None,'reported_card_balance':row['balance'],'reported_as_of':row['reported_as_of'],'monthly_payment':row['monthly_payment'],'interest_only':row['interest_only'],'annual_rate':row['annual_rate'],'early_repayment_terms':None,'source':row['source'],'notes':row['notes'],'basis':'user_reported; read notes for stated rates, remaining installments and applicability; no inferred uniform APR or amortization'})
    for row in ledger.rows('SELECT * FROM reported_loans WHERE active=1'):
        result.append({'ref':'reported-loan:'+row['id'],'name':row['name'],'type':'reported_debt','current_principal':row['principal_balance'],'reported_total_balance':row['reported_total_balance'],'reported_as_of':row['reported_as_of'],'monthly_payment':row['monthly_payment'],'payment_is_estimate':bool(row['payment_is_estimate']),'annual_rate':row['annual_rate'],'early_repayment_terms':None,'source':row['source'],'notes':row['notes'],'basis':'user_reported; read notes for rate conditions and debt components; total debt and future payments are not principal'})
    for row in ledger.rows('SELECT d.*,e.amount,e.due_day,e.start_period FROM family_debts d JOIN family_expenses e ON e.ref=d.expense_ref'):
        result.append({'ref':'family-debt:'+row['ref'],'name':row['name'],'type':'separate_loan_pending_details','expense_ref':row['expense_ref'],'monthly_payment':row['amount'],'payment_is_estimate':True,'due_day':row['due_day'],'current_principal':None,'reported_total_balance':None,'annual_rate':None,'term_months':None,'payment_allocation':None,'early_repayment_terms':None,'reported_as_of':row['reported_as_of'],'source':row['source'],'notes':row['notes'],'basis':'Confirmed separate loan; original monthly-list amount is estimated payment, not debt balance; one linked expense envelope'})
    return {'as_of':as_of,'unit':'kopecks','debts':result,'total_current_principal':None,'complete_current_principal':False}


def debt_save(ledger,ref,name,expense_ref,reported_as_of,source,notes):
    reported_as_of=day(reported_as_of)
    expenses=ledger.rows('SELECT * FROM family_expenses WHERE ref=?',(expense_ref,))
    if not expenses or not ref or not source:raise ValueError('Нужны существующая исходная строка, идентичность долга и источник')
    expense=expenses[0]
    old=ledger.rows('SELECT * FROM family_debts WHERE ref=?',(ref,))
    if old:
        if old[0]['expense_ref']!=expense_ref:raise ValueError('Долг уже связан с другой строкой')
        if old[0]['source']==source:return {'ref':ref,'duplicate':True,'cash_posting_created':False}
    with ledger.transaction():
        # Reuse the existing envelope; identity is now known, payment date is not.
        expense_save(ledger,expense_ref,expense['name'],expense['category'],rub(expense['amount']) if expense['amount'] is not None else None,expense['start_period'],source+':resolved-expense',expense['due_day'],False)
        ledger.db.execute('INSERT INTO family_debts VALUES(?,?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET name=excluded.name,reported_as_of=excluded.reported_as_of,source=excluded.source,notes=excluded.notes',(ref,name,expense_ref,reported_as_of,source,notes))
        ledger.audit('family_debt_identity_confirmed',{'ref':ref,'expense_ref':expense_ref,'source':source,'monthly_amount_not_principal':True,'no_payment_date_assumed':True})
    return {'ref':ref,'duplicate':False,'cash_posting_created':False,'expense_ref':expense_ref,'payment_date_known':expense['due_day'] is not None}


def forecast(ledger,as_of=None,until=None,reserve=None,income_amounts=None,income_dates=None,expense_remaining=None,daily_budget=None,purchase=None):
    as_of=as_of or datetime.now(timezone(timedelta(hours=5))).date().isoformat()
    current=date.fromisoformat(as_of)
    until=until or date(current.year,current.month,calendar.monthrange(current.year,current.month)[1]).isoformat()
    as_of,until=day(as_of),day(until)
    if until<as_of or (date.fromisoformat(until)-date.fromisoformat(as_of)).days>3660:raise ValueError('Горизонт 0..3660 дней')
    income_amounts=income_amounts or {};income_dates=income_dates or {};expense_remaining=expense_remaining or {}
    settings={r['key']:json.loads(r['value']) for r in ledger.rows('SELECT * FROM settings')}
    reserve_value=money(reserve) if reserve is not None else settings.get('reserve')
    daily_value=money(daily_budget) if daily_budget is not None else settings.get('daily')
    if reserve_value is not None and reserve_value<0 or daily_value is not None and daily_value<0:raise ValueError('Резерв и дневной бюджет неотрицательны')
    accounts=ledger.rows('SELECT * FROM accounts');actual=sum(ledger.available_balances(as_of).values()) if accounts else None
    opening_dates={r['id']:r['as_of'] for r in accounts}
    # Native sources own their obligation identities; intake is not an extra list of payments.
    earliest=min([as_of[:7]+'-01']+[r['as_of'] for r in accounts])
    events=ledger.events(earliest,until)
    native=[];uncertain=[];income=[];scenario_parameters={'income_amounts':income_amounts,'income_dates':income_dates,'expense_remaining':expense_remaining,'daily_budget':daily_budget,'reserve':reserve}
    valid_refs={r['ref'] for r in ledger.rows('SELECT ref FROM family_incomes')}
    if set(income_amounts)-valid_refs:raise ValueError('Неизвестный ref дохода')
    valid_expenses={r['ref'] for r in ledger.rows('SELECT ref FROM family_expenses')}
    if set(expense_remaining)-valid_expenses:raise ValueError('Неизвестный ref расхода')
    # Date overrides may bring an expected event into this horizon from a prior month.
    income_start=min([as_of[:7]+'-01']+[key.rsplit(':',1)[-1][:7]+'-01' for key in income_dates])
    incoming=income_events(ledger,income_start,until)
    incoming_keys={e['occurrence'] for e in incoming}
    if set(income_dates)-incoming_keys:raise ValueError('Неизвестное или уже исполненное ожидание дохода')
    events=[e for e in events if not e['occurrence'].startswith('income:')]+incoming
    for event in events:
        e=dict(event);when=e['date']
        if e['kind']=='income':
            if e.get('ref') in income_amounts:
                e['amount']=money(income_amounts[e['ref']]);e['amount_basis']='scenario_override'
                if e['amount']<=0:raise ValueError('Сценарный доход должен быть положительным')
            if e['occurrence'] in income_dates:
                override=income_dates[e['occurrence']]
                if override is None:e['excluded_until_known']=True
                else:
                    override=day(override)
                    if override<when:raise ValueError('Сценарий задержки не может переносить доход раньше ожидания')
                    e['date']=override;e['date_basis']='scenario_override'
            if when<as_of and e['occurrence'] not in income_dates:continue
            if e['date']>until:continue
            income.append(e)
            if e['date']>=as_of and e.get('amount') is not None and not e.get('excluded_until_known'):native.append(e)
            continue
        if when<as_of and not (e.get('unpaid_confirmed') or e.get('period_status')=='unknown' or e.get('undated')):
            threshold=opening_dates.get(e.get('account'),min(opening_dates.values(),default=as_of))
            if when>=threshold:e['period_status']='unknown'
            else:continue
        e['original_date']=when;e['date']=max(when,as_of)
        if e.get('unpaid_confirmed'):
            e['status']='unpaid';e['overdue']=when<as_of;e['days_overdue']=max(0,(date.fromisoformat(as_of)-date.fromisoformat(when)).days);native.append(e)
        elif e.get('period_status')=='unknown' or when==as_of and not e.get('undated'):
            e['status']='unknown';uncertain.append(e)
        else:native.append(e)
    holds=[]
    for e in expense_events(ledger,as_of,until):
        if e['ref'] in expense_remaining and e['date'][:7]==as_of[:7]:
            e['amount']=money(expense_remaining[e['ref']]);e['status']='scenario_remaining'
            e['estimate']={'source':'explicit_forecast_parameter','confidence':'scenario','calculated_as_of':as_of,'formula':'Explicit owner what-if remaining amount; no additional subtraction','precision':'Explicit scenario amount in kopecks','limitations':['Scenario override, not an actual expense']};e['amount_is_estimate']=True;e['amount_basis']='scenario_remaining'
            if e['amount']<0:raise ValueError('Остаток расходов неотрицателен')
        if e['uncertain_identity']:
            e['reason']='Unresolved identity: not added to avoid a second representation of a debt';uncertain.append(e);continue
        if e['amount'] is None:uncertain.append(e);continue
        if e['status']=='unknown':
            uncertain.append(e);holds.append({**e,'amount_basis':'full_month_envelope_remaining_unknown','budget_hold':True})
        else:native.append(e)
    # Ledger cash postings are facts. A linked occurrence was already removed above.
    for op in ledger.rows('SELECT * FROM operations WHERE void=0 AND on_date>? AND on_date<=?',(as_of,until)):
        if op['kind']!='transfer':native.append({**op,'date':op['on_date'],'name':op['note'],'status':'recorded_future_fact'})
    if daily_value:
        if ledger.rows('SELECT ref FROM family_expenses WHERE category IN (?,?)',('Еда','Транспорт')):raise ValueError('Повседневный бюджет вместе с едой/транспортом требует явного разделения; иначе двойной счёт')
        cursor=date.fromisoformat(as_of)+timedelta(days=1)
        while cursor.isoformat()<=until:
            native.append({'date':cursor.isoformat(),'amount':daily_value,'kind':'expense','name':'Дополнительный дневной бюджет','status':'scenario'});cursor+=timedelta(days=1)
    if purchase:
        value=money(purchase['amount']);when=day(purchase['date'])
        if value<=0 or not as_of<=when<=until:raise ValueError('Покупка вне горизонта или неположительна')
        native.append({'date':when,'kind':'expense','amount':value,'name':'Сценарная покупка','status':'scenario'})
    known_unknown=[e for e in uncertain if not e.get('uncertain_identity') and e.get('amount') is not None and not e.get('ref')]
    # Both curves are allocations, not changes to actual balance. Unknown execution
    # is charged only in the explicitly labelled conservative allocation curve.
    from .savings import holds as savings_holds
    holds += savings_holds(ledger,as_of,until)
    allocation_events=native+holds+[{**e,'budget_hold':True,'amount_basis':'if_still_unpaid'} for e in known_unknown]
    rows=[];known=actual;allocation=actual;minimum=actual;reserve_floor=reserve_value or 0;gap=as_of if actual is not None and actual<reserve_floor else None
    cursor=date.fromisoformat(as_of)
    while cursor.isoformat()<=until:
        when=cursor.isoformat();today=sorted([e for e in allocation_events if e['date']==when],key=lambda e:e['kind']=='income')
        for e in today:
            sign=1 if e['kind']=='income' else -1
            if allocation is not None:allocation+=sign*e['amount'];minimum=min(minimum,allocation)
            if not e.get('budget_hold') and known is not None:known+=sign*e['amount']
            if gap is None and allocation is not None and allocation<reserve_floor:gap=when
        rows.append({'date':when,'known_flow_balance':known,'conservative_allocated_balance':allocation,'events':today})
        cursor+=timedelta(days=1)
    unknown_income=[e for e in income if e['amount'] is None]
    excluded_income=[e for e in income if e.get('excluded_until_known')]
    missing=[]
    if actual is None:missing.append('opening_balance')
    if reserve_value is None:missing.append('reserve')
    if not income:missing.append('expected_income_in_horizon')
    if unknown_income:missing.append('income_amounts')
    if uncertain:missing.append('expense_execution_or_timing_or_identity')
    if any(e.get('amount_is_estimate') for e in native):missing.append('estimated_obligation_amounts')
    if excluded_income:missing.append('delayed_income_date')
    if daily_value is None and not ledger.rows("SELECT ref FROM family_expenses WHERE category IN ('Еда','Транспорт')"):
        missing.append('basic_needs_budget')
    next_income=min((e['date'] for e in income if not e.get('excluded_until_known') and e['date']>=as_of),default=None)
    before_next=[r for r in rows if next_income is None or r['date']<next_income]
    actual_free=max(0,min(actual,minimum)-reserve_floor) if actual is not None and minimum is not None and not missing else 0
    groups=ledger.rows('SELECT * FROM family_income_groups')
    group_members={member for g in groups for member in json.loads(g['members'])}
    references=ledger.rows('SELECT p.*,i.name FROM family_income_profiles p JOIN family_incomes i ON i.ref=p.ref WHERE p.reference_amount IS NOT NULL')
    full_month_reference=sum(g['reference_amount'] for g in groups)+sum(p['reference_amount'] for p in references if p['ref'] not in group_members)
    months=[]
    for period in sorted({r['date'][:7] for r in rows}):
        period_days=[r for r in rows if r['date'].startswith(period)]
        period_events=[e for r in period_days for e in r['events']]
        months.append({'period':period,'modeled_inflows':sum(e['amount'] for e in period_events if e['kind']=='income'),'allocated_outflows_and_holds':sum(e['amount'] for e in period_events if e['kind']=='expense'),'ending_conservative_allocated_balance':period_days[-1]['conservative_allocated_balance'],'unpriced_expected_income_dates':[e['date'] for e in unknown_income if e['date'].startswith(period)],'actual_income_is_separate_from_reference':True})
    need_events=[e for e in allocation_events if e['kind']=='expense']
    result={'months':months,'timezone':'Asia/Yekaterinburg','current_day_included_in_demand_estimates':True,
            'remaining_needs':{'total_allocated':sum(e['amount'] for e in need_events),'is_actual_spending':False,
                               'estimates':[{'ref':e.get('ref'),'name':e['name'],'amount':e['amount'],'estimate':e.get('estimate'),'remaining_provenance':e.get('remaining_provenance')} for e in need_events if e.get('amount_is_estimate')],
                               'monthly_holds_with_unknown_execution':[e['ref'] for e in holds if 'ref' in e]},
            'clarification_policy':{'ask_for':['Material deficit before mandatory payments','Large unpriced obligation or conflicting facts that change the decision','No applicable safe estimate when needed for a concrete decision'],
                                    'do_not_ask_for':['Saved facts again','Each small category sequentially when an estimate or existing hold suffices'],
                                    'fixed_money_threshold':None,'estimates_are_not_actual_expenses':True}}
    return {**result,'unit':'kopecks','scope':'aggregate_family_not_bank_allocation','as_of':as_of,'until':until,'actual_balance':actual,'expected_income':income,'unknown_income':unknown_income,'uncertain_expenses':uncertain,'monthly_income_reference':{'full_nominal_month_estimate':full_month_reference or None,'groups':groups,'individual_references':references,'remaining_current_month':None,'guaranteed_minimum':None,'daily_split_known':not bool(groups),'not_added_to_cash_flow':True},'reserve':reserve_value,'daily_budget':daily_value,'missing':missing,'complete':not missing,'safe_to_spend':not missing and minimum is not None and minimum>=reserve_floor,'available_for_early_repayment':actual_free,'minimum_conservative':minimum,'first_gap':gap,'next_expected_income_date':next_income,'until_next_income':{'through':before_next[-1]['date'] if before_next else as_of,'conservative_balance':before_next[-1]['conservative_allocated_balance'] if before_next else actual},'days':rows,'scenario_parameters':scenario_parameters,'assumptions':['No expected income changes actual cash','Debits allocated before expected credits within each day','Unknown salary amounts contribute no numeric credit; not assumed zero salary','Undated full monthly envelopes are holds, not invented debit dates or confirmed remaining spend','Unknown execution shown separately; conservative curve assumes still payable','Reserve unknown is not an approved zero reserve; prepayment blocked','No historic unconfirmed payments fabricated; aggregate account does not specify bank allocation']}


def scenarios(ledger,as_of,until,delay20_until=None,**parameters):
    ontime=forecast(ledger,as_of,until,**parameters)
    candidates=[e for e in ontime['expected_income'] if e.get('ref')=='salary20']
    if not candidates:return {'on_time':ontime,'delayed':None,'reason':'No outstanding salary20 occurrence in horizon'}
    first=min(candidates,key=lambda e:e['date']);dates={**parameters.pop('income_dates',{}),first['occurrence']:delay20_until}
    delayed=forecast(ledger,as_of,until,income_dates=dates,**parameters)
    return {'on_time':ontime,'delayed':delayed,'delay_assumption':'Excluded until date is supplied' if delay20_until is None else 'Explicit scenario date, not promised latest arrival','delayed_occurrence':first['occurrence']}


def allocation(ledger,as_of,until,allocations=None,**parameters):
    base=forecast(ledger,as_of,until,**parameters);debts=inventory(ledger,as_of);allocations=allocations or {}
    refs={r['ref'] for r in debts['debts']}
    if set(allocations)-refs:raise ValueError('Неизвестный долг; используйте debt_inventory')
    values={k:money(v) for k,v in allocations.items()}
    if any(v<0 for v in values.values()):raise ValueError('Распределение неотрицательно')
    total=sum(values.values());available=base['available_for_early_repayment']
    return {'unit':'kopecks','cash_available':available,'requested_allocation':values,'requested_total':total,'within_cash_limit':total<=available,'allocation_status':'blocked_incomplete_budget' if base['missing'] else 'exceeds_free_cash' if total>available else 'cash_envelope_only','budget_missing':base['missing'],'debts':debts['debts'],'optimal_strategy':None,'interest_savings':None,'payoff_date':None,'cash_posting_created':False,'limitation':'Allocation only; rates, product-specific interest rules and bank prepayment terms must be supplied before optimizing. No fixed monthly formula applied to cards.'}
