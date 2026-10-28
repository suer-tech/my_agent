"""One real direct request and conditional VPN retry; no synthetic denial.

Only access metadata is retained. The source route cache is isolated, and no
model, research database, browser profile, notification or VPN config is used.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from iva_integration import demand_network as network
from iva_integration.demand_sources import _fetch_source_direct
from deploy.verify_demand_agents import deployment_read_lock, stamp
from deploy.verify_demand_branch import atomic_json, research_lease

RELEASE = ROOT / 'data/demand-http403-release-20261026'
OUTPUT = RELEASE / 'verification-network'
URL = 'https://www.ukbusinessforums.co.uk/threads/linking-shop-stock-control-and-online-sales.223165/'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def summary(result):
    return {key: result.get(key) for key in
            ('ok', 'http_status', 'error_category', 'canonical_url', 'network')} | {
                'verified_text_chars': len(result.get('text') or ''),
                'content_hash': result.get('content_hash'),
                'text_hash_matches': (hashlib.sha256(result.get('text', '').encode()).hexdigest() == result.get('content_hash')) if result.get('ok') else None}


def run_probe(directory, direct=None, vpn=None):
    """Test transport injections are only used by unit tests, never main()."""
    injected = direct is not None or vpn is not None
    direct = direct or _fetch_source_direct
    vpn = vpn or network.vpn_fetch
    production_cache = network.STATE / 'routes.json'
    before = digest(production_cache)
    direct_calls, vpn_calls = [], []

    def read_direct(url):
        value = direct(url)
        direct_calls.append({'url': url, 'result': summary(value)})
        return value

    def read_vpn(url):
        try:
            value = vpn(url)
            vpn_calls.append({'url': url, 'result': summary(value)})
            return value
        except (OSError, ValueError, TypeError, KeyError) as exc:
            vpn_calls.append({'url': url, 'result': {'ok': False, 'http_status': None,
                              'error_category': 'vpn_transport_unavailable'}, 'error_type': type(exc).__name__})
            raise

    isolated = directory / 'route-state'
    isolated.mkdir(mode=0o700, exist_ok=False)
    with patch.object(network, 'STATE', isolated), patch.object(network, 'vpn_fetch', read_vpn):
        result = network.fetch(URL, read_direct)
        status = network.status()
        isolated_file = network.STATE / 'routes.json'
        cache = json.loads(isolated_file.read_text()) if isolated_file.exists() else None
    original = direct_calls[0]['result'] if len(direct_calls) == 1 else {}
    retried = vpn_calls[0]['result'] if len(vpn_calls) == 1 else {}
    metadata = result.get('network') or {}
    success = result.get('ok') is True
    expected_target = original.get('canonical_url') or URL
    criteria = {
        'real_direct_ordinary_403_observed': original.get('http_status') == 403 and original.get('error_category') == 'access_denied',
        'exactly_one_direct_and_one_vpn_request': len(direct_calls) == 1 and len(vpn_calls) == 1,
        'vpn_http_response_observed': type(retried.get('http_status')) is int and 100 <= retried['http_status'] <= 599,
        'same_public_target_retried': len(vpn_calls) == 1 and vpn_calls[0]['url'] == expected_target,
        'actual_403_trigger_recorded_without_geo_claim': metadata.get('route') == 'vpn' and metadata.get('reason') == 'http_403' and metadata.get('retry') is True,
        'both_http_outcomes_recorded': metadata.get('direct_http_status') == 403 and metadata.get('vpn_http_status') == retried.get('http_status'),
        'result_matches_vpn_outcome': len(vpn_calls) == 1 and result.get('ok') == retried.get('ok') and result.get('http_status') == retried.get('http_status'),
        'success_has_verified_content_or_failure_remains_gap': (summary(result)['text_hash_matches'] is True and summary(result)['verified_text_chars'] >= 80) if success else bool(result.get('error_category')) and not result.get('text'),
        'isolated_success_cache_or_failure_cooldown_persisted': bool(cache) and (status.get('verified_domains') == 1 if success else status.get('cooldown_domains') == 1),
        'production_route_cache_unchanged': digest(production_cache) == before,
    }
    report = {'at': stamp(), 'url': URL, 'passed': all(criteria.values()), 'criteria': criteria,
              'direct': direct_calls, 'vpn': vpn_calls, 'final': summary(result),
              'isolated_status': status, 'isolated_cache': cache,
              'simulation': injected, 'models_used': False,
              'source_read_through_vpn': success and len(vpn_calls) == 1,
              'limitations': ['The denial and VPN response are real HTTP observations. No inference of geographical blocking follows from a changed response.',
                             'The probe does not change the production route cache, research/finance databases, profiles, schedules or notifications.',
                             'An unsuccessful VPN response remains an explicit source gap, not verified content.']}
    atomic_json(directory / 'report.json', report)
    return report


def main():
    if os.name != 'posix' or ROOT != Path('/opt/iva') or len(sys.argv) != 1:
        raise RuntimeError('run_fixed_vps_probe_only')
    import pwd
    from iva_integration import research_priority
    if os.geteuid() != pwd.getpwnam('iva').pw_uid:
        raise RuntimeError('run_as_iva')
    if not RELEASE.is_dir() or OUTPUT.exists() or any(p.is_symlink() for p in (ROOT / 'data', RELEASE, OUTPUT)):
        raise RuntimeError('fresh_private_output_required')
    if not network.enabled():
        raise RuntimeError('existing_vpn_reader_unavailable')
    os.umask(0o077); OUTPUT.mkdir(mode=0o700)
    lease = {'job_id': 'diagnostic-http403-' + hashlib.sha256(str(OUTPUT).encode()).hexdigest()[:16],
             'cycle_id': 'diagnostic-public-http403-vpn-retry'}
    with deployment_read_lock(), patch.object(research_priority, 'STATUS', OUTPUT / 'priority-status.json'), research_lease(lease, OUTPUT):
        report = run_probe(OUTPUT)
    print(json.dumps({'passed': report['passed'], 'criteria': report['criteria'],
                      'direct': report['direct'], 'vpn': report['vpn'],
                      'source_read_through_vpn': report['source_read_through_vpn']}, ensure_ascii=True), flush=True)
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'passed': False, 'error_type': type(exc).__name__}), flush=True)
        raise SystemExit(2)
