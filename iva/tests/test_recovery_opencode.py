import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iva_integration import recovery_opencode as adapter


def event(index=1, verified=1200):
    return {'id': 'opencode:service_restart:' + format(index, '032x'),
            'source': 'opencode', 'code': 'provider_unavailable', 'action': 'service_restart',
            'verified_at': verified,
            'proof': {'attempted_at': verified - 5, 'restart_succeeded': True,
                      'global_healthy': True, 'provider_available': True, 'provider_503_confirmed': True}}


class RecoveryOpenCode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'events.json'
        self.trusted = patch.object(adapter, '_trusted', return_value=True)
        self.trusted.start()
        self.addCleanup(self.trusted.stop)
        self.previous = {'version': 1, 'baseline_at': 1000}

    def write(self, **kwargs):
        self.path.write_text(json.dumps({'version': 1, 'checked_at': 1250, 'observation': 'healthy',
                                       'global_http_status': 200, 'provider_http_status': 200,
                                       'events': [event()], **kwargs}), encoding='utf-8')

    def collect(self, now=1300):
        return adapter.collect(self.path, self.previous, now)

    def test_installation_baselines_even_with_old_or_missing_export(self):
        for present in (False, True):
            if present:
                self.write()
            result = adapter.collect(self.path, None, 1300)
            self.assertEqual(result, {'state': {'version': 1, 'baseline_at': 1300}, 'events': []})
            self.previous = result['state']
            self.assertEqual(self.collect(1350)['events'], [])

    def test_verified_event_has_stable_id_and_caller_outbox_owns_deduplication(self):
        self.write()
        result = self.collect()
        self.assertEqual(result['events'], [event()])
        self.assertEqual(self.collect(1400)['events'], result['events'])
        self.assertEqual(result['state'], self.previous)

    def test_history_after_baseline_survives_later_healthy_ticks(self):
        self.write(checked_at=9000)
        self.assertEqual(self.collect(9050)['events'], [event()])
        self.write(events=[event(verified=900)])
        self.assertEqual(self.collect()['events'], [])

    def test_no_event_for_unhealthy_unknown_stale_future_or_partial_proof(self):
        for change in ({'observation': 'provider_503'}, {'provider_http_status': 503},
                       {'global_http_status': None}, {'checked_at': 900},
                       {'checked_at': 1400}, {'version': 2}, {'events': [event(verified=1400)]}):
            self.write(**change)
            self.assertEqual(self.collect()['events'], [], change)
        for key in event()['proof']:
            item = event()
            item['proof'].pop(key)
            self.write(events=[item])
            self.assertEqual(self.collect()['events'], [], key)
        self.write(events=[], last_restart_result='succeeded')
        self.assertEqual(self.collect()['events'], [])

    def test_only_known_evidence_is_exposed_and_duplicate_ids_collapse(self):
        item = event()
        item['private_body'] = 'secret'
        item['proof']['token'] = 'secret'
        self.write(events=[item, item])
        result = self.collect()
        self.assertEqual(result['events'], [event()])
        self.assertNotIn('secret', json.dumps(result))

    def test_missing_corrupt_oversized_or_untrusted_file_keeps_baseline(self):
        for raw in ('{broken', '[]', 'x' * 17000):
            self.path.write_text(raw)
            self.assertEqual(self.collect(), {'state': self.previous, 'events': []})
        self.path.unlink()
        self.assertEqual(self.collect()['events'], [])
        self.write()
        with patch.object(adapter, '_trusted', return_value=False):
            self.assertEqual(self.collect()['events'], [])

    @unittest.skipUnless(os.name == 'posix', 'Unix symlink fixture')
    def test_symlink_source_is_not_followed(self):
        self.write()
        link = self.path.parent / 'link.json'
        link.symlink_to(self.path)
        self.assertEqual(adapter.collect(link, self.previous, 1300)['events'], [])

    def test_invalid_clock_resets_baseline_or_rejects_non_time(self):
        self.write()
        self.previous = {'version': 1, 'baseline_at': 2000}
        self.assertEqual(self.collect()['events'], [])
        self.assertEqual(self.collect()['state']['baseline_at'], 1300)
        for value in (None, True, float('nan'), -1):
            with self.assertRaises(ValueError):
                self.collect(value)


if __name__ == '__main__':
    unittest.main()
