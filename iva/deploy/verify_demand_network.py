"""Bounded live public-reader check; no production routing-cache or DB writes."""
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path('/opt/iva')


def main():
    if os.name != 'posix' or Path.cwd() != ROOT or len(sys.argv) != 2:
        raise RuntimeError('Use on the active VPS with a fresh private output directory')
    directory = Path(sys.argv[1])
    if (not directory.is_absolute() or directory.exists() or not directory.parent.is_dir()
            or not directory.parent.name.startswith('demand-release-') or directory.parent.parent != ROOT / 'data'):
        raise ValueError('invalid_network_probe_output')
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError('network_probe_symlink_rejected')
    directory.mkdir(mode=0o700)
    sys.path.insert(0, str(ROOT))
    from deploy.verify_demand_agents import deployment_read_lock
    from deploy.verify_demand_branch import atomic_json, stamp, research_lease
    from iva_integration import demand_network as network, research_priority
    from iva_integration.demand_sources import _fetch_source_direct

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    def summary(result):
        return {key: result.get(key) for key in
                ('ok', 'http_status', 'error_category', 'canonical_url', 'network')} | {
                    'verified_text_chars': len(result.get('text', '')),
                    'has_content_hash': bool(result.get('content_hash'))}

    route_file = network.STATE / 'routes.json'
    before = digest(route_file)
    lease = {'job_id': 'diagnostic-network-' + hashlib.sha256(str(directory).encode()).hexdigest()[:16],
             'cycle_id': 'diagnostic-public-reader'}
    with deployment_read_lock(), patch.object(research_priority, 'STATUS', directory / 'priority-status.json'), \
            research_lease(lease, directory):
        if not network.enabled():
            raise RuntimeError('research_reader_unavailable')
        observed = []
        # Real public reads: reports contain only access metadata, never page text.
        for url in ('https://example.com/', 'https://habr.com/ru/docs/help/comments/',
                    'https://www.reddit.com/r/smallbusiness/'):
            result = _fetch_source_direct(url)
            observed.append({'route': 'direct', 'result': summary(result)})
            print(json.dumps({'event': 'direct_probe', **observed[-1]}), flush=True)
        real_vpn = network.vpn_fetch('https://example.com/')
        print(json.dumps({'event': 'vpn_probe', 'result': summary(real_vpn)}), flush=True)
        state = directory / 'route-state'
        direct_calls = []
        def simulated_geo(url):
            direct_calls.append(url)
            return {'ok': False, 'url': url, 'canonical_url': url, 'text': '',
                    'http_status': 403, 'error_category': 'geographic_restriction'}
        # Only the original geo error is simulated; both VPN GETs use the live service.
        with patch.object(network, 'STATE', state):
            retried = network.fetch('https://example.com/', simulated_geo)
            cached = network.fetch('https://example.com/', simulated_geo)
            isolated_status = network.status()
        rejected = []
        for url in ('http://127.0.0.1/', 'http://169.254.169.254/latest/meta-data/',
                    'https://example.com/?access_token=diagnostic'):
            try:
                result = network.vpn_fetch(url)
                rejected.append(result.get('ok') is not True)
            except (ValueError, TypeError):
                rejected.append(True)
        criteria = {
            'real_vpn_source_read': real_vpn.get('ok') is True,
            'simulated_geo_causes_real_vpn_retry': retried.get('ok') is True and
                retried.get('network', {}).get('retry') is True,
            'verified_host_cached_in_isolated_state': cached.get('ok') is True and
                cached.get('network', {}).get('reason') == 'cached_geographic_restriction'
                and len(direct_calls) == 1 and isolated_status.get('verified_domains') == 1,
            'private_or_token_urls_rejected': all(rejected),
            'production_route_cache_unchanged': digest(route_file) == before,
        }
        report = {'at': stamp(), 'passed': all(criteria.values()), 'criteria': criteria,
                  'real_direct_reads': observed, 'real_vpn_read': summary(real_vpn),
                  'simulated_geo_real_retry': summary(retried), 'cached_retry': summary(cached),
                  'limitations': ['Geographic restriction was simulated; VPN requests were real.',
                                  'A generic Reddit 403 or anti-bot challenge is not a geographic restriction.',
                                  'No research database, financial state, production routing cache or notifications changed.']}
        atomic_json(directory / 'final-report.json', report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if not report['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
