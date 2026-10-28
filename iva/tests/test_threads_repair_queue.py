import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iva_integration import threads_repair as r


class RepairQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.now=1000
        self.queue=r.Queue(self.root/'queue',lambda:self.now)
        self.resources=patch.object(r,'resources_available',return_value=True)
        self.resources.start()

    def tearDown(self):
        self.queue.close()
        self.resources.stop()
        self.temp.cleanup()

    def observe(self, stamp, code='messages', key='inbox:messages', **extra):
        return self.queue.observe('extension',{key:{'code':code}},
            {'checked_at':stamp,'inbox_checked_at':stamp,**extra})

    def confirmed(self):
        for stamp in (1,2,3):
            ids=self.observe(stamp)
        return ids[0]

    def test_three_independent_errors_required(self):
        self.assertEqual(self.observe(1),[])
        self.assertEqual(self.observe(1),[])
        self.assertEqual(self.observe(2),[])
        self.assertEqual(len(self.observe(3)),1)

    def test_stale_inbox_does_not_confirm_via_monitor_samples(self):
        for stamp in range(20):
            self.queue.observe('extension',{'inbox:messages':{'code':'messages'}},
                {'checked_at':stamp,'inbox_checked_at':10})
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    def test_failed_cycle_is_immediate_but_not_for_unrelated_warning(self):
        cycle={'id':7,'state':'error','finished':999}
        self.assertEqual(self.observe(1,'queue','queue',cycle=cycle),[])
        self.assertEqual(len(self.observe(1,'interface','cycle:interface',cycle=cycle)),1)

    def test_persisted_uncertain_publication_is_immediately_confirmed(self):
        url='https://www.threads.com/@public.user/post/ABC123'
        ids=self.queue.observe('development',{'publication:'+url:{'code':'publication','url':url}},
            {'checked_at':1,'cycle':{'id':81,'state':'done','finished':999},
             'posts':[{'url':url,'state':'uncertain','reply_url':None,'attempt_at':998}]})
        self.assertEqual(len(ids),1)
        self.assertEqual(self.queue.db.execute('SELECT code FROM jobs WHERE id=?',(ids[0],)).fetchone()[0],'publication')

    def test_sending_publication_is_not_immediately_an_error(self):
        url='https://www.threads.com/@public.user/post/ABC123'
        self.assertEqual(self.queue.observe('development',{'publication:'+url:{'code':'publication','url':url}},
            {'checked_at':1,'posts':[{'url':url,'state':'sending','reply_url':None,'attempt_at':998}]}),[])

    def test_uncertain_evidence_must_match_incident_and_does_not_accelerate_like(self):
        url='https://www.threads.com/@public.user/post/ABC123'
        evidence={'checked_at':1,'posts':[{'url':url,'state':'uncertain','reply_url':None}]}
        self.assertEqual(self.queue.observe('development',{'publication:https://www.threads.com/@another/post/Other':
            {'code':'publication','url':'https://www.threads.com/@another/post/Other'}},evidence),[])
        self.assertEqual(self.queue.observe('extension',{'like:'+url:{'code':'like','url':url}},evidence),[])

    def test_stall_needs_actual_expired_operation_deadline(self):
        for stamp in range(3):
            self.assertEqual(self.observe(stamp,'stalled','stalled',operation_deadline=2000),[])
        for stamp in (4,5,6):
            ids=self.observe(stamp,'stalled','stalled',operation_deadline=900)
        self.assertEqual(len(ids),1)

    def test_continuing_incident_does_not_make_infinite_jobs(self):
        ident=self.confirmed()
        self.queue.update(ident,'review_required',{'reason':'manual_review'})
        self.now+=r.COOLDOWN*10
        for stamp in range(20,30):
            self.assertEqual(self.observe(stamp),[])
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)

    def test_cooldown_applies_to_new_incident_episode(self):
        ident=self.confirmed()
        self.queue.update(ident,'failed',{})
        self.queue.observe('extension',{}, {'checked_at':4})
        for stamp in (5,6,7):
            self.assertEqual(self.observe(stamp),[])
        self.now+=r.COOLDOWN
        self.assertEqual(len(self.observe(8)),1)

    def test_disappeared_pending_issue_is_cancelled(self):
        ident=self.confirmed()
        self.queue.observe('extension',{}, {'checked_at':4})
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ident,)).fetchone()[0], 'resolved_without_repair')

    def test_live_inbox_success_only_after_install_verifies(self):
        ident=self.confirmed()
        self.queue.update(ident,'awaiting_live_verification',{'installed':True,'installed_at':100})
        self.queue.observe('extension',{}, {'checked_at':200,'inbox_checked_at':99,'inbox':{'state':'ok'}})
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ident,)).fetchone()[0], 'awaiting_live_verification')
        # Fresh result can arrive on a later observation after the issue disappeared.
        self.queue.observe('extension',{}, {'checked_at':202,'inbox_checked_at':202,'inbox':{'state':'ok'}})
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ident,)).fetchone()[0], 'verified')

    def test_private_fields_and_freeform_errors_are_dropped(self):
        result=r.clean_evidence({'token':'private','body':'private','checked_at':2,
            'cycle':{'id':1,'state':'error','error':'private message'},
            'inbox':{'error':'password=private','state':'error','folder':'requests','preview':'private'}})
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual(result['inbox']['folder'],'requests')

    def test_public_post_metadata_keeps_like_failure_without_dm_or_query_data(self):
        cleaned=r.clean_evidence({'posts':[
            {'url':'https://threads.com/@public.user/post/ABC_123/?token=private#private','state':'sent',
             'liked':-1,'attempt_at':100,'reply_url':'https://www.threads.com/@suer_stuff/post/Reply-9',
             'text':'private','preview':'private'},
            {'url':'https://www.threads.com/messages/private','state':'sent','liked':-1},
            {'url':'https://www.threads.com.evil.test/@x/post/a','state':'sent'},
            {'url':'https://user:private@www.threads.com/@x/post/a','state':'sent'}]})
        self.assertEqual(len(cleaned['posts']),1)
        self.assertEqual(cleaned['posts'][0],{'url':'https://www.threads.com/@public.user/post/ABC_123',
            'state':'sent','liked':-1,'attempt_at':100,'reply_url':'https://www.threads.com/@suer_stuff/post/Reply-9'})
        self.assertNotIn('private',json.dumps(cleaned))

    def test_post_metadata_is_bounded_and_never_preserves_message_reply_url(self):
        cleaned=r.clean_evidence({'posts':[{'url':'https://www.threads.com/@x/post/a','liked':True,
            'reply_url':'https://www.threads.com/messages/private','state':'private','attempt_at':float('nan')}]*100})
        self.assertEqual(len(cleaned['posts']),20)
        self.assertEqual(cleaned['posts'][0],{'url':'https://www.threads.com/@x/post/a','reply_url':None})

    def test_activity_and_unit_health_are_allowlisted(self):
        cleaned=r.clean_evidence({'activity':{'stage':'scan_inbox','state':'running','deadline_at':123,'body':'private'},
            'units':{'threads-browser.service':{'ActiveState':'failed','Result':'oom-kill','ExecMainStatus':'9','Environment':'private'},
                     'private.service':{'ActiveState':'private'}}})
        self.assertEqual(cleaned['activity'],{'stage':'scan_inbox','state':'running','deadline_at':123})
        self.assertEqual(cleaned['units'],{'threads-browser.service':{'ActiveState':'failed','Result':'oom-kill','ExecMainStatus':'9'}})
        self.assertNotIn('private',json.dumps(cleaned))

    def test_evidence_is_durable_and_status_contains_no_text(self):
        self.confirmed()
        self.queue.close()
        self.queue=r.Queue(self.root/'queue',lambda:self.now)
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
        status=json.loads((self.root/'queue/status.json').read_text())
        self.assertFalse(status['technical_telegram_notifications'])

    def fixture(self):
        app=self.root/'app'
        (app/'iva_integration').mkdir(parents=True)
        (app/'local_finance').mkdir()
        (app/'tests').mkdir()
        (app/'iva_integration/threads_inbox.py').write_text('def store():\n    return 1\n\ndef wake():\n    return 1\n\ndef scan():\n    return 1\n')
        (app/'iva_integration/threads_page.py').write_text('def working_page():\n    return 1\n')
        for name in r.FIXED_TESTS:
            (app/(name.replace('.','/')+'.py')).write_text('import unittest\n')
        return app

    def proposal(self, **overrides):
        return {'root_cause':'synthetic reader bug','disposition':'fix',
                'edits':[{'path':'iva_integration/threads_page.py','old':'return 1','new':'return 2'}],
                'regression_tests':'import unittest\nclass T(unittest.TestCase):\n def test_value(self):\n  from iva_integration.threads_page import working_page\n  self.assertEqual(working_page(),2)\n',**overrides}

    def test_patch_restricted_to_allowed_files_and_unique_match(self):
        app=self.fixture()
        for edits in ([{'path':'../data/private','old':'x','new':'y'}],
                      [{'path':'iva_integration/threads_page.py','old':'missing','new':'anything'}]):
            with self.assertRaises(ValueError):
                r.apply_proposal(app,self.proposal(edits=edits))

    def test_diagnostic_sources_follow_failing_component(self):
        app=self.fixture()
        (app/'iva_integration/threads_hourly.py').write_text('PUBLIC_PUBLISHER = True')
        (app/'iva_integration/threads_browser_rpc.py').write_text('PUBLIC_RPC = True')
        readers={'iva_integration/threads_inbox.py':'PUBLIC_READER = True'}
        self.assertEqual(r.diagnostic_sources(app,'messages',readers),readers)
        sources=r.diagnostic_sources(app,'like',readers)
        self.assertIn('iva_integration/threads_hourly.py',sources)
        self.assertIn('iva_integration/threads_browser_rpc.py',sources)
        self.assertNotIn('iva_integration/threads_inbox.py',sources)

    def test_nonreader_incident_cannot_patch_an_unrelated_reader(self):
        app=self.fixture()
        (app/'iva_integration/threads_hourly.py').write_text('PUBLIC_PUBLISHER = True')
        for stamp in (1,2,3):
            ids=self.observe(stamp,'like','like:public')
        proposal=self.proposal()
        prompts=[]
        class CLI:
            def structured(self,prompt,_):
                prompts.append(prompt)
                return proposal
        with patch.object(r,'probe_sandbox') as probe, patch.object(r,'install') as install:
            r.run_one(self.queue,app,lambda:True,lambda:CLI(),install_fn=install)
        probe.assert_not_called()
        install.assert_not_called()
        self.assertIn('PUBLIC_PUBLISHER',prompts[0])
        self.assertIn('READ ONLY',prompts[0])
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ids[0],)).fetchone()[0],'review_required')
        result=json.loads(self.queue.db.execute('SELECT result FROM jobs WHERE id=?',(ids[0],)).fetchone()[0])
        self.assertEqual(result['reason'],'proposal_rejected')

    def test_other_threads_fix_is_tested_and_staged_but_never_installed(self):
        app=self.fixture()
        name='iva_integration/threads_hourly.py'
        original='def like_state():\n    return 1\n'
        (app/name).write_text(original)
        for stamp in (1,2,3):
            ids=self.observe(stamp,'like','like:public')
        proposal=self.proposal(edits=[{'path':name,'old':'return 1','new':'return 2'}],
            regression_tests='import unittest\nclass T(unittest.TestCase):\n def test_like(self):\n  from iva_integration.threads_hourly import like_state\n  self.assertEqual(like_state(),2)\n')
        prompts=[]
        class CLI:
            def structured(self,prompt,_):
                prompts.append(prompt)
                return proposal
        with patch.object(r,'probe_sandbox',return_value=True), patch.object(r,'run_sandbox',side_effect=[{'passed':False},{'passed':True}]) as run, patch.object(r,'install') as install:
            r.run_one(self.queue,app,lambda:True,lambda:CLI(),install_fn=install)
        self.assertEqual(run.call_count,2)
        install.assert_not_called()
        self.assertEqual((app/name).read_text(),original)
        result=json.loads(self.queue.db.execute('SELECT result FROM jobs WHERE id=?',(ids[0],)).fetchone()[0])
        self.assertEqual(result['reason'],'tested_proposal_requires_review')
        self.assertTrue(result['tests_passed'])
        self.assertFalse(result['installed'])
        stage=Path(result['stage'])
        self.assertIn('return 2',(stage/name).read_text())
        self.assertTrue((stage.parent/'prepared.json').is_file())
        self.assertFalse((self.root/'queue'/ids[0]/'ready.json').exists())
        self.assertIn('executable regression test',prompts[0])

    def test_root_helpers_are_diagnosis_only_and_not_schema_edit_paths(self):
        self.assertEqual(r.proposal_scope('coordinator'),())
        self.assertEqual(r.proposal_scope('queue'),())
        paths=r.SCHEMA['properties']['edits']['items']['properties']['path']['enum']
        self.assertEqual(set(paths),set(r.PROPOSAL_FILES))
        self.assertIn('iva_integration/threads_alerts.py',paths)
        self.assertNotIn('deploy/browser_coordinator.py',paths)
        self.assertEqual(set(r.ALLOWED),{'iva_integration/threads_inbox.py','iva_integration/threads_page.py'})

    def test_patch_protects_inbox_store_and_wake(self):
        app=self.fixture()
        for name in ('store','wake'):
            with self.assertRaises(ValueError):
                r.apply_proposal(app,self.proposal(edits=[{'path':'iva_integration/threads_inbox.py',
                    'old':f'def {name}():\n    return 1','new':f'def {name}():\n    return 2'}]))

    def test_syntax_error_rejected(self):
        app=self.fixture()
        with self.assertRaises(SyntaxError):
            r.apply_proposal(app,self.proposal(edits=[{'path':'iva_integration/threads_page.py','old':'return 1','new':'return ('}]))

    def test_stage_has_no_data_or_secrets(self):
        app=self.fixture()
        (app/'data').mkdir()
        (app/'data/private').write_text('private')
        (app/'.env').write_text('private')
        stage=self.root/'stage'
        r.stage_sources(app,stage)
        self.assertFalse((stage/'data').exists())
        self.assertFalse((stage/'.env').exists())

    def test_no_codex_without_memory_headroom(self):
        self.confirmed()
        self.assertFalse(r.run_one(self.queue, idle_check=lambda:True,
            cli_factory=lambda:self.fail('No model without memory'), resources_check=lambda:False))

    def test_no_generated_tests_without_sandbox(self):
        ident=self.confirmed()
        app=self.fixture()
        proposal=self.proposal()
        class CLI:
            def structured(self,*_):return proposal
        with patch.object(r,'probe_sandbox',return_value=False), patch.object(r,'run_sandbox') as run:
            r.run_one(self.queue, app, lambda:True, lambda:CLI())
        run.assert_not_called()
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ident,)).fetchone()[0], 'review_required')
        self.assertIn('return 1',(app/'iva_integration/threads_page.py').read_text())

    def test_passing_baseline_cannot_authorize_install(self):
        ident=self.confirmed()
        app=self.fixture()
        proposal=self.proposal()
        class CLI:
            def structured(self,*_):return proposal
        with patch.object(r,'probe_sandbox',return_value=True), patch.object(r,'run_sandbox',return_value={'passed':True}), patch.object(r,'install') as install:
            r.run_one(self.queue,app,lambda:True,lambda:CLI(),install_fn=install)
        install.assert_not_called()
        self.assertEqual(json.loads(self.queue.db.execute('SELECT result FROM jobs WHERE id=?',(ident,)).fetchone()[0])['reason'],'regression_did_not_reproduce')

    def test_fixed_tests_and_regression_pass_then_wait_for_live_check(self):
        ident=self.confirmed()
        app=self.fixture()
        proposal=self.proposal()
        class CLI:
            def structured(self,*_):return proposal
        with patch.object(r,'probe_sandbox',return_value=True), patch.object(r,'run_sandbox',side_effect=[{'passed':False},{'passed':True}]), patch.object(r,'install') as install:
            r.run_one(self.queue,app,lambda:True,lambda:CLI(),install_fn=install)
        install.assert_called_once()
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ident,)).fetchone()[0],'awaiting_live_verification')

    def test_diagnose_busy_browser_then_resume_tested_patch_once(self):
        ident=self.confirmed()
        app=self.fixture()
        proposal=self.proposal()
        calls=[]
        class CLI:
            def structured(self,*_):
                calls.append('codex')
                return proposal
        with patch.object(r,'probe_sandbox',return_value=True), patch.object(r,'run_sandbox',side_effect=[{'passed':False},{'passed':True}]), patch.object(r,'install') as install:
            r.run_one(self.queue,app,lambda:False,lambda:CLI(),install_fn=install)
            install.assert_not_called()
            self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ident,)).fetchone()[0],'awaiting_idle')
            self.now+=301
            r.run_one(self.queue,app,lambda:True,lambda:self.fail('No second Codex call'),install_fn=install)
            install.assert_called_once()
            r.run_one(self.queue,app,lambda:True,lambda:self.fail('No replay'),install_fn=install)
            install.assert_called_once()
        self.assertEqual(calls,['codex'])

    def test_resolved_tested_job_is_not_applied(self):
        ident=self.confirmed()
        self.queue.update(ident,'awaiting_idle',{})
        self.queue.observe('extension',{}, {'checked_at':200})
        self.assertFalse(r.run_one(self.queue,idle_check=lambda:True,cli_factory=lambda:self.fail('No stale repair')))
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ident,)).fetchone()[0],'resolved_without_repair')

    def test_modified_stage_cannot_be_resumed(self):
        ident=self.confirmed()
        app=self.fixture()
        proposal=self.proposal()
        class CLI:
            def structured(self,*_):return proposal
        with patch.object(r,'probe_sandbox',return_value=True), patch.object(r,'run_sandbox',side_effect=[{'passed':False},{'passed':True}]), patch.object(r,'install') as install:
            r.run_one(self.queue,app,lambda:False,lambda:CLI(),install_fn=install)
            (self.root/'queue'/ident/'attempt-1/stage/iva_integration/threads_page.py').write_text('changed later')
            self.now+=301
            r.run_one(self.queue,app,lambda:True,lambda:self.fail('No repeated Codex call'),install_fn=install)
            install.assert_not_called()
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs WHERE id=?',(ident,)).fetchone()[0],'review_required')

    def test_install_rechecks_idle_and_original_hashes(self):
        app=self.fixture()
        stage=self.root/'stage'
        r.stage_sources(app,stage)
        hashes={name:r.digest((app/name).read_bytes()) for name in r.ALLOWED}
        changed=r.apply_proposal(stage,self.proposal())
        directory=self.root/'attempt';directory.mkdir()
        with self.assertRaisesRegex(RuntimeError,'browser_busy'):
            r.install(app,stage,changed,hashes,directory,lambda:False,nullcontext)
        (app/'iva_integration/threads_page.py').write_text('new concurrent code')
        with self.assertRaisesRegex(RuntimeError,'source_changed'):
            r.install(app,stage,changed,hashes,directory,lambda:True,nullcontext)

    def test_install_saves_backup_and_verified_hash_manifest(self):
        app=self.fixture()
        stage=self.root/'stage'
        r.stage_sources(app,stage)
        hashes={name:r.digest((app/name).read_bytes()) for name in r.ALLOWED}
        changed=r.apply_proposal(stage,self.proposal())
        directory=self.root/'attempt';directory.mkdir()
        r.install(app,stage,changed,hashes,directory,lambda:True,nullcontext)
        self.assertIn('return 2',(app/'iva_integration/threads_page.py').read_text())
        self.assertIn('return 1',(directory/'before/iva_integration__threads_page.py').read_text())
        self.assertEqual(json.loads((directory/'install-manifest.json').read_text())['state'],'installed_verified')


class RepairResearchPriorityTests(unittest.TestCase):
    def state(self,status='idle'):
        return {'ok':True,'research':{'status':status,'active':status=='active','granted':status=='active'}}

    def test_new_diagnosis_defers_to_granted_and_waiting_research(self):
        for status in ('active','waiting_background','waiting_interactive','waiting_owner_lock'):
            with self.subTest(status=status), patch.object(Path,'read_text') as memory:
                self.assertFalse(r.resources_available(lambda:self.state(status)))
                memory.assert_not_called()

    def test_coordinator_unknown_fails_closed_but_idle_still_requires_memory(self):
        for state in ({},None,{'ok':True},{'ok':False,'research':self.state()['research']}):
            self.assertFalse(r.resources_available(lambda:state))
        def unavailable():raise OSError('coordinator down')
        self.assertFalse(r.resources_available(unavailable))
        with patch.object(Path,'read_text',return_value='MemAvailable: 600000 kB\n'):
            self.assertTrue(r.resources_available(self.state))
        with patch.object(Path,'read_text',return_value='MemAvailable: 100000 kB\n'):
            self.assertFalse(r.resources_available(self.state))

    def test_deferred_repair_keeps_attempt_and_queue_without_starting_codex(self):
        with tempfile.TemporaryDirectory() as temporary:
            queue=r.Queue(Path(temporary)/'queue',lambda:1000)
            try:
                for stamp in (1,2,3):
                    ids=queue.observe('extension',{'inbox:messages':{'code':'messages'}},
                                      {'checked_at':stamp,'inbox_checked_at':stamp})
                ident=ids[0]
                self.assertFalse(r.run_one(queue,idle_check=lambda:True,
                    cli_factory=lambda:self.fail('No diagnosis during research'),
                    resources_check=lambda:r.resources_available(lambda:self.state('active'))))
                row=queue.db.execute('SELECT state,attempts FROM jobs WHERE id=?',(ident,)).fetchone()
                self.assertEqual(tuple(row),('pending',0))
                self.assertFalse((queue.root/ident).exists())
            finally:
                queue.close()


if __name__=='__main__':
    unittest.main()
