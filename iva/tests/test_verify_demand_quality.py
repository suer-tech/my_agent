import copy
import json
from pathlib import Path
import tempfile
import unittest

from deploy import verify_demand_quality as diagnostic
from tests.test_demand_agents import research_plan as fixture_plan


class QualityProbe(unittest.TestCase):
    def test_plan_requires_substantive_four_roles_and_persisted_validation(self):
        plan = fixture_plan()
        self.assertTrue(diagnostic.validated_plan({"plan": plan}, [{"event": "plan_validated"}]))
        self.assertFalse(diagnostic.validated_plan({"plan": plan}, []))
        bad = copy.deepcopy(plan)
        bad["researchers"][0]["methods"] = "x" * 100
        self.assertFalse(diagnostic.validated_plan({"plan": bad}, [{"event": "plan_validated"}]))

    def test_shared_settings_fingerprint_contains_no_contents_or_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "data/iva-model.json").write_text('SECRET_PROFILE')
            (root / "auth.json").write_text('SECRET_AUTH')
            before = diagnostic.fingerprints(root, root)
            self.assertNotIn('SECRET', json.dumps(before))
            self.assertEqual(set(before), {"iva_model", "iva_agents", "codex_config"})
            (root / "auth.json").write_text('REFRESHED_AUTH')
            self.assertEqual(before, diagnostic.fingerprints(root, root))
            (root / "data/iva-model.json").write_text('CHANGED')
            self.assertNotEqual(before, diagnostic.fingerprints(root, root))

    def test_fixed_paths_and_fresh_only_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / 'release'
            release.mkdir()
            database, cards, state_path = diagnostic.paths(release, release / 'verification-quality')
            state = diagnostic.prepare(database, cards, state_path)
            self.assertFalse(state['full_cycle'])
            self.assertEqual(state['steps'], 0)
            with self.assertRaisesRegex(RuntimeError, 'fresh_database'):
                diagnostic.prepare(database, cards, state_path)
            with self.assertRaisesRegex(ValueError, 'unexpected_quality_directory'):
                diagnostic.paths(release, release / 'live')

    def test_single_step_accepts_actual_execution_not_requested_attribute(self):
        class Client:
            reasoning_effort = 'high'
            last_web_events = []

            def __init__(self, actual):
                self.actual = actual
                self.calls = 0

            def structured(self, prompt, schema):
                actual = self.actual[self.calls]
                self.calls += 1
                self.last_execution = {'model': 'test-only', 'reasoning_effort': actual,
                                       'read_only': True, 'web_search': 'disabled'}
                if 'scope_decisions' in schema['properties']:
                    return {'scope_id': schema['properties']['scope_id']['enum'][0],
                            'scope_decisions': [{'key': key, 'relationship': 'same_problem',
                                                'reason': 'The isolated fixture concerns the same software process and buyer.'}
                                for key in schema['properties']['scope_decisions']['items']['properties']['key']['enum']]}
                return fixture_plan()

        with tempfile.TemporaryDirectory() as temp:
            for actual, expected in [(('low', 'medium'), False), (('medium', 'low'), False),
                                     (('medium', 'medium'), True)]:
                with self.subTest(actual=actual):
                    release = Path(temp) / '-'.join(actual)
                    release.mkdir()
                    database, cards, state_path = diagnostic.paths(release, release / 'verification-quality')
                    state = diagnostic.prepare(database, cards, state_path)
                    value = diagnostic.run_planner(state, database, cards, Client(actual))
                    self.assertEqual(value['structured_call_count'], 2)
                    self.assertEqual(value['passed'], expected)
                    self.assertTrue(value['criteria']['planner_and_independent_scope_audit_completed'])
                    self.assertEqual([row['execution']['reasoning_effort'] for row in value['structured_calls']], list(actual))
                    self.assertTrue(value['criteria']['schema_and_substantive_plan_validated'])
                    self.assertTrue(value['criteria']['only_planner_step_completed'])
                    self.assertFalse(value['full_cycle'])


if __name__ == '__main__':
    unittest.main()
