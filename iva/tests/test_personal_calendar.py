from datetime import datetime,timedelta
from pathlib import Path
import tempfile
import unittest
from local_finance.personal_calendar import Calendar,ZONE,iso

class API:
    def __init__(self,fail=False):self.sent=[];self.fail=fail
    def call(self,method,payload):
        self.sent.append(payload)
        if self.fail:raise RuntimeError('response lost')
        return {'message_id':len(self.sent)}

class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'calendar.sqlite3';self.c=Calendar(self.path)
        self.time=datetime(2026,10,19,10,tzinfo=ZONE);self.api=API()
    def tearDown(self):self.c.close();self.tmp.cleanup()
    def add(self,**extra):
        return self.c.execute({'action':'add','title':'Test task','at':iso(self.time+timedelta(minutes=1)),**extra},'owner:1',self.time)['event']
    def test_due_delivery_survives_restart_and_does_not_repeat(self):
        self.add(requires_ack=False);self.c.close();self.c=Calendar(self.path)
        self.c.tick(self.api,123,self.time+timedelta(minutes=2));self.c.tick(self.api,123,self.time+timedelta(minutes=3))
        self.assertEqual(len(self.api.sent),1)
        self.assertEqual(self.c.rows('SELECT state FROM personal_notices')[0]['state'],'sent')
    def test_birthday_next_year_notice_day_before_and_repeat(self):
        event=self.add(title='Birthday mum',at='2026-10-20T09:00',kind='birthday')
        self.assertEqual(event['repeat'],'yearly');self.assertEqual(event['leads'],'[1440]')
        self.c.tick(self.api,123,self.time);self.assertEqual(len(self.api.sent),1)
        self.assertIn('20.10.2026 09:00',self.api.sent[0]['text'])
        self.c.tick(self.api,123,datetime(2027,10,19,9,tzinfo=ZONE));self.assertEqual(len(self.api.sent),2)
    def test_failed_delivery_is_visible_and_not_blindly_retried(self):
        self.add(requires_ack=False);api=API(True)
        self.c.tick(api,123,self.time+timedelta(minutes=2));self.c.tick(api,123,self.time+timedelta(minutes=3))
        # One notice plus one daily alert about its uncertain delivery.
        self.assertEqual(len(api.sent),2);self.assertEqual(len(self.c.status()['uncertain']),1)
    def test_done_cancels_overdue_followups(self):
        self.add();self.c.tick(self.api,123,self.time+timedelta(minutes=2))
        notice=self.c.rows('SELECT id FROM personal_notices ORDER BY due')[0]['id']
        self.c.execute({'action':'done','notice_id':notice},'callback:1',self.time)
        self.c.tick(self.api,123,self.time+timedelta(days=2));self.assertEqual(len(self.api.sent),1)
    def test_snooze_is_durable_and_callback_retry_is_idempotent(self):
        self.add(requires_ack=False);self.c.tick(self.api,123,self.time+timedelta(minutes=2))
        notice=self.c.rows('SELECT id FROM personal_notices')[0]['id']
        data={'action':'snooze','notice_id':notice,'minutes':60}
        self.c.execute(data,'callback:1',self.time);self.c.execute(data,'callback:1',self.time)
        self.c.tick(self.api,123,self.time+timedelta(minutes=61));self.assertEqual(len(self.api.sent),2)
    def test_cancel_and_update_reschedule_without_old_delivery(self):
        e=self.add(requires_ack=False)
        self.c.execute({'action':'update','id':e['id'],'at':iso(self.time+timedelta(days=2))},'owner:2',self.time)
        self.c.tick(self.api,123,self.time+timedelta(minutes=2));self.assertFalse(self.api.sent)
        self.c.execute({'action':'cancel','id':e['id']},'owner:3',self.time)
        self.c.tick(self.api,123,self.time+timedelta(days=3));self.assertFalse(self.api.sent)
    def test_recurring_month_end_and_february_birthday(self):
        e={'at':'2026-01-31T09:00:00+05:00','repeat':'monthly'}
        self.assertEqual(self.c.occurrence(e,1).day,28);self.assertEqual(self.c.occurrence(e,2).day,31)
        e={'at':'2024-02-29T09:00:00+05:00','repeat':'yearly'}
        self.assertEqual(self.c.occurrence(e,1).day,28);self.assertEqual(self.c.occurrence(e,4).day,29)
    def test_offline_catchup_merges_missed_notices(self):
        self.add(lead_minutes=[0,1],requires_ack=False)
        self.c.tick(self.api,123,self.time+timedelta(hours=3));self.assertEqual(len(self.api.sent),1)
    def test_crash_during_sending_is_uncertain_after_restart(self):
        self.add(requires_ack=False)
        self.c.execute({'action':'list'},clock=self.time)
        self.c.db.execute("UPDATE personal_notices SET state='sending'");self.c.recover()
        self.assertTrue(self.c.status()['uncertain'])
    def test_add_retry_and_same_event_new_source_do_not_duplicate(self):
        e=self.add();self.add()
        self.c.execute({'action':'add','title':e['title'],'at':e['at']},'owner:2',self.time)
        self.assertEqual(len(self.c.rows('SELECT * FROM personal_events')),1)
    def test_relative_time_computed_by_code_and_past_once_rejected(self):
        r=self.c.execute({'action':'add','title':'Relative','after_minutes':5},'owner:relative',self.time)
        self.assertEqual(r['next_occurrence_at'],iso(self.time+timedelta(minutes=5)))
        with self.assertRaises(ValueError):self.add(at=iso(self.time-timedelta(days=1)))

if __name__=='__main__':unittest.main()
