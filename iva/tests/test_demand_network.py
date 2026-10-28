"""Bounded HTTP403/geography routing and public VPN RPC, without internet I/O."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from iva_integration import demand_network as network
from iva_integration import demand_sources as sources
from tests.test_demand_sources import Connection, Response


SERVICE_PATH = Path(__file__).resolve().parents[1] / 'deploy/research_fetch_service.py'
SPEC = importlib.util.spec_from_file_location('research_fetch_service_test', SERVICE_PATH)
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)
URL = 'https://research.example/public-page'
TEXT = 'A public customer describes repeated manual reconciliation and its cost. ' * 3


def success(url=URL):
    return {'ok': True, 'url': url, 'canonical_url': url, 'text': TEXT,
            'title': 'Public customer story', 'status': 'ok', 'http_status': 200,
            'content_hash': hashlib.sha256(TEXT.encode()).hexdigest()}


def blocked(category='geographic_restriction', url=URL):
    return {'ok': False, 'url': url, 'canonical_url': url, 'text': '', 'title': '',
            'content_hash': None, 'status': 'blocked', 'http_status': 403,
            'error_category': category}


@contextmanager
def unlocked():
    yield


class Restrictions(unittest.TestCase):
    def test_only_explicit_geographic_messages_are_classified_as_geography(self):
        cases = [(403, 'This service is not available in your country.', ''),
                 (451, 'Requests from your region are blocked.', ''),
                 (200, 'Доступ к сервису ограничен в вашей стране.', 'Доступ ограничен')]
        for status, text, title in cases:
            with self.subTest(status=status):
                self.assertEqual(network.restriction(status, text, title), 'geographic_restriction')
        self.assertEqual(network.restriction(403, 'Forbidden'), 'access_denied')
        self.assertEqual(network.restriction(451, 'Unavailable for legal reasons'), 'legal_restriction')
        self.assertIsNone(network.restriction(500, 'not available in your country'))

    def test_auth_rate_limits_and_antibot_take_priority_over_geo_words(self):
        self.assertEqual(network.restriction(401, 'not available in your country'), 'authentication_required')
        self.assertEqual(network.restriction(429, 'not available in your country'), 'rate_limited')
        for phrase in ('CAPTCHA', 'blocked by network security', 'automated requests',
                       'verify you are human', 'sign in to continue'):
            with self.subTest(phrase=phrase):
                self.assertEqual(network.restriction(403, phrase + '; not available in your country'), 'access_challenge')

    def test_normal_article_about_captcha_and_geography_is_not_a_block_page(self):
        text = 'Our article compares CAPTCHA products and discusses automated access and geo-restricted pages. '
        self.assertIsNone(network.restriction(200, text, 'Comparing access products'))
        self.assertIsNone(network.restriction(200, 'A case study says their app is not available in your country.', 'Customer case study'))

    def test_titleless_explicit_human_challenge_is_not_evidence(self):
        body = '<p>Please verify you are human before continuing to this site.</p>'
        with patch.object(sources, '_addresses', return_value=['8.8.8.8']), \
                patch.object(sources, '_connection', return_value=Connection(Response(body))):
            result = sources._fetch_source_direct(URL)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_category'], 'access_challenge')
        self.assertEqual(result['text'], '')
        self.assertIsNone(result['content_hash'])

    def test_geographic_denial_with_http_200_is_not_evidence(self):
        title = 'Not available in your country'
        text = 'We are sorry. This service is not available in your country. Please contact support for details.'
        with patch.object(sources, '_addresses', return_value=['8.8.8.8']), \
                patch.object(sources, '_connection', return_value=Connection(Response('<title>' + title + '</title><p>' + text + '</p>'))):
            result = sources._fetch_source_direct(URL)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_category'], 'geographic_restriction')
        self.assertEqual(result['text'], '')
        self.assertIsNone(result['content_hash'])
        self.assertIsNone(network.restriction(200, text, 'Not available in your country: troubleshooting guide'))

    def test_direct_reader_extracts_explicit_geo_error_html_without_using_it_as_evidence(self):
        response = Response('<title>Access denied</title><p>This service is not available in your <strong>country</strong>.</p>', status=403)
        connection = Connection(response)
        with patch.object(sources, '_addresses', return_value=['8.8.8.8']), \
                patch.object(sources, '_connection', return_value=connection):
            result = sources._fetch_source_direct(URL)
        self.assertEqual(result['error_category'], 'geographic_restriction')
        self.assertEqual(result['text'], '')
        self.assertIsNone(result['content_hash'])
        self.assertTrue(connection.closed)


class Routing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        for target, value in [('STATE', self.state), ('enabled', lambda: True), ('_locked', unlocked)]:
            current = patch.object(network, target, value)
            current.start(); self.addCleanup(current.stop)
        self.clock = patch.object(network.time, 'time', return_value=100000.0)
        self.time = self.clock.start(); self.addCleanup(self.clock.stop)

    def test_one_vpn_attempt_after_geo_then_exact_host_cache(self):
        direct = Mock(return_value=blocked())
        with patch.object(network, 'vpn_fetch', return_value=success()) as vpn:
            first = network.fetch(URL, direct)
            self.assertTrue(first['ok']); self.assertTrue(first['network']['retry'])
            self.assertTrue(first['network']['route_saved'])
            route = network._load(self.time())['research.example']
            self.assertEqual(route['expires_at'], self.time() + network.TTL)
            self.assertEqual(route['status'], 'vpn_verified')
            direct.reset_mock()
            second = network.fetch(URL, direct)
            direct.assert_not_called()
            self.assertEqual(vpn.call_count, 2)
            self.assertFalse(second['network']['retry'])
            other = 'https://sub.research.example/public-page'
            direct.return_value = success(other)
            network.fetch(other, direct)
            direct.assert_called_once_with(other)
            self.assertEqual(vpn.call_count, 2)

    def test_challenges_and_other_failures_never_probe_vpn(self):
        for category in ('legal_restriction', 'access_challenge',
                         'rate_limited', 'authentication_required', 'timeout', 'tls', 'dns'):
            with self.subTest(category=category), patch.object(network, 'vpn_fetch') as vpn:
                original = blocked(category)
                self.assertEqual(network.fetch(URL, Mock(return_value=original)), original)
                vpn.assert_not_called()
        self.assertFalse((self.state / 'routes.json').exists())

    def test_http403_retries_once_then_caches_only_the_successful_exact_host(self):
        direct = Mock(return_value=blocked('access_denied'))
        with patch.object(network, 'vpn_fetch', return_value=success()) as vpn:
            first = network.fetch(URL, direct)
            self.assertTrue(first['ok'])
            self.assertTrue(first['network']['retry'])
            self.assertTrue(first['network']['route_saved'])
            direct.assert_called_once_with(URL)
            vpn.assert_called_once_with(URL)
            cached = network._load(self.time())['research.example']
            self.assertEqual(cached['status'], 'vpn_verified')
            self.assertEqual(cached['expires_at'], self.time() + network.TTL)
            self.assertEqual(cached['reason'], 'http_403')
            self.assertEqual(cached['direct_http_status'], 403)
            self.assertEqual(cached['vpn_http_status'], 200)
            self.assertEqual(first['network']['reason'], 'http_403')
            self.assertEqual(first['network']['direct_http_status'], 403)
            self.assertEqual(first['network']['vpn_http_status'], 200)
            direct.reset_mock()
            second = network.fetch(URL, direct)
            direct.assert_not_called()
            self.assertEqual(vpn.call_count, 2)
            self.assertFalse(second['network']['retry'])
            self.assertEqual(second['network']['reason'], 'cached_http_403')
            other = 'https://other.research.example/article'
            direct.return_value = success(other)
            self.assertTrue(network.fetch(other, direct)['ok'])
            direct.assert_called_once_with(other)
            self.assertEqual(vpn.call_count, 2)

    def test_two_403_responses_are_terminal_and_cool_down_without_loop(self):
        denial = blocked('access_denied')
        direct = Mock(return_value=denial)
        with patch.object(network, 'vpn_fetch', return_value=denial) as vpn:
            first = network.fetch(URL, direct)
            self.assertFalse(first['ok'])
            self.assertEqual(first['error_category'], 'access_denied')
            self.assertTrue(first['network']['retry'])
            self.assertFalse(first['network']['route_saved'])
            self.assertEqual(first['network']['direct_http_status'], 403)
            self.assertEqual(first['network']['vpn_http_status'], 403)
            self.assertEqual(vpn.call_count, 1)
            route = network._load(self.time())['research.example']
            self.assertEqual(route['expires_at'], 0)
            self.assertEqual(route['retry_at'], self.time() + network.COOLDOWN)
            self.assertEqual(route['reason'], 'http_403')
            self.assertEqual(route['direct_http_status'], 403)
            self.assertEqual(route['vpn_http_status'], 403)
            second = network.fetch(URL, direct)
            self.assertFalse(second['ok'])
            self.assertEqual(second['network']['reason'], 'vpn_probe_cooldown')
            self.assertFalse(second['network']['retry'])
            self.assertEqual(vpn.call_count, 1)
        self.time.return_value += network.COOLDOWN + 1
        with patch.object(network, 'vpn_fetch', return_value=success()) as vpn:
            self.assertTrue(network.fetch(URL, direct)['ok'])
            vpn.assert_called_once_with(URL)

    def test_access_denied_requires_actual_http403_status(self):
        for status in (None, 200, 401, 429, 451, 500, '403', 403.0, True):
            with self.subTest(status=status), patch.object(network, 'vpn_fetch') as vpn:
                original = {**blocked('access_denied'), 'http_status': status}
                self.assertEqual(network.fetch(URL, Mock(return_value=original)), original)
                vpn.assert_not_called()

    def test_plain_451_is_not_converted_into_http403_retry(self):
        original = {**blocked('legal_restriction'), 'http_status': 451}
        with patch.object(network, 'vpn_fetch') as vpn:
            self.assertEqual(network.fetch(URL, Mock(return_value=original)), original)
            vpn.assert_not_called()

    def test_old_verified_geo_cache_is_read_without_migration_or_probe(self):
        entry = {'checked_at': self.time(), 'expires_at': self.time() + network.TTL,
                 'retry_at': 0, 'status': 'vpn_verified',
                 'reason': 'explicit_geographic_restriction', 'last_error': None}
        network._save({'research.example': entry})
        direct = Mock()
        with patch.object(network, 'vpn_fetch', return_value=success()) as vpn:
            result = network.fetch(URL, direct)
        self.assertTrue(result['ok'])
        self.assertEqual(result['network']['reason'], 'cached_geographic_restriction')
        direct.assert_not_called(); vpn.assert_called_once_with(URL)
        self.assertEqual(network._load(self.time())['research.example'], entry)

    def test_http403_redirect_retries_and_caches_only_public_final_host(self):
        target = 'https://final.example/article'
        with patch.object(network, 'vpn_fetch', return_value=success(target)) as vpn:
            result = network.fetch(URL, Mock(return_value=blocked('access_denied', target)))
        self.assertTrue(result['ok'])
        vpn.assert_called_once_with(target)
        self.assertEqual(result['network']['host'], 'final.example')
        self.assertEqual(set(network._load(self.time())), {'final.example'})
        self.assertEqual(result['network']['reason'], 'http_403')
        self.assertEqual(result['network']['direct_http_status'], 403)
        self.assertEqual(result['network']['vpn_http_status'], 200)

    def test_disabled_reader_keeps_403_direct_result(self):
        original = blocked('access_denied')
        with patch.object(network, 'enabled', return_value=False), patch.object(network, 'vpn_fetch') as vpn:
            direct = Mock(return_value=original)
            result = network.fetch(URL, direct)
            self.assertEqual({key: result[key] for key in original}, original)
            self.assertEqual(result['network']['reason'], 'vpn_reader_unavailable')
            self.assertFalse(result['network']['retry'])
            self.assertEqual(result['network']['direct_http_status'], 403)
            direct.assert_called_once_with(URL)
            vpn.assert_not_called()

    def test_direct_source_reader_403_reaches_vpn_without_treating_denial_as_evidence(self):
        response = Connection(Response('<title>Forbidden</title><p>Access denied.</p>', status=403))
        with patch.object(sources, '_addresses', return_value=['8.8.8.8']), \
                patch.object(sources, '_connection', return_value=response), \
                patch.object(network, 'vpn_fetch', return_value=success()) as vpn:
            result = sources.fetch_source(URL)
        self.assertTrue(result['ok'])
        self.assertEqual(result['text'], TEXT)
        self.assertTrue(response.closed)
        vpn.assert_called_once_with(URL)

    def test_direct_403_challenge_does_not_use_vpn_even_with_geographic_words(self):
        response = Connection(Response('<title>Access denied</title><p>Verify you are human; not available in your country.</p>', status=403))
        with patch.object(sources, '_addresses', return_value=['8.8.8.8']), \
                patch.object(sources, '_connection', return_value=response), \
                patch.object(network, 'vpn_fetch') as vpn:
            result = sources.fetch_source(URL)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_category'], 'access_challenge')
        self.assertEqual(result['text'], '')
        vpn.assert_not_called()

    def test_http403_vpn_transport_error_keeps_original_status_and_redacts_details(self):
        with patch.object(network, 'vpn_fetch', side_effect=OSError('PRIVATE transport diagnostic')):
            result = network.fetch(URL, Mock(return_value=blocked('access_denied')))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_category'], 'vpn_transport_unavailable')
        self.assertEqual(result['network']['reason'], 'http_403')
        self.assertEqual(result['network']['direct_http_status'], 403)
        self.assertEqual(result['network']['direct_error'], 'access_denied')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertIsNone(result['network'].get('vpn_http_status'))

    def test_failed_probe_has_persistent_cooldown_and_bounded_retry(self):
        direct = Mock(return_value=blocked())
        with patch.object(network, 'vpn_fetch', side_effect=OSError('secret transport diagnostic')) as vpn:
            first = network.fetch(URL, direct)
            self.assertFalse(first['ok']); self.assertNotIn('secret', json.dumps(first))
            second = network.fetch(URL, direct)
            self.assertEqual(second['network']['reason'], 'vpn_probe_cooldown')
            self.assertEqual(vpn.call_count, 1)
        self.time.return_value += network.COOLDOWN + 1
        with patch.object(network, 'vpn_fetch', return_value=success()) as vpn:
            self.assertTrue(network.fetch(URL, direct)['ok'])
            vpn.assert_called_once()

    def test_cached_failure_removes_successful_route_until_new_geo_probe(self):
        network._remember('research.example', success(), self.time())
        with patch.object(network, 'vpn_fetch', return_value=blocked('access_challenge')):
            result = network.fetch(URL, Mock())
        self.assertFalse(result['ok'])
        self.assertEqual(network._load(self.time())['research.example']['expires_at'], 0)
        with patch.object(network, 'vpn_fetch') as vpn:
            self.assertEqual(network.fetch(URL, Mock(return_value=blocked()))['network']['reason'], 'vpn_probe_cooldown')
            vpn.assert_not_called()

    def test_expired_success_returns_to_direct_route(self):
        network._remember('research.example', success(), self.time())
        self.time.return_value += network.TTL + 1
        direct = Mock(return_value=success())
        with patch.object(network, 'vpn_fetch') as vpn:
            result = network.fetch(URL, direct)
            self.assertTrue(result['ok']); direct.assert_called_once(); vpn.assert_not_called()

    def test_redirect_block_caches_only_final_exact_host(self):
        target = 'https://final.example/article'
        with patch.object(network, 'vpn_fetch', return_value=success(target)) as vpn:
            result = network.fetch(URL, Mock(return_value=blocked(url=target)))
        vpn.assert_called_once_with(target)
        self.assertEqual(result['network']['host'], 'final.example')
        self.assertEqual(set(network._load(self.time())), {'final.example'})

    def test_state_corruption_does_not_create_an_unrecorded_vpn_route(self):
        for raw in ('{broken', '{"version":2,"hosts":{}}', '{"version":1,"hosts":[]}'):
            with self.subTest(raw=raw), patch.object(network, 'vpn_fetch') as vpn:
                (self.state / 'routes.json').write_text(raw)
                result = network.fetch(URL, Mock(return_value=blocked()))
                self.assertFalse(result['ok'])
                self.assertEqual(result['network']['reason'], 'route_state_unavailable')
                vpn.assert_not_called()
                self.assertEqual((self.state / 'routes.json').read_text(), raw)

    def test_routes_remain_bounded_and_expiration_is_checked(self):
        entries = {f'host-{index}.example': {'checked_at': self.time() + index / 1000,
                   'expires_at': self.time() + 10, 'retry_at': 0, 'status': 'vpn_verified'} for index in range(300)}
        network._save(entries)
        loaded = network._load(self.time())
        self.assertEqual(len(loaded), network.MAX_HOSTS)
        self.assertIn('host-299.example', loaded)
        self.assertNotIn('host-0.example', loaded)
        self.assertEqual(network._load(self.time() + 11), {})
        raw = {'version': 1, 'hosts': {'host.example': {'checked_at': self.time(), 'expires_at': float('inf'), 'retry_at': 0}}}
        (self.state / 'routes.json').write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            network._load(self.time())

    def test_unsupported_ipv6_route_fails_closed_without_poisoning_state(self):
        address = 'https://[2001:4860:4860::8888]/article'
        with patch.object(network, 'vpn_fetch') as vpn:
            result = network.fetch(address, Mock(return_value=blocked(url=address)))
            self.assertFalse(result['ok'])
            self.assertEqual(result['network']['reason'], 'route_state_unavailable')
            vpn.assert_not_called()
        self.assertFalse((self.state / 'routes.json').exists())
        network._remember('research.example', success(), self.time())
        self.assertIn('research.example', network._load(self.time()))

    def test_invalid_redirect_target_is_not_sent_to_vpn(self):
        with patch.object(network, 'vpn_fetch') as vpn:
            result = network.fetch(URL, Mock(return_value=blocked(url='http://127.0.0.1/private')))
            self.assertFalse(result['ok'])
            self.assertEqual(result['network']['reason'], 'invalid_retry_target')
            vpn.assert_not_called()

    def test_disabled_reader_keeps_original_direct_behavior(self):
        with patch.object(network, 'enabled', return_value=False), patch.object(network, 'vpn_fetch') as vpn:
            direct = Mock(return_value=blocked())
            result = network.fetch(URL, direct)
            self.assertEqual({key: result[key] for key in blocked()}, blocked())
            self.assertEqual(result['network']['reason'], 'vpn_reader_unavailable')
            self.assertFalse(result['network']['retry'])
            vpn.assert_not_called()

    def test_public_source_guards_still_apply_before_vpn_evidence_read(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=answers), patch.object(sources, '_connection') as connect, \
                patch.object(network, 'vpn_fetch') as vpn:
            result = sources.fetch_source('https://public-looking.example/')
        self.assertEqual(result['error_category'], 'disallowed_url')
        connect.assert_not_called(); vpn.assert_not_called()
        first = Connection(Response(status=302, Location='https://redirect.example/private'))
        with patch.object(sources, '_addresses', side_effect=[['8.8.8.8'], ValueError('private address')]), \
                patch.object(sources, '_connection', return_value=first), patch.object(network, 'vpn_fetch') as vpn:
            result = sources.fetch_source(URL)
        self.assertEqual(result['error_category'], 'disallowed_url')
        vpn.assert_not_called(); self.assertTrue(first.closed)


class NetworkJournal(unittest.TestCase):
    def check_route(self, details):
        from iva_integration import demand_research as research
        with tempfile.TemporaryDirectory() as directory:
            store = research.Research(Path(directory) / 'evidence.sqlite3', Path(directory) / 'cards')
            try:
                with patch.object(research, 'AGENT_RESEARCH_ENABLED', False):
                    cycle = store.cycle('network-journal-test', 'Public evidence only.')
                with store.db:
                    store.db.execute('''INSERT INTO demand_candidates
                        (cycle_id,url,purpose,source_type,state,batch_index,analysis_state)
                        VALUES(?,?,'support','discussion','pending',0,'pending')''', (cycle['id'], URL))
                research._fetch(store, cycle, lambda url: {**blocked('access_denied'), 'network': details})
                rows = store.rows("SELECT detail FROM demand_events WHERE event='network_route'")
                self.assertEqual(len(rows), 1)
                return json.loads(rows[0]['detail'])
            finally:
                store.close()

    def test_research_journal_preserves_both_http_statuses_and_retry_reason(self):
        details = {'route': 'vpn', 'reason': 'http_403', 'host': 'research.example',
                   'direct_error': 'access_denied', 'retry': True, 'route_saved': False,
                   'direct_http_status': 403, 'vpn_http_status': 403}
        event = self.check_route(details)
        self.assertEqual(event, {'url': URL, **details})

    def test_journal_drops_invalid_status_types_and_unapproved_fields(self):
        for value in (True, '403', 99, 600, None, {'secret': 'private'}):
            with self.subTest(value=value):
                event = self.check_route({'route': 'vpn', 'reason': 'http_403',
                                         'direct_http_status': value, 'vpn_http_status': value,
                                         'cookies': 'PRIVATE'})
                self.assertNotIn('direct_http_status', event)
                self.assertNotIn('vpn_http_status', event)
                self.assertNotIn('cookies', event)
                self.assertNotIn('PRIVATE', json.dumps(event))


class FakeSocket:
    def __init__(self, response):
        self.parts = [response] if isinstance(response, bytes) else list(response)
        self.sent = None
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def settimeout(self, value): self.timeout = value
    def connect(self, path): self.path = path
    def sendall(self, value): self.sent = value
    def recv(self, maximum): return self.parts.pop(0) if self.parts else b''


class RpcReader(unittest.TestCase):
    def setUp(self):
        # Windows Python may omit AF_UNIX; these two client tests use a fake
        # transport. Real descriptor/credential checks run separately on Linux.
        unix = patch.object(network.socket, 'AF_UNIX', getattr(socket, 'AF_UNIX', 1), create=True)
        unix.start(); self.addCleanup(unix.stop)

    def test_client_checks_hash_status_and_public_final_url(self):
        valid = success()
        raw = json.dumps(valid).encode() + b'\n'
        connection = FakeSocket([raw[:10], raw[10:]])
        with patch.object(network.socket, 'socket', return_value=connection):
            self.assertEqual(network.vpn_fetch(URL), valid)
        self.assertEqual(json.loads(connection.sent), {'action': 'fetch', 'url': URL})
        invalid = [{**valid, 'content_hash': 'bad'}, {**valid, 'http_status': 201},
                   {**valid, 'canonical_url': 'http://127.0.0.1/private'},
                   {**valid, 'text': 'x' * 80001}, {**valid, 'ok': 'true'}]
        for value in invalid:
            with self.subTest(kind=list(value)[-1]), patch.object(network.socket, 'socket',
                       return_value=FakeSocket(json.dumps(value).encode() + b'\n')):
                with self.assertRaises(ValueError): network.vpn_fetch(URL)

    def test_client_refuses_truncated_or_oversized_response(self):
        for body in (b'{"ok": false}', b'x' * (network.MAX_RESPONSE + 1)):
            with patch.object(network.socket, 'socket', return_value=FakeSocket(body)):
                with self.assertRaises(ValueError): network.vpn_fetch(URL)

    def test_service_only_accepts_public_fetch_without_extra_commands(self):
        self.assertEqual(service.validated_request(json.dumps({'action': 'fetch', 'url': URL})), URL)
        requests = [{'action': 'shell', 'url': URL}, {'action': 'fetch', 'url': URL, 'command': 'secret'},
                    {'action': 'fetch', 'url': 'http://169.254.169.254/'},
                    {'action': 'fetch', 'url': 'https://user:secret@example.org/'},
                    {'action': 'fetch', 'url': 'https://example.org/?token=secret'}]
        for value in requests:
            with self.subTest(value=value['action']), self.assertRaises(ValueError):
                service.validated_request(json.dumps(value))

    def test_service_uses_fixed_isolated_worker_and_minimal_environment(self):
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(success()).encode())
        with patch.dict(os.environ, {'SECRET_API_KEY': 'private', 'COOKIE': 'secret'}), \
                patch.object(service.subprocess, 'run', return_value=completed) as run:
            self.assertTrue(service.execute(json.dumps({'action': 'fetch', 'url': URL}))['ok'])
        args, kwargs = run.call_args
        self.assertIn('-I', args[0]); self.assertEqual(args[0][-1], '--once')
        self.assertEqual(kwargs['timeout'], 20)
        self.assertNotIn('SECRET_API_KEY', kwargs['env']); self.assertNotIn('COOKIE', kwargs['env'])
        self.assertNotIn('shell', kwargs)
        self.assertEqual(json.loads(kwargs['input']), {'action': 'fetch', 'url': URL})

    def test_service_timeout_and_bad_response_have_no_private_diagnostics(self):
        with patch.object(service.subprocess, 'run', side_effect=subprocess.TimeoutExpired('SECRET', 20)):
            value = service.execute(json.dumps({'action': 'fetch', 'url': URL}))
        self.assertEqual(value['error_category'], 'vpn_reader_timeout')
        self.assertNotIn('SECRET', json.dumps(value))
        for completed in (SimpleNamespace(returncode=1, stdout=b'SECRET'),
                          SimpleNamespace(returncode=0, stdout=b'x' * (service.MAX_RESPONSE + 1)),
                          SimpleNamespace(returncode=0, stdout=b'{"ok":"yes"}')):
            with patch.object(service.subprocess, 'run', return_value=completed):
                result = service.execute(json.dumps({'action': 'fetch', 'url': URL}))
                self.assertFalse(result['ok']); self.assertNotIn('SECRET', json.dumps(result))

    def test_rpc_rejects_other_uid_before_reading_request(self):
        client = Mock()
        client.getsockopt.return_value = struct.pack('3i', 10, 9999, 9999)
        with patch.object(service.socket, 'SO_PEERCRED', getattr(socket, 'SO_PEERCRED', 17), create=True), \
                patch.object(service.os, 'geteuid', return_value=1000, create=True), \
                patch.object(service, 'execute') as execute:
            service.handle(client)
        client.recv.assert_not_called(); execute.assert_not_called()

    def test_rpc_large_result_becomes_bounded_error(self):
        client = Mock()
        client.getsockopt.return_value = struct.pack('3i', 10, 1000, 1000)
        client.recv.return_value = json.dumps({'action': 'fetch', 'url': URL}).encode() + b'\n'
        with patch.object(service.socket, 'SO_PEERCRED', getattr(socket, 'SO_PEERCRED', 17), create=True), \
                patch.object(service.os, 'geteuid', return_value=1000, create=True), \
                patch.object(service, 'execute', return_value={'ok': True, 'text': 'x' * service.MAX_RESPONSE}):
            service.handle(client)
        outgoing = client.sendall.call_args.args[0]
        self.assertLess(len(outgoing), 1000)
        self.assertEqual(json.loads(outgoing)['error_category'], 'vpn_reader_response_too_large')

    @unittest.skipUnless(os.name == 'posix', 'SO_PEERCRED is exercised on Linux')
    def test_real_private_socket_handles_one_bounded_public_request(self):
        server, client = socket.socketpair()
        errors = []
        def work():
            try:
                with server: service.handle(server)
            except Exception as exc:
                errors.append(type(exc).__name__)
        with patch.object(service, 'execute', return_value=success()) as execute:
            thread = threading.Thread(target=work)
            thread.start()
            with client:
                client.settimeout(3)
                client.sendall(json.dumps({'action': 'fetch', 'url': URL}).encode() + b'\n')
                received = client.recv(service.MAX_RESPONSE)
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
            self.assertTrue(json.loads(received)['ok']); execute.assert_called_once()

    @unittest.skipUnless(os.name == 'posix', 'SO_PEERCRED is exercised on Linux')
    def test_real_socket_rejects_oversized_request_before_worker(self):
        server, client = socket.socketpair()
        with patch.object(service, 'execute') as execute:
            with client:
                client.sendall(b'x' * (service.MAX_REQUEST + 1))
                with server: service.handle(server)
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
