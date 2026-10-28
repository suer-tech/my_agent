"""Install only the research reader; never restart or rewrite the existing VPN."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = Path('/opt/iva')
HELPER = Path('/usr/local/lib/iva-research-fetch/server.py')
UNIT = Path('/etc/systemd/system/iva-research-fetch.service')
NAME = UNIT.name


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def rollback_reader(existing, old_active, old_enabled, release, host_route, vpn_route, replace_source):
    """Attempt every restoration step; never log subprocess output or routes."""
    failed = []

    def attempt(label, action):
        try:
            return action()
        except BaseException:
            failed.append(label)
            return None

    # Keep these independent: a broken stop must not prevent disabling or
    # restoring the installed files, and a broken helper restore must not skip
    # the unit restore. Only this reader is managed, never iva-vpn or its routes.
    attempt('stop_reader', lambda: run(['systemctl', 'stop', NAME]))
    attempt('disable_reader', lambda: run(['systemctl', 'disable', NAME]))
    for label, path in (('restore_helper', HELPER), ('restore_unit', UNIT)):
        if path in existing:
            attempt(label, lambda path=path: replace_source(existing[path], path, 'root', 'root', 0o644))
        else:
            attempt(label, lambda path=path: path.unlink(missing_ok=True))
    attempt('reload_units', lambda: run(['systemctl', 'daemon-reload']))
    safe_to_start = not any(label in failed for label in ('restore_helper', 'restore_unit', 'reload_units'))
    for requested, label, verb in ((old_enabled, 'enable_previous_reader', 'enable'),
                                   (old_active, 'start_previous_reader', 'start')):
        if requested and safe_to_start:
            attempt(label, lambda verb=verb: run(['systemctl', verb, NAME]))
        elif requested:
            failed.append(label + '_skipped_incomplete_restore')

    # Observe only. Unexpected external route changes must be reported, not
    # overwritten by a research-reader rollback that has no routing ownership.
    unchanged = {}
    for label, command, expected in (
            ('host', ['ip', 'route', 'show', 'default'], host_route),
            ('vpn', ['ip', '-n', 'iva-codex', 'route', 'show', 'default'], vpn_route)):
        result = attempt(label + '_route_check', lambda command=command: run(command))
        unchanged[label + '_route_unchanged'] = None if result is None else result.stdout == expected
        if unchanged[label + '_route_unchanged'] is False:
            failed.append(label + '_route_changed')
    report = {'rollback_complete': not failed, 'failed_steps': failed, **unchanged,
              'vpn_restarted': False, 'routes_modified': False}
    try:
        (release / 'network-rollback.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    except BaseException:
        failed.append('write_rollback_report')
        report['rollback_complete'] = False
    return report


def main():
    import fcntl
    if os.geteuid() != 0 or Path.cwd() != ROOT:
        raise RuntimeError('Run as root in /opt/iva')
    release = Path(sys.argv[1]).resolve()
    if not release.is_relative_to(ROOT / 'data') or not release.name.startswith('demand-release-'):
        raise ValueError('invalid_release')
    sys.path.insert(0, str(ROOT))
    from deploy.upgrade_demand_research import idle, replace_source
    with (ROOT / 'data/application-deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not idle():
            raise RuntimeError('Wait for active research or owner work to finish')
        report = json.loads((release / 'installation.json').read_text())
        required = ('deploy/research_fetch_service.py', 'deploy/systemd/' + NAME,
                    'iva_integration/demand_network.py', 'iva_integration/demand_sources.py')
        if not report.get('installed') or any(sha(ROOT / rel) != report['sha256'].get(rel) for rel in required):
            raise RuntimeError('Successful verified code installation required')
        expected = json.loads((release / 'expected-network.json').read_text())
        for path in (HELPER, UNIT):
            if path.is_symlink() or sha(path) != expected[str(path)]:
                raise RuntimeError('Research reader changed since review')
        if run(['systemctl', 'is-active', 'iva-vpn']).stdout.strip() != 'active':
            raise RuntimeError('Existing VPN must already be active')
        host_route = run(['ip', 'route', 'show', 'default']).stdout
        vpn_route = run(['ip', '-n', 'iva-codex', 'route', 'show', 'default']).stdout
        if 'dev awg-iva' not in vpn_route:
            raise RuntimeError('Expected isolated VPN route missing')
        before = release / 'network-before'
        before.mkdir(mode=0o700)
        old_active = subprocess.run(['systemctl', 'is-active', NAME], capture_output=True, text=True).stdout.strip() == 'active'
        old_enabled = subprocess.run(['systemctl', 'is-enabled', NAME], capture_output=True, text=True).stdout.strip() == 'enabled'
        existing = {}
        for path in (HELPER, UNIT):
            if path.exists():
                saved = before / path.name
                shutil.copy2(path, saved)
                existing[path] = saved
        try:
            if old_active:run(['systemctl', 'stop', NAME])
            replace_source(ROOT / 'deploy/research_fetch_service.py', HELPER, 'root', 'root', 0o644)
            replace_source(ROOT / 'deploy/systemd' / NAME, UNIT, 'root', 'root', 0o644)
            run(['systemd-analyze', 'verify', str(UNIT)])
            run(['systemctl', 'daemon-reload'])
            run(['systemctl', 'enable', '--now', NAME])
            deadline = time.monotonic() + 15
            while not Path('/run/iva-research-fetch/fetch.sock').is_socket():
                if time.monotonic() > deadline:
                    raise RuntimeError('Research reader socket did not become ready')
                time.sleep(.2)
            if run(['systemctl', 'is-active', NAME]).stdout.strip() != 'active':
                raise RuntimeError('Research reader is not active')
            if run(['ip', 'route', 'show', 'default']).stdout != host_route or run(['ip', '-n', 'iva-codex', 'route', 'show', 'default']).stdout != vpn_route:
                raise RuntimeError('Routing changed during reader installation')
            value = {'installed': True, 'reader_active': True, 'host_route_unchanged': True,
                     'vpn_route_unchanged': True, 'vpn_restarted': False,
                     'sha256': {str(path): sha(path) for path in (HELPER, UNIT)}}
            (release / 'network-installation.json').write_text(json.dumps(value, indent=2))
            print(json.dumps(value))
        except BaseException:
            # Cleanup/diagnostic failures must never replace the installation
            # exception; the caller needs the original cause for remediation.
            try:
                rollback_reader(existing, old_active, old_enabled, release, host_route, vpn_route, replace_source)
            except BaseException:
                try:
                    (release / 'network-rollback.json').write_text(json.dumps({
                        'rollback_complete': False, 'failed_steps': ['rollback_internal_error'],
                        'host_route_unchanged': None, 'vpn_route_unchanged': None,
                        'vpn_restarted': False, 'routes_modified': False}, indent=2), encoding='utf-8')
                except BaseException:
                    pass
            raise


if __name__ == '__main__':
    main()
