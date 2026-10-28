import unittest
from local_finance.assistant import Ledger, Assistant
from local_finance.changes import apply_change
from finance.delivery import deliver


class SavingsTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:')
        Assistant(self.l,None)
        self.l.account('Общий','100000','2026-10-01')
        self.args=dict(name='Подарки',items=[{'name':'Подарок','recipient':'Жена','price':'10000.01'}],
                       deadline='2026-12-25',first_date='2026-10-20',monthday=20)

    def tearDown(self):self.l.db.close()

    def plan(self):
        return self.l.savings_preview(**self.args,as_of='2026-10-20',source='calculation')

    def save(self):
        self.plan()
        return self.l.savings_save(ref='gifts',source='agreement',**self.args)

    def test_rounding_preview_no_cash_or_budget_commit(self):
        before=self.l.family_forecast('2026-10-20','2026-12-25')
        p=self.plan()
        self.assertEqual([i['amount'] for i in p['plan']['installments']],[333334,333334,333333])
        self.assertEqual(self.l.savings_list()['goals'],[])
        self.assertFalse(p['affordable']) # unknown reserve/income, not an affordability promise
        self.assertEqual(before,self.l.family_forecast('2026-10-20','2026-12-25'))
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_adoption_requires_previous_calculation_and_new_owner_message(self):
        with self.assertRaises(ValueError):self.l.savings_save(ref='gifts',source='agreement',**self.args)
        self.plan()
        with self.assertRaises(ValueError):self.l.savings_save(ref='gifts',source='owner-change:calculation:hash',**self.args)
        result=apply_change(self.l,412850740,'agreement','savings_save',
                            dict(ref='gifts',**self.args),'Да, добавь план','Да, добавь план')
        self.assertTrue(result['applied'])
        self.assertTrue(apply_change(self.l,412850740,'agreement','savings_save',
                            dict(ref='gifts',**self.args),'Да, добавь план','Да, добавь план')['duplicate'])

    def test_saved_is_not_an_expense_and_not_counted_twice(self):
        self.save()
        before=self.l.family_forecast('2026-10-20','2026-12-25')
        self.l.savings_progress('gifts','3333.34','2026-10-20','receipt')
        after=self.l.family_forecast('2026-10-20','2026-12-25')
        self.assertEqual(before['minimum_conservative'],after['minimum_conservative'])
        self.assertEqual(after['actual_balance'],10000000)
        self.assertEqual(after['days'][-1]['known_flow_balance'],10000000)
        self.assertEqual(after['days'][-1]['conservative_allocated_balance'],8999999)
        self.assertTrue(self.l.savings_progress('gifts','3333.34','2026-10-20','receipt')['duplicate'])
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_reminders_once_weekly_overdue_and_cancel(self):
        self.save();sent=[]
        sender=lambda text:sent.append(text) or len(sent)
        deliver(self.l,'2026-10-19','2026-12-31',sender)
        deliver(self.l,'2026-10-19','2026-12-31',sender)
        self.assertEqual(len(sent),1)
        deliver(self.l,'2026-10-27','2026-12-31',sender)
        self.assertEqual(len(sent),2)
        self.l.savings_progress('gifts','3333.34','2026-10-20','receipt')
        self.assertEqual(self.l.queue_reminders('2026-10-27','2026-12-31'),[])
        self.l.savings_status('gifts','paused','pause')
        self.assertEqual(self.l.queue_reminders('2026-11-19','2026-12-31'),[])
        self.assertEqual(self.l.family_forecast('2026-10-20','2026-12-25')['minimum_conservative'],9666666)
        self.l.savings_status('gifts','released','release')
        self.assertEqual(self.l.family_forecast('2026-10-20','2026-12-25')['minimum_conservative'],10000000)

    def test_complete_keeps_earmark_and_stops_notifications(self):
        self.save()
        result=self.l.savings_progress('gifts','10000.01','2026-10-20','receipt')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(self.l.queue_reminders('2026-12-25','2026-12-31'),[])
        self.assertEqual(self.l.family_forecast('2026-10-20','2026-12-25')['minimum_conservative'],8999999)

    def test_short_horizon_and_bad_dates(self):
        p=self.l.savings_preview(**{**self.args,'deadline':'2026-10-20'},as_of='2026-10-20')
        self.assertEqual(len(p['plan']['installments']),1)
        with self.assertRaises(ValueError):self.l.savings_preview(**self.args,as_of='2026-10-21')

    def test_duplicate_identity_and_month_end_dates(self):
        self.save()
        with self.assertRaises(ValueError):
            self.l.savings_save(ref='another-ref',source='another-message',**self.args)
        p=self.l.savings_preview(**{**self.args,'first_date':'2026-10-31',
                                    'deadline':'2027-02-28','monthday':31},as_of='2026-10-20')
        self.assertEqual([r['date'] for r in p['plan']['installments']],
                         ['2026-10-31','2026-11-30','2026-12-31','2027-01-31','2027-02-28'])


if __name__=='__main__':unittest.main()
