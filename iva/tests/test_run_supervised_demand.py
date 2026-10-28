from contextlib import nullcontext, redirect_stdout
import copy
import json
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

from deploy import run_supervised_demand as runner


def cycle(job, identity='cycle-one', state='running'):
    from iva_integration import demand_scope
    topic = 'software fixture'
    return {'id': identity, 'job_id': job, 'sequence': 0, 'track': 'software', 'topic': topic,
            'stage': 'agent_plan' if state == 'running' else 'done', 'state': state,
            'plan': {'version': 3, 'scope': demand_scope.contract('software', topic)}}


def result(more=False, **changes):
    value = {'ok': True, 'cycle_id': 'cycle-one', 'has_more': more,
             'status': 'in_progress' if more else 'completed', 'stage': 'fetch' if more else 'done',
             'cursor': {'research_lease_token': 'private-token'} if more else {},
             'cards_persisted': not more,
             'memory_export': {'ok': True, 'errors': [], 'conflicts': [], 'has_more': False}}
    return {**value, **changes}


class FakeRuntime:
    def __init__(self, directory):
        self.directory = directory
        self.now = 1000
        self.stop = False
        self.records = []
        self.calls = []
        self.releases = []
        self.waits = []
        self.statuses = [{'ok': True, 'status': 'idle', 'active': False, 'granted': False}]
        self.results = [result()]
        self.action = None

    def clock(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds

    def status(self):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def cycles(self, job):
        return copy.deepcopy(self.records)

    def validate(self, record):
        runner.Runtime.validate(self, record)

    def deployment_guard(self):
        return nullcontext()

    def step(self, state):
        persisted = json.loads((self.directory / 'state.json').read_text())
        assert persisted['state'] == 'in_flight'
        assert persisted['job_id'] == state['job_id']
        self.calls.append(copy.deepcopy(state))
        if not self.records:
            self.records = [cycle(state['job_id'])]
        if self.action:
            return self.action(state)
        value = self.results.pop(0)
        if not value.get('has_more'):
            self.records[0]['state'] = value['status']
        return copy.deepcopy(value)

    def release(self):
        state = json.loads((self.directory / 'state.json').read_text())
        assert state['state'] in runner.TERMINAL | {'suspended'}
        self.releases.append(state)
        return True


class SupervisedDemand(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.runtime = FakeRuntime(self.directory)

    def run_job(self):
        with redirect_stdout(io.StringIO()):
            return runner.run(self.directory, self.runtime)

    def state(self):
        return json.loads((self.directory / 'state.json').read_text())

    def test_one_terminal_cycle_is_not_started_again_even_in_later_hour(self):
        first = self.run_job()
        self.assertEqual(first['state'], 'completed')
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(len(self.runtime.records), 1)
        self.assertTrue((self.directory / 'final-result.json').exists())
        self.runtime.now += 7200
        second = self.run_job()
        self.assertEqual(second['cycle_id'], first['cycle_id'])
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(len(self.runtime.releases), 1)

    def test_retains_lease_and_anchor_through_checkpoints_and_retries(self):
        self.runtime.results = [result(True), result(True, ok=False, status='retrying', retry_after_seconds=60), result()]
        output = self.run_job()
        self.assertEqual(output['state'], 'completed')
        self.assertEqual(len(self.runtime.releases), 1)
        self.assertEqual(self.runtime.waits, [30, 30])
        self.assertEqual(self.runtime.calls[1]['cursor']['demand_cycle_id'], 'cycle-one')
        self.assertEqual(self.runtime.calls[1]['cursor']['research_lease_token'], 'private-token')
        self.assertEqual(self.state()['cycle_id'], 'cycle-one')

    def test_first_call_crash_after_cycle_commit_recovers_anchor_across_hours(self):
        def fail(state):
            self.runtime.records[0]['state'] = 'completed'
            raise RuntimeError('private model detail')

        self.runtime.action = fail
        first = self.run_job()
        job = first['job_id']
        self.assertEqual(first['state'], 'suspended')
        self.assertEqual(first['steps'], 0)
        self.runtime.now += 7200
        self.runtime.action = None
        second = self.run_job()
        self.assertEqual(second['state'], 'completed')
        self.assertEqual(second['job_id'], job)
        self.assertEqual(self.runtime.calls[1]['cursor']['demand_cycle_id'], 'cycle-one')
        self.assertEqual(len(self.runtime.records), 1)
        self.assertNotIn('private model detail', json.dumps(first))

    def test_other_research_is_waited_out_without_acquiring_or_releasing_it(self):
        self.runtime.statuses.insert(0, {'ok': True, 'status': 'active', 'job_id': 'commerce-other', 'active': True})
        output = self.run_job()
        self.assertEqual(output['state'], 'completed')
        self.assertEqual(self.runtime.waits, [30])
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(len(self.runtime.releases), 1)

    def test_resume_can_reclaim_its_own_dead_process_lease(self):
        state = runner.load(self.directory, self.runtime.clock())
        self.runtime.statuses = [{'ok': True, 'status': 'active', 'job_id': state['job_id'], 'owner_alive': False}]
        self.assertEqual(self.run_job()['state'], 'completed')
        self.assertEqual(self.runtime.waits, [])

    def test_resource_suspension_keeps_checkpoint_and_releases_after_persist(self):
        self.runtime.results = [result(True, ok=False, status='resource_blocked',
                                       retry_after_seconds=900, cursor={'demand_cycle_id': 'cycle-one'})]
        output = self.run_job()
        self.assertEqual(output['state'], 'suspended')
        self.assertEqual(output['retry_at'], 1900)
        self.assertEqual(self.runtime.releases[0]['cursor']['demand_cycle_id'], 'cycle-one')
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertFalse((self.directory / 'final-result.json').exists())

    def test_wrong_lane_or_second_cycle_blocks_models(self):
        for kind in ('lane', 'second'):
            state = runner.load(self.directory, self.runtime.clock())
            record = cycle(state['job_id'])
            if kind == 'lane':
                record['track'] = 'b2c'
                self.runtime.records = [record]
            else:
                self.runtime.records = [record, cycle(state['job_id'], 'cycle-two')]
            self.assertEqual(self.run_job()['state'], 'suspended')
            self.assertEqual(self.runtime.calls, [])

    def test_failed_or_unconfirmed_terminal_result_is_not_success(self):
        self.runtime.results = [result(memory_export=None)]
        self.assertEqual(self.run_job()['state'], 'suspended')
        self.assertEqual(self.state()['cursor']['demand_cycle_id'], 'cycle-one')
        self.assertFalse((self.directory / 'final-result.json').exists())

    def test_summary_never_exposes_private_cursor_or_lease_token(self):
        self.runtime.results = [result(True, ok=False, status='resource_blocked')]
        output = self.run_job()
        self.assertNotIn('private-token', json.dumps(output))
        self.assertNotIn('private-token', (self.directory / 'status.json').read_text())
        self.assertNotIn('cursor', output)

    def test_completed_with_gaps_stays_explicit(self):
        self.runtime.results = [result(status='completed_with_gaps')]
        self.assertEqual(self.run_job()['state'], 'completed_with_gaps')

    def test_actual_cycle_lookup_is_readonly(self):
        path = self.directory / 'research.sqlite3'
        connection = sqlite3.connect(path)
        try:
            connection.execute('CREATE TABLE demand_cycles(id,job_id,sequence,track,topic,plan,state,stage)')
            connection.execute("INSERT INTO demand_cycles VALUES('a','job',0,'software','topic','{}','running','agent_plan')")
            connection.commit()
        finally:
            connection.close()
        before = path.read_bytes()
        rows = runner.Runtime(path).cycles('job')
        self.assertEqual(rows[0]['id'], 'a')
        self.assertEqual(path.read_bytes(), before)

    def test_real_fresh_cycle_has_compatible_software_scope_and_anchor(self):
        from iva_integration.demand_research import Research
        path = self.directory / 'real-shape.sqlite3'
        state = runner.load(self.directory, 1000)
        research = Research(path, self.directory / 'cards')
        try:
            actual = research.cycle(state['job_id'], runner.TASK)
        finally:
            research.close()
        self.assertEqual(actual['sequence'], 0)
        self.assertEqual(actual['track'], 'software')
        runtime = runner.Runtime(path)
        runtime.validate(actual)
        runner.anchor(state, runtime)
        self.assertEqual(state['cycle_id'], actual['id'])
        self.assertEqual(state['cursor']['demand_cycle_id'], actual['id'])

    def test_attempt_paths_are_fixed_and_reject_arbitrary_or_out_of_range_values(self):
        self.assertEqual(runner.output_path(1), runner.OUTPUT)
        self.assertEqual(runner.output_path(2), runner.OUTPUT.with_name(runner.OUTPUT.name + '-attempt2'))
        self.assertEqual(len({runner.output_path(attempt) for attempt in range(1, 10)}), 9)
        for value in (0, 10, -1, True, '../other', 2.0, None):
            with self.assertRaises(ValueError):
                runner.output_path(value)

    def test_second_attempt_has_independent_job_cycle_and_files_preserving_first(self):
        first = self.run_job()
        first_bytes = {path.name: path.read_bytes() for path in self.directory.iterdir() if path.is_file()}
        second_dir = self.directory / 'attempt2'
        second_dir.mkdir()
        runtime = FakeRuntime(second_dir)
        with redirect_stdout(io.StringIO()):
            second = runner.run(second_dir, runtime, attempt=2)
            repeated = runner.run(second_dir, runtime, attempt=2)
        self.assertEqual(second['attempt'], 2)
        self.assertRegex(second['job_id'], r'^supervised-software-a2-[0-9a-f]{24}$')
        self.assertNotEqual(second['job_id'], first['job_id'])
        self.assertEqual(second['cycle_id'], repeated['cycle_id'])
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(first_bytes, {path.name: path.read_bytes() for path in self.directory.iterdir() if path.is_file()})
        with self.assertRaisesRegex(RuntimeError, 'checkpoint_invalid'):
            runner.load(self.directory, 2000, attempt=2)

    def test_legacy_first_attempt_state_is_accepted_without_new_job(self):
        original = runner.load(self.directory, 1000)
        original.pop('attempt')
        runner.atomic(self.directory / 'state.json', original)
        resumed = runner.load(self.directory, 2000, attempt=1)
        self.assertEqual(resumed['job_id'], original['job_id'])
        self.assertEqual(resumed['attempt'], 1)


if __name__ == '__main__':
    unittest.main()
