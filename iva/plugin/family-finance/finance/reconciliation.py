"""Dated cash reconciliations, separate from income and expense postings."""
from .core import day, money, local_today


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS balance_adjustments(
        source TEXT PRIMARY KEY,account TEXT NOT NULL REFERENCES accounts(id),
        as_of TEXT NOT NULL,reported INTEGER NOT NULL,previous INTEGER NOT NULL,
        delta INTEGER NOT NULL,reason TEXT NOT NULL)''')
    db.commit()


def reconcile(ledger, amount, as_of, source, reason, account=None, expected_balance=None):
    """Set observed balance at this point; subsequent postings still change it."""
    amount=money(amount);as_of=day(as_of)
    if not isinstance(source,str) or not source.strip() or not isinstance(reason,str) or not reason.strip():
        raise ValueError('Нужны источник и причина корректировки остатка')
    if as_of>local_today():raise ValueError('Сверка фактического остатка не может быть в будущем')
    with ledger.transaction():
        old=ledger.rows('SELECT * FROM balance_adjustments WHERE source=?',(source,))
        if old:
            row=old[0]
            if row['reported']!=amount or row['as_of']!=as_of or account not in (None,row['account']) or row['reason']!=reason:
                raise ValueError('Источник уже использован для другой корректировки остатка')
            return {**row,'duplicate':True,'cash_posting_created':False}
        accounts=ledger.rows('SELECT * FROM accounts'+(' WHERE id=?' if account else ''),(account,) if account else ())
        if len(accounts)!=1:
            raise ValueError('Для сверки нужен один существующий счёт; при нескольких укажите account, не распределяйте общий остаток произвольно')
        selected=accounts[0];account=selected['id']
        if as_of<selected['as_of']:raise ValueError('Сверка раньше открытия счёта невозможна')
        if ledger.rows('SELECT source FROM balance_adjustments WHERE account=? AND as_of>?',(account,as_of)):
            raise ValueError('Уже есть более поздняя сверка этого счёта; уточните актуальный остаток на последнюю дату')
        previous=ledger.balances(as_of)[account]
        if expected_balance is not None and previous!=money(expected_balance):
            raise ValueError('Остаток изменился после чтения; перечитайте реестр перед сверкой')
        delta=amount-previous
        ledger.db.execute('INSERT INTO balance_adjustments VALUES(?,?,?,?,?,?,?)',
                          (source,account,as_of,amount,previous,delta,reason))
        result={'source':source,'account':account,'as_of':as_of,'reported':amount,
                'previous':previous,'delta':delta,'reason':reason,
                'duplicate':False,'cash_posting_created':False}
        ledger.audit('balance_reconcile',result)
        return result
