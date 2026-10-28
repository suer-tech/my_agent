"""Research-only fallback after an ordinary HTTP403 or explicit geographic denial.

No VPN key, global routes, browser profile, cookie, or model-written config.
Successful exact hosts are cached for seven days; failures have a cooldown.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'data/research-network'
SOCKET = Path('/run/iva-research-fetch/fetch.sock')
TTL = 7 * 86400
COOLDOWN = 3600
MAX_HOSTS = 256
MAX_RESPONSE = 600000
RETRY_REASONS = frozenset(('explicit_geographic_restriction', 'http_403'))


def _retry_reason(result):
    if result.get('ok') is True:
        return None
    if result.get('error_category') == 'geographic_restriction':
        return 'explicit_geographic_restriction'
    if _http_status(result.get('http_status')) == 403 and result.get('error_category') == 'access_denied':
        return 'http_403'
    return None


def _http_status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def restriction(status, text='', title=''):
    """A generic 403/451 or an article discussing a geoblock is insufficient."""
    if status == 401:
        return 'authentication_required'
    if status == 429:
        return 'rate_limited'
    value = re.sub(r'\s+', ' ', (title + ' ' + text[:8192]).casefold())
    if status == 200 and len(text) < 2000 and re.search(
            r'^\s*(?:please\s+)?(?:(?:verify|confirm) (?:that )?you are (?:a )?human\b|checking your browser\b|'
            r'enable javascript and cookies to continue\b|подтвердите,? что вы (?:не робот|человек))', text, re.I):
        return 'access_challenge'
    block_title = re.search(r'^(?:access denied|forbidden|unavailable|service unavailable|доступ ограничен|just a moment|attention required|robot check|captcha)(?:[.!…]|\s*[-|:]|$)', title.strip(), re.I)
    # A denial page may incorrectly return 200. Require a denial title, not a
    # normal article containing quoted geography errors or troubleshooting tips.
    geo_title = re.fullmatch(
        r'(?:(?:this )?(?:content|service) (?:is )?)?(?:not available|not accessible|unavailable|restricted) '
        r'(?:in|from|for) (?:your|this) (?:country|region|location)[.!…]?', title.strip(), re.I)
    block_title = block_title or geo_title
    if status in (403, 451) or (status == 200 and block_title and len(text) < 8000):
        if any(item in value for item in (
                'verify you are human', 'checking your browser', 'confirm you are human',
                'captcha', 'blocked by network security', 'automated requests', 'automated access',
                'unknown bots', 'bot detection', 'подтвердите, что вы не робот',
                'подтвердите, что вы человек', 'enable javascript and cookies',
                'log in to continue', 'login to continue', 'sign in to continue')):
            return 'access_challenge'
    if status in (403, 451) or (status == 200 and block_title and len(text) < 8000):
        patterns = (
            r'(?:not available|not accessible|not supported|unavailable|restricted) (?:in|from|for) (?:your|this) (?:country|region|location)',
            r'(?:access|requests?).{0,60}(?:from|in) (?:your|this) (?:country|region|location).{0,60}(?:denied|blocked|restricted|not allowed)',
            r'(?:your|this) (?:country|region|location).{0,60}(?:not supported|not permitted|blocked|restricted)',
            r'\bgeo[-_ ]?(?:blocked|restricted|restriction)\b',
            r'(?:недоступен|недоступна|недоступно|заблокирован|ограничен).{0,35}(?:в|из|для) (?:ваш\w*|данн\w*|эт\w*) (?:стран\w*|регион\w*)',
            r'(?:ваш\w*|данн\w*|эт\w*) (?:стран\w*|регион\w*).{0,45}(?:не поддерживается|заблокирован|ограничен)',
        )
        if any(re.search(pattern, value) for pattern in patterns):
            return 'geographic_restriction'
    if status == 200 and block_title and len(text) < 8000:
        return 'access_challenge'
    return {403: 'access_denied', 451: 'legal_restriction'}.get(status)


def enabled():
    return os.name == 'posix' and SOCKET.is_socket()


def _load(clock):
    path = STATE / 'routes.json'
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 128000:
        raise ValueError('invalid_route_state')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('hosts'), dict):
        raise ValueError('invalid_route_state')
    result = {}
    for host, entry in value['hosts'].items():
        if not isinstance(host, str) or not re.fullmatch(r'[a-z0-9.-]{1,253}', host) or not isinstance(entry, dict):
            raise ValueError('invalid_route_state')
        for key in ('checked_at', 'expires_at', 'retry_at'):
            if type(entry.get(key)) not in (int, float) or not math.isfinite(entry[key]):
                raise ValueError('invalid_route_state')
        if entry['expires_at'] > clock + TTL + 60 or entry['retry_at'] > clock + COOLDOWN + 60:
            raise ValueError('invalid_route_state')
        if max(entry['expires_at'], entry['retry_at']) > clock:
            result[host] = entry
    return result


def _save(hosts):
    # A bounded state file, not an append-only per-page log.
    ordered = sorted(hosts.items(), key=lambda item: item[1]['checked_at'], reverse=True)[:MAX_HOSTS]
    descriptor, temporary = tempfile.mkstemp(prefix='.routes-', dir=STATE)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as out:
            json.dump({'version': 1, 'hosts': dict(ordered)}, out, ensure_ascii=True)
            out.flush(); os.fsync(out.fileno())
        os.replace(temporary, STATE / 'routes.json')
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _locked():
    import fcntl
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    if STATE.is_symlink():
        raise ValueError('invalid_route_state')
    descriptor = os.open(STATE / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _entry(host, clock):
    with _locked():
        return _load(clock).get(host)


def _claim(host, clock, reason='explicit_geographic_restriction'):
    if reason not in RETRY_REASONS:
        raise ValueError('invalid_retry_reason')
    if not re.fullmatch(r'[a-z0-9.-]{1,253}', host):
        raise ValueError('unsupported_route_host')
    with _locked():
        hosts = _load(clock)
        if hosts.get(host, {}).get('retry_at', 0) > clock:
            return False
        hosts[host] = {'checked_at': clock, 'expires_at': 0, 'retry_at': clock + COOLDOWN,
                       'status': 'probe_pending', 'reason': reason}
        _save(hosts)
        return True


def _remember(host, result, clock, reason='explicit_geographic_restriction', original=None):
    if reason not in RETRY_REASONS:
        raise ValueError('invalid_retry_reason')
    with _locked():
        hosts = _load(clock)
        success = result.get('ok') is True
        hosts[host] = {'checked_at': clock, 'expires_at': clock + TTL if success else 0,
                       'retry_at': 0 if success else clock + COOLDOWN,
                       'status': 'vpn_verified' if success else 'vpn_unavailable',
                       'reason': reason,
                       'direct_http_status': _http_status((original or {}).get('http_status')),
                       'vpn_http_status': _http_status(result.get('http_status')),
                       'last_error': None if success else str(result.get('error_category') or 'unavailable')[:80]}
        _save(hosts)


def vpn_fetch(url, *, include_contact_fragments=False):
    from iva_integration.demand_sources import canonical_url
    if type(include_contact_fragments) is not bool:
        raise ValueError('include_contact_fragments_must_be_boolean')
    url = canonical_url(url)
    request = {'action': 'fetch', 'url': url}
    if include_contact_fragments:
        request['include_contact_fragments'] = True
    payload = json.dumps(request).encode() + b'\n'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(24)
        client.connect(str(SOCKET)); client.sendall(payload)
        data = bytearray()
        while b'\n' not in data:
            piece = client.recv(65536)
            if not piece or len(data) + len(piece) > MAX_RESPONSE:
                raise ValueError('invalid_vpn_response')
            data.extend(piece)
    result = json.loads(data.split(b'\n', 1)[0])
    if not isinstance(result, dict) or type(result.get('ok')) is not bool:
        raise ValueError('invalid_vpn_response')
    if result.get('ok'):
        text = result.get('text')
        if (not isinstance(text, str) or not 40 <= len(text) <= 80000
                or result.get('http_status') != 200
                or hashlib.sha256(text.encode()).hexdigest() != result.get('content_hash')):
            raise ValueError('invalid_vpn_evidence')
        canonical_url(result['canonical_url'])
    if 'contact_fragments' in result or 'contact_readable' in result or 'contact_fragments_hash' in result:
        fragments = result.get('contact_fragments')
        if (not include_contact_fragments or not isinstance(fragments, list) or len(fragments) > 4
                or any(not isinstance(item, str) or not 1 <= len(item) <= 400 for item in fragments)
                or type(result.get('contact_readable')) is not bool
                or result['contact_readable'] != bool(fragments)
                or result.get('http_status') != 200
                or result.get('status') not in ('ok', 'empty')
                or result.get('error_category') not in (None, 'insufficient_content', 'no_readable_text')
                or hashlib.sha256(json.dumps(fragments, ensure_ascii=False,
                    separators=(',', ':')).encode('utf-8')).hexdigest() != result.get('contact_fragments_hash')):
            raise ValueError('invalid_vpn_contact_evidence')
        canonical_url(result['canonical_url'])
    return result


def _retry(url, *, include_contact_fragments=False):
    try:
        if include_contact_fragments:
            return vpn_fetch(url, include_contact_fragments=True)
        return vpn_fetch(url)
    except (OSError, ValueError, TypeError, KeyError):
        return {'ok': False, 'url': url, 'canonical_url': url, 'text': '', 'title': '',
                'content_hash': None, 'status': 'unavailable', 'error_category': 'vpn_transport_unavailable',
                'error': 'Research VPN reader is unavailable; source evidence was not read'}


def fetch(url, direct, *, include_contact_fragments=False):
    from iva_integration.demand_sources import canonical_url
    if type(include_contact_fragments) is not bool:
        raise ValueError('include_contact_fragments_must_be_boolean')
    retry = (lambda target: _retry(target, include_contact_fragments=True)) if include_contact_fragments else _retry
    try:
        clean = canonical_url(url); host = urlsplit(clean).hostname
    except (ValueError, TypeError, UnicodeError):
        return direct(url)
    if not enabled():
        original = direct(clean)
        if _retry_reason(original):
            return {**original, 'network': {'route': 'direct', 'reason': 'vpn_reader_unavailable',
                    'direct_http_status': _http_status(original.get('http_status')), 'host': host, 'retry': False}}
        return original
    clock = time.time()
    try:
        prior = _entry(host, clock)
    except (OSError, ValueError):
        prior = None
    if prior and prior.get('status') == 'vpn_verified' and prior['expires_at'] > clock:
        reason = prior.get('reason') if prior.get('reason') in RETRY_REASONS else 'explicit_geographic_restriction'
        result = retry(clean)
        if not result.get('ok'):
            try:_remember(host, result, clock, reason, {'http_status': prior.get('direct_http_status')})
            except (OSError, ValueError):pass
        return {**result, 'network': {'route': 'vpn', 'reason': 'cached_http_403' if reason == 'http_403' else 'cached_geographic_restriction',
                                      'vpn_http_status': _http_status(result.get('http_status')),
                                      'host': host, 'retry': False}}
    original = direct(clean)
    reason = _retry_reason(original)
    if reason is None:
        return original
    direct_meta = {'route': 'direct', 'direct_error': original['error_category'],
                   'direct_http_status': _http_status(original.get('http_status')), 'retry': False}
    # A redirect may fail on a different host; never broaden this to a wildcard.
    try:
        target = canonical_url(original.get('canonical_url') or clean)
    except (ValueError, TypeError, UnicodeError):
        return {**original, 'network': {**direct_meta, 'reason': 'invalid_retry_target'}}
    host = urlsplit(target).hostname
    try:
        claimed = _claim(host, clock, reason)
    except (OSError, ValueError):
        return {**original, 'network': {**direct_meta, 'reason': 'route_state_unavailable', 'host': host}}
    if not claimed:
        return {**original, 'network': {**direct_meta, 'reason': 'vpn_probe_cooldown', 'host': host}}
    result = retry(target)
    saved = True
    try:_remember(host, result, time.time(), reason, original)
    except (OSError, ValueError):saved = False
    return {**result, 'network': {'route': 'vpn', 'reason': reason,
                                  'direct_error': original['error_category'], 'host': host,
                                  'direct_http_status': _http_status(original.get('http_status')),
                                  'vpn_http_status': _http_status(result.get('http_status')),
                                  'retry': True, 'route_saved': saved and result.get('ok') is True}}


def status():
    try:
        clock = time.time()
        hosts = _load(clock)
        return {'reader_available': enabled(), 'verified_domains': sum(x['expires_at'] > clock for x in hosts.values()),
                'cooldown_domains': sum(x['retry_at'] > clock for x in hosts.values()),
                'policy': 'http_403_or_explicit_geo; one_vpn_retry; exact_host; ttl_7_days; cooldown_1_hour; no_auth_or_captcha_bypass'}
    except (OSError, ValueError):
        return {'reader_available': enabled(), 'state': 'route_state_unavailable'}
