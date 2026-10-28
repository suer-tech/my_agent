from contextlib import contextmanager
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import hr_campaign as hr
from local_finance.codex_cli import CliError
from worker.store import Store


class Model:
    last_usage={}
    def __init__(self,outcomes):self.outcomes=iter(outcomes);self.calls=0
    def structured(self,*args):
        self.calls+=1;value=next(self.outcomes)
        if isinstance(value,Exception):raise value
        return value


class Runtime:
    blocked=True
    @contextmanager
    def lease(self,run_id):yield
    def search(self,*args):
        if self.blocked:raise hr.ModelAuthorizationDeferred()
        return {'companies':[]}
    def followup(self,*args):raise hr.ModelAuthorizationDeferred()
    def repair_quotes(self,*args):raise hr.ModelAuthorizationDeferred()


class HrAuthorizationTests(unittest.TestCase):
    def runtime(self):
        runtime=object.__new__(hr.HrRuntime);runtime.product='test';return runtime

    def test_busy_before_start_can_wait_then_succeed_without_duplicate_inference(self):
        model=Model([CliError(hr.AUTH_BUSY),{'companies':[]}])
        with patch.object(hr.time,'sleep'),patch.object(hr.time,'monotonic',side_effect=[0,1]):
            self.assertEqual(self.runtime().ask(model,'prompt',{},{}),{'companies':[]})
        self.assertEqual(model.calls,2)

    def test_busy_deadline_becomes_resource_pause(self):
        model=Model([CliError(hr.AUTH_BUSY)])
        with patch.object(hr.time,'monotonic',side_effect=[0,61]):
            with self.assertRaises(hr.ModelAuthorizationDeferred):self.runtime().ask(model,'prompt',{}, {})

    def test_inherited_quote_repair_uses_the_same_authorization_wait(self):
        with patch.object(hr.IvaRuntime,'repair_quotes',side_effect=[CliError(hr.AUTH_BUSY),{'signal_quote':'literal'}]) as repair:
            with patch.object(hr.time,'sleep'),patch.object(hr.time,'monotonic',side_effect=[0,1]):
                self.assertEqual(self.runtime().repair_quotes({}, {}, [], []),{'signal_quote':'literal'})
            self.assertEqual(repair.call_count,2)

    def test_other_failed_invocations_keep_their_reservations(self):
        store,runtime,engine=self.fixture()
        with patch.object(runtime,'search',side_effect=CliError('Research CLI exceeded its time budget')):
            engine.run('slot')
        row=store.rows('SELECT * FROM runs')[0];query=store.rows('SELECT * FROM queries')[0]
        self.assertEqual(row['state'],'failed');self.assertEqual(row['model_calls'],1)
        self.assertEqual(query['attempts'],1)

    def test_timeout_login_quota_or_other_errors_are_not_retried(self):
        for error in ['Research CLI exceeded its time budget','Research CLI login is unavailable',
                      'Research model limit reached; retry later','Some private provider error']:
            model=Model([CliError(error)])
            with self.subTest(error=error),self.assertRaises(CliError):self.runtime().ask(model,'prompt',{}, {})
            self.assertEqual(model.calls,1)

    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        profile=root/'product-brief.json';profile.write_text('{}');(root/'seed-companies.json').write_text('[]')
        self.profile_patch=patch.object(hr,'PROFILE',profile);self.profile_patch.start();self.addCleanup(self.profile_patch.stop)
        config={'campaign':'hr-test','database':str(root/'state.sqlite3'),'queries':['поиск HR компаний для резюме'],
                'max_candidates':1,'max_pages':6,'max_model_calls':12,'max_signal_age_days':180,
                'timezone':'UTC','enabled':True,'mode':'preview','discovery_outreach':True}
        store=Store(config['database']);self.addCleanup(store.close)
        runtime=Runtime();engine=hr.HrEngine(config,store,runtime)
        return store,runtime,engine

    def test_pause_preserves_query_quota_and_budget_and_resumes_same_run(self):
        store,runtime,engine=self.fixture();result=engine.run('slot')
        row=store.rows('SELECT * FROM runs')[0];query=store.rows('SELECT * FROM queries')[0]
        self.assertEqual(row['state'],'waiting_resources');self.assertEqual(row['model_calls'],0)
        self.assertEqual(query['attempts'],0);self.assertEqual(query['state'],'pending')
        self.assertIn('search_deferred_authorization_busy',[r['code'] for r in store.rows('SELECT code FROM events')])
        runtime.blocked=False;result=engine.run('later-slot')
        self.assertTrue(result['ok']);self.assertEqual(len(store.rows('SELECT * FROM runs')),1)
        self.assertEqual(store.run(row['id'])['model_calls'],1)
        self.assertEqual(store.rows('SELECT attempts FROM queries')[0]['attempts'],1)

    def test_unstarted_followup_and_citation_remain_resumable(self):
        store,runtime,engine=self.fixture();row=store.start(engine.config,'slot',engine.config_fingerprint())
        company={'company_domain':'example.org'}
        with store.db:
            store.db.execute('INSERT INTO candidates(run_id,domain,data) VALUES(?,?,?)',(row['id'],'example.org',json.dumps(company)))
        for method,column,stage in [('followup','followup_attempts','followup_search'),('repair_quotes','citation_attempts','assessment_checked')]:
            store.update_candidate(row['id'],'example.org',**{column:1})
            with self.assertRaises(hr.ModelAuthorizationDeferred):engine.model(row['id'],method,company)
            candidate=store.rows('SELECT * FROM candidates')[0]
            self.assertEqual(candidate[column],0);self.assertEqual(candidate['stage'],stage)
            self.assertEqual(store.run(row['id'])['model_calls'],0)
