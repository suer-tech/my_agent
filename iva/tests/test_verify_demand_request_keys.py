import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from deploy import verify_demand_request_keys as probe
from tests import test_demand_request_keys as fixture


class HistoricalDuplicateProbeTests(unittest.TestCase):
    setUp = fixture.RequestKeyBindingTests.setUp
    report = fixture.RequestKeyBindingTests.report

    def prepare(self):
        self.report(self.requests)
        with self.store.db:
            self.store.db.execute("UPDATE demand_agent_tasks SET status='done',result='{}'")
            self.store.db.execute("UPDATE demand_agent_tasks SET status='failed' WHERE id=?", (self.parent['id'],))
            self.store.db.execute("UPDATE demand_agent_plans SET status='limited'")
            self.store.db.execute("UPDATE demand_cycles SET state='completed_with_gaps',stage='done',result='{}'")
        self.store.close()
        target = Path(self.directory.name) / 'probe'
        prepared = probe.prepare_case(self.path, target, 'cycle', self.parent['id'], False)
        return prepared, target

    def test_isolated_replay_uses_real_stage_and_preserves_source_database(self):
        prepared, target = self.prepare()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        client = fixture.ContractCli()
        client.last_execution = {'model': 'unit-test-only', 'reasoning_effort': 'medium', 'read_only': True, 'web_search': 'disabled'}
        client.last_web_events = []
        with patch.object(probe, 'CYCLE', 'cycle'), patch.object(probe, 'TASK', self.parent['id']):
            report = probe.run_fixture(prepared, target, client)
        self.assertTrue(report['passed'], report['criteria'])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(len(report['decisions']), 2)
        self.assertFalse(report['full_cycle'])
        self.assertEqual(json.loads((target / 'report.json').read_text(encoding='utf-8')), report)

    def test_invalid_quote_is_rejected_before_any_native_call(self):
        self.requests[1]['trigger_quote'] = 'This made-up text is absent from all the recorded sources.'
        prepared, target = self.prepare()
        client = fixture.ContractCli()
        with patch.object(probe, 'CYCLE', 'cycle'), patch.object(probe, 'TASK', self.parent['id']), \
             self.assertRaisesRegex(RuntimeError, 'unplanned_quote_repair'):
            probe.run_fixture(prepared, target, client)
        self.assertEqual(client.calls, [])


if __name__ == '__main__':
    unittest.main()
