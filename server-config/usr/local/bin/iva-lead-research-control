#!/usr/bin/python3
"""Install root:root0755 as /usr/local/bin/iva-lead-research-control.

sudoers permits iva to run only this executable without arguments. JSON stdin
has exactly action and a request digest. No command, unit, path or environment
is supplied by the model. Timer pause never stops an active research service.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

CONFIG = Path('/etc/lead-flow/research.json')
STATE = Path('/var/lib/iva-lead-research/control.json')
TIMER = 'lead-flow-research.timer'
SERVICE = 'lead-flow-research.service'
MANUAL = 'lead-flow-research-manual.service'


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def command(*arguments):
    completed = subprocess.run(['/usr/bin/systemctl', *arguments], capture_output=True,
        text=True, timeout=8, check=False, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'})
    if completed.returncode:
        raise RuntimeError('systemd_command_failed')
    return completed.stdout.strip()


def atomic_state(value):
    # Parent is installed root:iva0750 by deployment, never created by caller input.
    import grp
    gid = grp.getgrnam('iva').gr_gid
    fd, name = tempfile.mkstemp(prefix='.control-', dir=STATE.parent)
    try:
        os.fchmod(fd, 0o640)
        os.fchown(fd, 0, gid)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, STATE)
        directory = os.open(STATE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {'action', 'request_id'}:
        raise ValueError('invalid_control_request')
    if payload['action'] not in ('run', 'pause', 'resume'):
        raise ValueError('invalid_action')
    if not isinstance(payload['request_id'], str) or not re.fullmatch('[a-f0-9]{64}', payload['request_id']):
        raise ValueError('invalid_request_id')


def perform(payload, state, execute_command=command, persist=atomic_state):
    validate(payload)
    ident, action = payload['request_id'], payload['action']
    previous = state.get('requests', {}).get(ident)
    if previous:
        if previous.get('action') != action:
            raise ValueError('request_identity_conflict')
        if previous.get('state') == 'inflight':
            return {'ok': False, 'state': 'uncertain', 'error': 'control_outcome_unconfirmed',
                'replayed': True, 'email_sending': False, 'check_status': True}
        return {**previous['result'], 'replayed': True}
    state.setdefault('requests', {})[ident] = {'action': action, 'state': 'inflight', 'at': timestamp()}
    state.update(action=action, updated_at=timestamp())
    persist(state)
    try:
        if action == 'run':
            active = [execute_command('show', name, '--property=ActiveState', '--value') for name in (SERVICE, MANUAL)]
            if any(value in ('active', 'activating', 'deactivating', 'reloading') for value in active):
                result = {'ok': True, 'accepted': False, 'state': 'already_running'}
            else:
                execute_command('start', '--no-block', MANUAL)
                result = {'ok': True, 'accepted': True, 'state': 'requested', 'unit': MANUAL,
                    'completed': False, 'schedule_changed': False}
        elif action == 'pause':
            execute_command('disable', '--now', TIMER)
            result = {'ok': True, 'accepted': True, 'state': 'future_runs_paused',
                'current_cycle_interrupted': False}
        else:
            config = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
            if config.get('enabled') is not True:
                raise RuntimeError('worker_disabled_in_config')
            execute_command('enable', '--now', TIMER)
            result = {'ok': True, 'accepted': True, 'state': 'schedule_enabled', 'immediate_run_requested': False}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        error = str(exc) if str(exc) in ('worker_disabled_in_config', 'systemd_command_failed') else type(exc).__name__
        # A timeout may have happened after systemd accepted an operation. Never
        # replay an action automatically under the same owner request identity.
        result = {'ok': False, 'state': 'uncertain', 'error': error, 'check_status': True}
    result.update(email_sending=False, general_demand_schedule_changed=False)
    state['requests'][ident] = {'action': action, 'state': 'recorded', 'at': timestamp(), 'result': result}
    state['requests'] = dict(sorted(state['requests'].items(), key=lambda item: item[1].get('at', ''))[-128:])
    state.update(updated_at=timestamp(), result=result)
    persist(state)
    return result


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise PermissionError('root_helper_only')
    payload = sys.stdin.buffer.read(4097)
    if len(payload) > 4096:
        raise ValueError('request_too_large')
    payload = json.loads(payload)
    validate(payload)
    import fcntl
    # Directory is non-writable by iva; reject accidental unsafe deployment.
    info = STATE.parent.stat()
    if info.st_uid != 0 or info.st_mode & 0o022 or STATE.parent.is_symlink():
        raise PermissionError('unsafe_control_directory')
    with open(STATE.parent / 'control.lock', 'a+b') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
        print(json.dumps(perform(payload, state), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error_type': type(exc).__name__, 'email_sending': False}))
        raise SystemExit(1)
