"""Probe evidence distinguishes real transport behavior from a successful read."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from deploy import verify_demand_403 as probe
from iva_integration import demand_network as network


@contextmanager
def unlocked():
    yield


def denied(url=probe.URL, category='access_denied', status=403):
    return {'ok': False, 'url': url, 'canonical_url': url, 'http_status': status,
            'error_category': category, 'text': '', 'content_hash': None}


def readable(url=probe.URL):
    text = 'A public forum contributor explains manually updating inventory across the retail shop and online sales. '
    return {'ok': True, 'url': url, 'canonical_url': url, 'http_status': 200,
            'text': text, 'content_hash': hashlib.sha256(text.encode()).hexdigest()}


class Http403Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.out = self.base / 'probe'; self.out.mkdir()
        self.cache = self.base / 'production'; self.cache.mkdir()
        self.original = b'{"version":1,"hosts":{}}'
        (self.cache / 'routes.json').write_bytes(self.original)
        for name, value in [('STATE', self.cache), ('enabled', lambda: True), ('_locked', unlocked)]:
            current = patch.object(network, name, value); current.start(); self.addCleanup(current.stop)

    def test_actual_denial_callback_and_successful_vpn_are_proved_without_touching_cache(self):
        direct = Mock(side_effect=lambda url: denied(url)); vpn = Mock(side_effect=lambda url: readable(url))
        report = probe.run_probe(self.out, direct, vpn)
        self.assertTrue(report['passed'], report['criteria'])
        direct.assert_called_once_with(probe.URL); vpn.assert_called_once_with(probe.URL)
        self.assertTrue(report['source_read_through_vpn'])
        self.assertEqual((self.cache / 'routes.json').read_bytes(), self.original)
        self.assertEqual(json.loads((self.out / 'report.json').read_text())['passed'], True)

    def test_failed_vpn_still_proves_bounded_retry_but_not_readability(self):
        direct = Mock(side_effect=lambda url: denied(url)); vpn = Mock(side_effect=lambda url: denied(url))
        report = probe.run_probe(self.out, direct, vpn)
        self.assertTrue(report['passed'], report['criteria'])
        self.assertFalse(report['source_read_through_vpn'])
        self.assertEqual(report['final']['verified_text_chars'], 0)
        self.assertEqual(report['isolated_status']['cooldown_domains'], 1)
        self.assertEqual(vpn.call_count, 1)

    def test_direct_success_cannot_be_reported_as_a_403_retry(self):
        vpn = Mock()
        report = probe.run_probe(self.out, lambda url: readable(url), vpn)
        self.assertFalse(report['passed'])
        self.assertFalse(report['criteria']['real_direct_ordinary_403_observed'])
        self.assertFalse(report['source_read_through_vpn'])
        vpn.assert_not_called()

    def test_challenge_is_not_misrepresented_as_an_ordinary_403(self):
        vpn = Mock()
        report = probe.run_probe(self.out, lambda url: denied(url, 'access_challenge'), vpn)
        self.assertFalse(report['passed'])
        vpn.assert_not_called()

    def test_corrupt_success_body_does_not_pass_probe_content_gate(self):
        bad = readable(); bad['content_hash'] = 'unverified'
        report = probe.run_probe(self.out, lambda url: denied(url), lambda url: bad)
        self.assertFalse(report['passed'])
        self.assertFalse(report['criteria']['success_has_verified_content_or_failure_remains_gap'])

    def test_reader_transport_failure_is_not_a_real_vpn_http_response(self):
        report = probe.run_probe(self.out, lambda url: denied(url), Mock(side_effect=OSError('private diagnostic')))
        self.assertFalse(report['passed'])
        self.assertFalse(report['criteria']['vpn_http_response_observed'])
        self.assertEqual(len(report['vpn']), 1)
        self.assertNotIn('private diagnostic', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
