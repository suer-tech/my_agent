"""Run on VPS against installed MAX code, using temporary state and fake browser only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0]=['/opt/max','/usr/local/lib/iva-browser']
import browser_turn as turn
import daily_schedule as daily
import ad_classification as ads
import broadcast as broadcast
import broadcast_worker as worker


class NativeResumeTest(unittest.TestCase):
    def test_daily_range_and_next_link_survive_new_instance(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'DAILY_WINDOW_START':'12345'}):
            links=['https://max.ru/test-'+str(i) for i in range(5)]
            first=daily.DailySchedule('RB',{'daily_limit':10,'auto':False},directory=tmp,clock=lambda:12346,logger=lambda *_:None)
            self.assertEqual(first.select_links(links,2,4),list(enumerate(links,1))[1:4])
            first.record(links[1],1)
            restored=daily.DailySchedule('RB',{'daily_limit':10,'auto':False},directory=tmp,clock=lambda:12347,logger=lambda *_:None)
            self.assertEqual(restored.select_links(links),[(3,links[2]),(4,links[3])])
            self.assertEqual(restored.data['count'],1)

    def test_classification_restores_only_unprocessed_urls(self):
        links=['https://max.ru/test-a','https://max.ru/test-b']
        with tempfile.TemporaryDirectory() as tmp, patch.object(ads,'load_source',return_value=links),patch.object(ads.ClassificationRun,'export'):
            first=ads.ClassificationRun({'mode':'RB'},directory=tmp)
            first.save(links[0],{'title':'Synthetic'},ads.review_result('Synthetic only'))
            restored=ads.ClassificationRun({'mode':'RB'},directory=tmp)
            self.assertEqual(restored.pending(),[links[1]])

    def test_broadcast_yields_after_ack_and_never_repeats_sent_or_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'checkpoints').mkdir()
            db=broadcast.database(root)
            db.execute("INSERT INTO campaigns(id,created,config,phase) VALUES ('test',1000,'{}','prepared')")
            for pos,state in enumerate(['ready','ready','uncertain'],1):
                db.execute("INSERT INTO deliveries(campaign,recipient,position,text,state,title,scheduled_at,deadline_at) VALUES ('test',?,?,?,?,?,1000,9000)",(str(pos),pos,'synthetic text',state,'Synthetic '+str(pos)))
            db.commit()
            sent=[]
            class FakeSender:
                def open(self,title,**_): self.title=title
                def send(self,text):
                    sent.append(self.title)
                    turn.atomic(root/'grant.json',{'task':'max-broadcast','yield':True})
                    return {'id':'synthetic-ack'}
            def next_row(db,campaign,**_):
                return db.execute("SELECT * FROM deliveries WHERE campaign=? AND state='ready' ORDER BY position LIMIT 1",(campaign,)).fetchone()
            with patch.object(turn,'STATE',root),patch.object(turn,'TASK','max-broadcast'),patch.object(turn,'YIELDED',False),patch.object(turn,'launch_key',return_value='test'),patch.object(worker,'refresh_campaign',return_value={}),patch.object(worker,'is_sending_time',return_value=True),patch.object(broadcast,'ensure_schedule'),patch.object(broadcast,'validate_prepared'),patch.object(broadcast,'prepare_next_delivery',side_effect=next_row):
                turn.atomic(root/'grant.json',{'task':'max-broadcast','yield':False})
                with self.assertRaises(SystemExit):worker.send_campaign(db,'test',FakeSender(),clock=lambda:1001,directory=root)
                self.assertEqual(sent,['Synthetic 1'])
                db.close();db=broadcast.database(root)
                states={r['recipient']:r['state'] for r in db.execute('SELECT * FROM deliveries')}
                self.assertEqual(states,{'1':'sent','2':'ready','3':'uncertain'})
                turn.atomic(root/'grant.json',{'task':'max-broadcast','yield':False})
                worker.send_campaign(db,'test',FakeSender(),clock=lambda:2000,directory=root)
                print('DEBUG',sent,[dict(r) for r in db.execute('SELECT recipient,state,error FROM deliveries')])
                self.assertEqual(sent,['Synthetic 1','Synthetic 2'])
            db.close()


if __name__=='__main__':unittest.main()
