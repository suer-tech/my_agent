import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from iva_integration.scheduled_tasks import Store,validate
from iva_integration.scheduled_worker import tick

NOW=datetime(2026,10,25,9,tzinfo=timezone.utc)
class DemandScheduler(unittest.TestCase):
    def setUp(self):
        self.priority=patch('iva_integration.research_priority.acquire',side_effect=lambda job,cycle=None:{'ok':True,'granted':True,'lease_token':'test-lease','job_id':job,'cycle_id':cycle})
        self.priority.start();self.addCleanup(self.priority.stop)
        self.reconcile=patch('iva_integration.research_priority.reconcile');self.reconcile.start();self.addCleanup(self.reconcile.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'schedule.sqlite3')
        self.spec={'title':'Исследование','task':'Исследуй спрос на ПО и хозблоки','kind':'demand_research','cron':'0 * * * *','notification':'changed'}
        self.job=self.store.execute({'action':'create',**self.spec},'owner:research',clock=NOW)['job']['id']
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def due(self):self.store.db.execute('UPDATE scheduled_jobs SET next_at=? WHERE id=?',(NOW.isoformat(),self.job))
    def test_browser_resource_hold_does_not_starve_due_mail(self):
        self.assertEqual(validate(self.spec)['kind'],'demand_research')
        self.due()
        mail=self.store.execute({'action':'create','title':'Mail','task':'Check','kind':'agent','interval_minutes':180},'owner:mail',clock=NOW)['job']['id']
        self.store.db.execute('UPDATE scheduled_jobs SET next_at=? WHERE id=?',((NOW+timedelta(seconds=10)).isoformat(),mail))
        research,run=self.store.claim(NOW)
        self.store.finish(research,run,{'ok':True,'has_more':True,'notify':False,'text':'Fetch checkpoint','cursor':{'demand_cycle_id':'active-cycle','research_lease_token':'lease'}},NOW+timedelta(seconds=20))
        self.assertEqual(self.store.rows('SELECT state FROM scheduled_runs')[0]['state'],'continued')
        row,_=self.store.claim(NOW+timedelta(minutes=1))
        self.assertEqual(row['id'],mail)
        self.assertEqual(json.loads(self.store.rows('SELECT cursor FROM scheduled_jobs WHERE id=?',(self.job,))[0]['cursor'])['research_lease_token'],'lease')
    def test_research_continues_immediately_and_model_busy_defers_without_failure(self):
        self.due();tick(self.store,lambda *_:{'ok':True,'has_more':True,'notify':False},NOW)
        job=self.store.rows('SELECT * FROM scheduled_jobs WHERE id=?',(self.job,))[0]
        self.assertEqual(job['next_at'],NOW.isoformat())
        self.due();tick(self.store,lambda *_:{'ok':True,'has_more':True,'notify':False,'status':'waiting_model','retry_after_seconds':60},NOW)
        job=self.store.rows('SELECT * FROM scheduled_jobs WHERE id=?',(self.job,))[0]
        self.assertEqual(job['failures'],0);self.assertEqual(job['next_at'],(NOW+timedelta(seconds=60)).isoformat())
        self.assertEqual(self.store.rows('SELECT * FROM scheduled_outbox'),[])

    def test_findings_deduplicate_across_non_notifying_checkpoints(self):
        self.due();finding={'ok':True,'notify':True,'text':'New supported hypothesis'}
        tick(self.store,lambda *_:finding,NOW)
        self.due();tick(self.store,lambda *_:{'ok':True,'notify':False,'has_more':True,'text':'next stage'},NOW)
        self.due();tick(self.store,lambda *_:finding,NOW)
        self.assertEqual(len(self.store.rows('SELECT * FROM scheduled_outbox')),1)
    def test_partial_coverage_is_visible(self):
        self.due();tick(self.store,lambda *_:{'ok':True,'notify':False,'status':'partial','coverage':{'failed':2}},NOW)
        self.assertEqual(self.store.rows('SELECT state FROM scheduled_runs')[0]['state'],'completed_with_gaps')
    def test_memory_retry_keeps_final_across_hour_without_new_research(self):
        from iva_integration.scheduled_worker import run_demand
        cards=Path(self.tmp.name)/'research/cards';cards.mkdir(parents=True)
        final={'ok':True,'notify':True,'text':'A verified finding','cards_persisted':True,'has_more':False,'cycle_id':'cycle1'}
        row={'id':self.job,'cursor':'{}'}
        with patch('iva_integration.demand_research.CARDS',cards),patch('iva_integration.demand_research.run_step',return_value=final) as step,patch('iva_integration.demand_memory.sync_cards',side_effect=[{'ok':False,'errors':[{'reason':'temporary'}]}, {'ok':True,'created':1}]):
            failed=run_demand(row,self.spec)
            self.assertFalse(failed['ok']);self.assertIn('demand_pending_result',failed['cursor'])
            recovered=run_demand({**row,'cursor':json.dumps(failed['cursor'])},self.spec)
            self.assertTrue(recovered['ok']);self.assertTrue(recovered['notify']);self.assertEqual(recovered['cycle_id'],'cycle1')
            self.assertEqual(recovered['cursor'],{});step.assert_called_once()
    def test_cycle_anchor_replays_final_after_crash_before_outbox_commit(self):
        from iva_integration.scheduled_worker import run_demand
        cards=Path(self.tmp.name)/'anchor/cards';cards.mkdir(parents=True)
        self.due()
        checkpoint={'ok':True,'notify':False,'has_more':True,'cycle_id':'old-hour'}
        final={'ok':True,'notify':True,'text':'New supported finding','cards_persisted':True,
               'has_more':False,'cycle_id':'old-hour'}
        with patch('iva_integration.demand_research.CARDS',cards),patch('iva_integration.demand_research.run_step',side_effect=[checkpoint,final,final]) as step,patch('iva_integration.demand_memory.sync_cards',return_value={'ok':True}):
            tick(self.store,clock=NOW)
            row=self.store.rows('SELECT * FROM scheduled_jobs WHERE id=?',(self.job,))[0]
            self.assertEqual(json.loads(row['cursor'])['demand_cycle_id'],'old-hour')
            # Finalized in research DB, then process died without Store.finish.
            lost=run_demand(row,self.spec)
            self.assertTrue(lost['notify'])
            self.assertEqual(self.store.rows('SELECT * FROM scheduled_outbox'),[])
            self.assertEqual(json.loads(self.store.rows('SELECT cursor FROM scheduled_jobs WHERE id=?',(self.job,))[0]['cursor'])['demand_cycle_id'],'old-hour')
            # One hour later the exact completed cycle is replayed and delivered.
            self.due();tick(self.store,clock=NOW+timedelta(hours=1))
            self.assertEqual(step.call_args_list[-1].kwargs,{'cycle_id':'old-hour'})
            self.assertEqual(json.loads(self.store.rows('SELECT cursor FROM scheduled_jobs WHERE id=?',(self.job,))[0]['cursor']),{})
            self.assertEqual(len(self.store.rows('SELECT * FROM scheduled_outbox')),1)

    def test_terminal_failure_clears_cycle_anchor_but_retry_preserves_it(self):
        from iva_integration.scheduled_worker import run_demand
        row={'id':self.job,'cursor':json.dumps({'demand_cycle_id':'old','unrelated':1})}
        with patch('iva_integration.demand_research.run_step',return_value={'ok':False,'has_more':True,'cycle_id':'old'}):
            cursor=run_demand(row,self.spec)['cursor']
            self.assertEqual(cursor['demand_cycle_id'],'old');self.assertEqual(cursor['research_lease_token'],'test-lease')
        with patch('iva_integration.demand_research.run_step',return_value={'ok':False,'has_more':False,'cycle_id':'old'}):
            self.assertEqual(run_demand(row,self.spec)['cursor'],{'unrelated':1})

    def test_waiting_for_browser_checkpoint_does_not_start_research(self):
        from iva_integration.scheduled_worker import run_demand
        with patch('iva_integration.research_priority.acquire',return_value={'ok':True,'granted':False,'status':'waiting_background','lease_token':'queued'}),patch('iva_integration.demand_research.run_step') as step:
            result=run_demand({'id':self.job,'cursor':'{}'},self.spec)
        self.assertTrue(result['has_more']);self.assertEqual(result['status'],'waiting_background')
        self.assertEqual(result['cursor']['research_lease_token'],'queued');step.assert_not_called()

    def test_permanent_memory_failure_releases_resources_without_losing_finding(self):
        from iva_integration.scheduled_worker import run_demand
        cards=Path(self.tmp.name)/'blocked/cards';cards.mkdir(parents=True)
        final={'ok':True,'notify':True,'text':'A verified finding','cards_persisted':True,'has_more':False,'cycle_id':'preserved'}
        row={'id':self.job,'cursor':'{}'}
        failures=[{'ok':False,'errors':[{'reason':'io'}]}]*6+[{'ok':True}]
        with patch('iva_integration.demand_research.CARDS',cards),patch('iva_integration.demand_research.run_step',return_value=final) as step,patch('iva_integration.demand_memory.sync_cards',side_effect=failures):
            for _ in range(6):
                result=run_demand(row,self.spec);row={**row,'cursor':json.dumps(result['cursor'])}
            self.assertEqual(result['status'],'resource_blocked');self.assertEqual(result['retry_after_seconds'],900)
            self.assertNotIn('research_lease_token',result['cursor'])
            self.assertEqual(result['cursor']['demand_cycle_id'],'preserved')
            self.assertIn('demand_pending_result',result['cursor'])
            done=run_demand(row,self.spec)
            self.assertTrue(done['notify']);self.assertEqual(done['cursor'],{});step.assert_called_once()
    def test_memory_batches_keep_notice_and_manual_conflicts_allow_next_cycle(self):
        from iva_integration.scheduled_worker import run_demand
        cards=Path(self.tmp.name)/'research/cards';cards.mkdir(parents=True)
        final={'ok':True,'notify':True,'text':'A verified finding','cards_persisted':True,'has_more':False,'cycle_id':'cycle2'}
        row={'id':self.job,'cursor':'{}'}
        with patch('iva_integration.demand_research.CARDS',cards),patch('iva_integration.demand_research.run_step',return_value=final) as step,patch('iva_integration.demand_memory.sync_cards',side_effect=[{'ok':True,'has_more':True,'pending':1},{'ok':False,'errors':[],'conflicts':[{'reason':'manual_or_unmanaged_changes'}]}]):
            partial=run_demand(row,self.spec)
            self.assertTrue(partial['ok']);self.assertFalse(partial['notify']);self.assertTrue(partial['has_more'])
            done=run_demand({**row,'cursor':json.dumps(partial['cursor'])},self.spec)
            self.assertTrue(done['ok']);self.assertTrue(done['notify']);self.assertEqual(done['status'],'completed_with_gaps');self.assertEqual(done['cursor'],{})
            step.assert_called_once()

    def test_dedicated_route_never_calls_legacy_browser(self):
        self.due()
        with patch('iva_integration.demand_research.run_step',return_value={'ok':True,'notify':False}) as research, patch('iva_integration.scheduled_worker.run_agent') as generic:
            tick(self.store,clock=NOW)
        research.assert_called_once_with(self.job,self.spec['task']);generic.assert_not_called()

    def attempt(self,result,clock=NOW):
        self.due()
        tick(self.store,lambda *_:result,clock)
        return self.store.rows('SELECT * FROM scheduled_jobs WHERE id=?',(self.job,))[0]

    def test_internal_retries_are_silent_but_keep_diagnostics_and_checkpoint(self):
        result={'ok':False,'has_more':True,'notify':False,'status':'retrying','stage':'agent_review',
                'error_category':'agent_review_keys_invalid','error':'Typed validation error',
                'cycle_id':'cycle1','cursor':{'demand_cycle_id':'cycle1','research_lease_token':'keep'}}
        for index in range(4):
            row=self.attempt(result,NOW+timedelta(minutes=index))
        self.assertEqual(self.store.rows('SELECT * FROM scheduled_outbox'),[])
        self.assertEqual(row['failures'],4)
        self.assertEqual(row['error'],'Typed validation error')
        cursor=json.loads(row['cursor'])
        self.assertEqual(cursor['demand_cycle_id'],'cycle1')
        self.assertEqual(cursor['research_lease_token'],'keep')
        self.assertEqual(cursor['demand_notice_state']['retry_count'],4)
        runs=self.store.rows('SELECT state,result,error FROM scheduled_runs')
        self.assertEqual(len(runs),4)
        self.assertTrue(all(item['state']=='failed' and item['error']=='Typed validation error' for item in runs))
        self.assertTrue(all(json.loads(item['result'])['scheduler_notice_policy']=='silent_retry' for item in runs))

    def test_terminal_failure_deduplicates_across_restart_and_new_cycle(self):
        result={'ok':False,'has_more':False,'notify':False,'status':'failed','stage':'agent_synthesis',
                'error_category':'schema_invalid','error':'invalid schema','cycle_id':'cycle1','cursor':{}}
        self.attempt(result)
        self.store.close();self.store=Store(Path(self.tmp.name)/'schedule.sqlite3')
        self.attempt({**result,'cycle_id':'cycle2'},NOW+timedelta(hours=1))
        notices=self.store.rows('SELECT text FROM scheduled_outbox')
        self.assertEqual(len(notices),1)
        self.assertIn('Текущий цикл исследования не завершён',notices[0]['text'])
        self.assertIn('сопоставление результатов',notices[0]['text'])
        self.assertNotIn('Не удалось выполнить задачу',notices[0]['text'])

    def test_blocked_resources_notify_once_with_actual_retry_time(self):
        result={'ok':False,'has_more':True,'notify':False,'status':'resource_blocked','stage':'agent_review',
                'error':'Repeated error','retry_after_seconds':900,'cursor':{'demand_cycle_id':'cycle1'}}
        row=self.attempt(result)
        self.assertEqual(row['next_at'],(NOW+timedelta(minutes=15)).isoformat())
        self.attempt(result,NOW+timedelta(minutes=15))
        notices=self.store.rows('SELECT text FROM scheduled_outbox')
        self.assertEqual(len(notices),1)
        self.assertIn('проверка отчёта',notices[0]['text'])
        self.assertIn('25.10 в 14:15',notices[0]['text'])

    def test_auth_quota_are_actionable_without_recovery_spam(self):
        for index,category in enumerate(('auth_unavailable','model_quota')):
            result={'ok':False,'has_more':True,'notify':False,'status':'retrying','stage':'discovery',
                    'error_category':category,'error':'service unavailable','cursor':{}}
            self.attempt(result,NOW+timedelta(minutes=index*3))
            self.attempt({'ok':True,'has_more':True,'notify':False,'cursor':{}},NOW+timedelta(minutes=index*3+1))
            self.attempt(result,NOW+timedelta(minutes=index*3+2))
        notices=self.store.rows('SELECT text FROM scheduled_outbox')
        self.assertEqual(len(notices),2)
        self.assertTrue(any('проверить вход' in item['text'] for item in notices))
        self.assertTrue(any('проверить доступный лимит' in item['text'] for item in notices))

    def test_unknown_retry_escalates_after_thirty_minutes_and_survives_restart(self):
        result={'ok':False,'has_more':True,'notify':False,'status':'retrying','error':'unknown','cursor':{}}
        self.attempt(result)
        self.store.close();self.store=Store(Path(self.tmp.name)/'schedule.sqlite3')
        self.attempt(result,NOW+timedelta(minutes=15))
        self.assertEqual(self.store.rows('SELECT * FROM scheduled_outbox'),[])
        self.attempt(result,NOW+timedelta(minutes=30))
        self.attempt(result,NOW+timedelta(minutes=45))
        self.assertEqual(len(self.store.rows('SELECT * FROM scheduled_outbox')),1)

    def test_completed_source_gaps_close_episode_without_recovery_notice(self):
        failure={'ok':False,'has_more':True,'notify':False,'status':'retrying',
                 'error_category':'auth_unavailable','error':'authentication','cursor':{}}
        self.attempt(failure)
        completed={'ok':True,'has_more':False,'notify':False,'status':'completed_with_gaps','cursor':{},
                   'coverage':{'failed':2,'partial_reads':[{'url':'https://example.org/doc'}],
                               'analysis_failed':0,'batches':[{'state':'done'}]}}
        row=self.attempt(completed,NOW+timedelta(minutes=5))
        self.assertNotIn('demand_notice_state',json.loads(row['cursor']))
        notices=self.store.rows('SELECT state FROM scheduled_outbox')
        self.assertEqual([item['state'] for item in notices],['cancelled'])
        self.attempt(failure,NOW+timedelta(hours=1))
        self.assertEqual(len(self.store.rows("SELECT * FROM scheduled_outbox WHERE state='pending'")),1)

    def test_unresolved_technical_gap_does_not_reset_failure_episode_or_finding_hash(self):
        self.attempt({'ok':True,'notify':True,'text':'A new verified finding','has_more':False})
        original=self.store.rows('SELECT last_hash FROM scheduled_jobs WHERE id=?',(self.job,))[0]['last_hash']
        failure={'ok':False,'has_more':False,'notify':False,'error':'invalid','error_category':'schema_invalid','cursor':{}}
        self.attempt(failure,NOW+timedelta(minutes=1))
        self.attempt({'ok':True,'notify':False,'has_more':False,'status':'completed_with_gaps','cursor':{},
                      'coverage':{'agent_progress':{'counts':{'failed':1}}}},NOW+timedelta(minutes=2))
        row=self.attempt(failure,NOW+timedelta(minutes=3))
        self.assertEqual(row['last_hash'],original)
        self.assertEqual(len(self.store.rows('SELECT * FROM scheduled_outbox')),2)

    def test_episode_close_never_changes_uncertain_or_sent_delivery(self):
        self.attempt({'ok':False,'has_more':False,'notify':False,'error':'failed','cursor':{}})
        self.store.db.execute("UPDATE scheduled_outbox SET state='uncertain'")
        self.attempt({'ok':True,'has_more':False,'notify':False,'status':'completed'},NOW+timedelta(minutes=5))
        self.assertEqual(self.store.rows('SELECT state FROM scheduled_outbox')[0]['state'],'uncertain')

    def agent_final(self,*,synthesis_failed=False,failed_roles=0,deferred=0):
        return {'ok':True,'notify':False,'has_more':False,'status':'completed_with_gaps',
                'cycle_id':'agent-cycle','cursor':{},'coverage':{
                    'failed':0,'partial_reads':[],'analysis_failed':0,'batches':[{'state':'done'}],
                    'agent_progress':{'counts':{'done':4-failed_roles,'failed':failed_roles,'deferred':deferred},
                        'plan_status':'limited','synthesis_failed':synthesis_failed,
                        'tasks':[{'id':'critic','role':'critic','status':'failed' if failed_roles else 'done'}],
                        'review_needed':['deferred-test'] if deferred else []}}}

    def test_real_shaped_failed_synthesis_ok_true_is_not_silent_or_closed(self):
        self.attempt({'ok':True,'notify':True,'has_more':False,'text':'Verified finding'})
        before=self.store.rows('SELECT last_hash FROM scheduled_jobs WHERE id=?',(self.job,))[0]['last_hash']
        result=self.agent_final(synthesis_failed=True)
        row=self.attempt(result,NOW+timedelta(minutes=1))
        self.assertIn('demand_notice_state',json.loads(row['cursor']))
        self.assertEqual(row['last_hash'],before)
        notices=self.store.rows('SELECT text FROM scheduled_outbox')
        self.assertEqual(len(notices),2)
        self.assertIn('завершён с техническими ограничениями',notices[-1]['text'])
        self.assertIn('сопоставление результатов',notices[-1]['text'])
        self.attempt(result,NOW+timedelta(minutes=2))
        self.assertEqual(len(self.store.rows('SELECT * FROM scheduled_outbox')),2)
        saved=json.loads(self.store.rows('SELECT result FROM scheduled_runs ORDER BY rowid DESC LIMIT 1')[0]['result'])
        self.assertTrue(saved['ok'])
        self.assertTrue(saved['coverage']['agent_progress']['synthesis_failed'])
        self.assertEqual(saved['scheduler_notice_policy'],'deduplicated_failure')

    def test_terminal_role_failure_ok_true_gets_one_notice_if_not_previously_reported(self):
        result=self.agent_final(failed_roles=1)
        self.attempt(result)
        self.attempt(result,NOW+timedelta(minutes=1))
        notices=self.store.rows('SELECT text FROM scheduled_outbox')
        self.assertEqual(len(notices),1)
        self.assertIn('работа исследователя',notices[0]['text'])

    def test_failed_synthesis_does_not_close_or_repeat_an_already_reported_failure(self):
        self.attempt({'ok':False,'has_more':True,'notify':False,'error_category':'auth_unavailable',
                      'error':'authentication','cycle_id':'agent-cycle','cursor':{}})
        row=self.attempt(self.agent_final(synthesis_failed=True),NOW+timedelta(minutes=5))
        self.assertIn('auth',json.loads(row['cursor'])['demand_notice_state']['notices'])
        self.assertEqual(len(self.store.rows('SELECT * FROM scheduled_outbox')),1)
        self.assertEqual(self.store.rows('SELECT state FROM scheduled_outbox')[0]['state'],'pending')

    def test_deferred_tests_and_http403_are_not_technical_execution_failures(self):
        result=self.agent_final(deferred=1)
        result['coverage'].update(failed=1,gaps=[{'error':'HTTP 403','url':'https://example.org/blocked'}],
                                  partial_reads=[{'url':'https://example.org/pdf'}])
        self.attempt(result)
        self.assertEqual(self.store.rows('SELECT * FROM scheduled_outbox'),[])
        row=self.store.rows('SELECT cursor FROM scheduled_jobs WHERE id=?',(self.job,))[0]
        self.assertNotIn('demand_notice_state',json.loads(row['cursor']))

    def test_public_last_error_survives_checkpoints_and_clears_after_real_completion(self):
        for failed in (self.agent_final(synthesis_failed=True),self.agent_final(failed_roles=1)):
            row=self.attempt(failed)
            public=self.store.public(row)
            self.assertIn('техническими ограничениями',public['last_error'])
            self.assertEqual(row['next_at'],(NOW+timedelta(hours=1)).isoformat())
            self.assertEqual(row['failures'],0)
            self.assertEqual(public['state'],'active')
            row=self.attempt({'ok':True,'has_more':True,'notify':False,'cursor':{}},NOW+timedelta(minutes=1))
            self.assertEqual(self.store.public(row)['last_error'],public['last_error'])
            completed=self.agent_final(deferred=1)
            completed['coverage'].update(failed=1,gaps=[{'error':'HTTP 403'}],partial_reads=[{'url':'https://example.org/pdf'}])
            row=self.attempt(completed,NOW+timedelta(minutes=5))
            self.assertIsNone(self.store.public(row)['last_error'])
            self.assertNotIn('demand_notice_state',json.loads(row['cursor']))

    def test_cron_title_update_keeps_current_cycle_export_lease_incident_and_continuation(self):
        cursor={'demand_cycle_id':'anchored-cycle','demand_pending_result':{'ok':True,'notify':True,'text':'Saved finding'},
                'research_lease_token':'held-lease','research_stall_count':2,
                'demand_notice_state':{'notices':{'validation':{'run_id':'old-run'}}},'unrelated':'preserved'}
        due=(NOW+timedelta(minutes=2)).isoformat()
        self.store.db.execute('UPDATE scheduled_jobs SET cursor=?,error=?,failures=?,last_hash=?,next_at=? WHERE id=?',
                              (json.dumps(cursor),'Незавершённая проверка отчёта',2,'finding-hash',due,self.job))
        updated=self.store.execute({'action':'update','id':self.job,'cron':'30 * * * *',
                                    'title':'Новое название','task':'Будущее исследование другой темы'},
                                   'owner:update-research',clock=NOW)
        row=self.store.rows('SELECT * FROM scheduled_jobs WHERE id=?',(self.job,))[0]
        self.assertEqual(json.loads(row['cursor']),cursor)
        self.assertEqual(row['next_at'],due)
        self.assertEqual(row['last_hash'],'finding-hash')
        self.assertEqual(row['failures'],2)
        self.assertEqual(updated['job']['last_error'],'Незавершённая проверка отчёта')
        self.assertEqual(json.loads(row['spec'])['title'],'Новое название')
        self.assertEqual(json.loads(row['spec'])['cron'],'30 * * * *')
        self.assertEqual(json.loads(row['spec'])['task'],'Будущее исследование другой темы')

    def test_idle_research_update_uses_new_schedule_without_clearing_incident(self):
        cursor={'demand_notice_state':{'notices':{'auth':{'run_id':'old-run'}}}}
        self.store.db.execute('UPDATE scheduled_jobs SET cursor=?,error=? WHERE id=?',
                              (json.dumps(cursor),'Требуется проверка входа',self.job))
        self.store.execute({'action':'update','id':self.job,'cron':'30 * * * *','title':'Название'},
                           'owner:update-idle-research',clock=NOW)
        row=self.store.rows('SELECT * FROM scheduled_jobs WHERE id=?',(self.job,))[0]
        self.assertEqual(row['next_at'],(NOW+timedelta(minutes=30)).isoformat())
        self.assertEqual(json.loads(row['cursor']),cursor)
        self.assertEqual(self.store.public(row)['last_error'],'Требуется проверка входа')

if __name__=='__main__':unittest.main()
