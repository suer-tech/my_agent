"""Root-owned deterministic recovery of a wedged OpenCode provider context.

No model calls, application imports, notifications or financial writes. A healthy
global endpoint alone is insufficient: only three consecutive provider HTTP 503
observations admit recovery, and every safety condition is checked again while
holding the deployment and inference locks.
"""
from contextlib import closing, contextmanager
import base64
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path('/opt/iva')
STATE = Path('/var/lib/iva-opencode-health/state.json')
EVENTS = Path('/var/lib/iva-opencode-health-events/events.json')
DEPLOY_LOCK = ROOT / 'data/application-deploy.lock'
INFERENCE_LOCK = ROOT / 'data/opencode-runtime/inference.lock'
PASSWORD = ROOT / 'data/opencode-runtime/server-password'
DATABASE = ROOT / 'data/local-finance.sqlite3'
SOCKET = '/run/iva-browser/control.sock'
BASE_URL = 'http://127.0.0.1:18726'
DIRECTORY = '/home/iva/.local/share/iva-opencode/work'
UNIT = 'iva-opencode.service'
THRESHOLD = 3
COOLDOWN = 900
HOUR = 3600
MAX_RESTARTS = 2
MAX_GAP = 600
MAX_STATE = 16384
MAX_EVENTS = 32
OBSERVATIONS = {'healthy', 'provider_503', 'global_unhealthy', 'probe_unknown'}


def regular_open(path, flags, mode=0o600):
    path = Path(path)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise OSError('unsafe_path')
    descriptor = os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0), mode)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError('not_regular')
    return descriptor


@contextmanager
def exclusive(path, create=False):
    import fcntl
    descriptor = regular_open(path, (os.O_RDWR | os.O_CREAT) if create else os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def save_state(path, state):
    data = json.dumps(state, sort_keys=True, allow_nan=False).encode()
    if len(data) > MAX_STATE or Path(path).is_symlink():
        raise ValueError('invalid_state_target')
    descriptor, name = tempfile.mkstemp(prefix='.state-', dir=Path(path).parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == 'posix':
            directory = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def clean_event(value):
    """Only immutable, non-secret evidence from a confirmed service restart."""
    if not isinstance(value, dict):
        return None
    ident = value.get('id')
    proof = value.get('proof')
    verified = value.get('verified_at')
    if (not isinstance(ident, str) or not re.fullmatch(r'opencode:service_restart:[0-9a-f]{32}', ident)
            or value.get('source') != 'opencode' or value.get('code') != 'provider_unavailable'
            or value.get('action') != 'service_restart' or not isinstance(proof, dict)
            or not number(verified) or not number(proof.get('attempted_at'))
            or not 0 < proof['attempted_at'] <= verified
            or any(proof.get(key) is not True for key in
                   ('restart_succeeded', 'global_healthy', 'provider_available', 'provider_503_confirmed'))):
        return None
    return {'id': ident, 'source': 'opencode', 'code': 'provider_unavailable',
            'action': 'service_restart', 'verified_at': verified,
            'proof': {'attempted_at': proof['attempted_at'], 'restart_succeeded': True,
                      'global_healthy': True, 'provider_available': True, 'provider_503_confirmed': True}}


def clean_events(values):
    result = {}
    if isinstance(values, list):
        for value in values[-MAX_EVENTS:]:
            event = clean_event(value)
            if event is not None:
                result.setdefault(event['id'], event)
    return list(result.values())[-MAX_EVENTS:]


def save_events(path, state):
    """Export sanitized evidence without opening the private watchdog state.

    The fixed directory is created by the root installer, never by the app. It
    is root-owned/group-readable and cannot be modified by the notifier. A
    directory descriptor and O_NOFOLLOW prevent symlink substitution at write.
    """
    import grp
    path = Path(path)
    if path != EVENTS or any(item.is_symlink() for item in (path, *path.parents)):
        raise OSError('unsafe_events_path')
    gid = grp.getgrnam('iva').gr_gid
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = '.events-' + uuid.uuid4().hex
    try:
        info = os.fstat(descriptor)
        if info.st_uid != 0 or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o750:
            raise OSError('unsafe_events_directory')
        try:
            info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != gid
                    or stat.S_IMODE(info.st_mode) != 0o640 or info.st_nlink != 1):
                raise OSError('unsafe_events_file')
        value = {'version': 1, 'checked_at': state['checked_at'], **clean_probe(state),
                 'events': clean_events(state.get('recovery_events'))}
        data = json.dumps(value, sort_keys=True, allow_nan=False).encode()
        if len(data) > MAX_STATE:
            raise ValueError('oversized_events')
        target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=descriptor)
        with os.fdopen(target, 'wb') as stream:
            os.fchown(stream.fileno(), 0, gid)
            os.fchmod(stream.fileno(), 0o640)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        os.close(descriptor)


def initial_state():
    return {'version': 1, 'checked_at': 0, 'consecutive_provider_503': 0,
            'restart_attempts': [], 'blocked_until': 0, 'recovery_events': []}


def load_state(path, clock):
    try:
        descriptor = regular_open(path, os.O_RDONLY)
    except FileNotFoundError:
        return initial_state()
    try:
        with os.fdopen(descriptor, 'rb') as stream:
            raw = stream.read(MAX_STATE + 1)
        if len(raw) > MAX_STATE:
            raise ValueError('oversized_state')
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('version') != 1:
            raise ValueError('invalid_state')
        count = value['consecutive_provider_503']
        attempts = value['restart_attempts']
        if (type(count) is not int or not 0 <= count <= THRESHOLD or not isinstance(attempts, list)
                or len(attempts) > MAX_RESTARTS or not all(number(item) for item in attempts)
                or not number(value['checked_at']) or not number(value.get('blocked_until', 0))):
            raise ValueError('invalid_state')
        # Unknown fields, including accidentally stored provider bodies, are
        # never copied into the next state or emitted to journald.
        result = {key: value[key] for key in initial_state() if key in value}
        result.setdefault('blocked_until', 0)
        result['recovery_events'] = clean_events(value.get('recovery_events'))
        result['restart_attempts'] = sorted(attempts)
        attempt = value.get('last_restart_attempt')
        if (isinstance(attempt, dict) and isinstance(attempt.get('id'), str)
                and re.fullmatch(r'opencode:service_restart:[0-9a-f]{32}', attempt['id'])
                and number(attempt.get('attempted_at'))):
            result['last_restart_attempt'] = {'id': attempt['id'], 'attempted_at': attempt['attempted_at']}
        if value.get('last_restart_result') in ('attempted', 'succeeded', 'failed', 'unhealthy'):
            result['last_restart_result'] = value['last_restart_result']
        return result
    except (ValueError, KeyError, TypeError):
        result = initial_state()
        result.update(blocked_until=clock + HOUR, state_recovered=True)
        return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Runtime:
    def state_lock(self, path):
        return exclusive(path, create=True)

    @contextmanager
    def recovery_locks(self):
        with exclusive(DEPLOY_LOCK), exclusive(INFERENCE_LOCK):
            yield

    def http(self, endpoint):
        if endpoint not in ('/global/health', '/provider', '/session/status'):
            raise ValueError('unsupported_health_endpoint')
        descriptor = regular_open(PASSWORD, os.O_RDONLY)
        with os.fdopen(descriptor, 'rb') as stream:
            secret = stream.read(1025).strip()
        if not secret or len(secret) > 1024:
            raise ValueError('invalid_password')
        auth = base64.b64encode(b'opencode:' + secret).decode('ascii')
        url = BASE_URL + endpoint + '?' + urllib.parse.urlencode({'directory': DIRECTORY})
        req = urllib.request.Request(url, headers={'Authorization': 'Basic ' + auth})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=3) as response:
                body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    raise ValueError('oversized_response')
                return response.status, json.loads(body)
        except urllib.error.HTTPError as error:
            # HTTP status is enough; never consume/log a provider error body.
            error.close()
            return error.code, None

    def probe(self):
        result = {'observation': 'probe_unknown', 'global_http_status': None, 'provider_http_status': None}
        try:
            status, body = self.http('/global/health')
            result['global_http_status'] = status
            if status != 200 or not isinstance(body, dict) or body.get('healthy') is not True:
                result['observation'] = 'global_unhealthy'
                return result
            status, body = self.http('/provider')
            result['provider_http_status'] = status
            if status == 503:
                result['observation'] = 'provider_503'
            elif status == 200 and isinstance(body, dict) and isinstance(body.get('all'), list):
                result['observation'] = 'healthy'
        except (OSError, ValueError, TypeError):
            pass
        return result

    def unit_active(self):
        result = subprocess.run(['systemctl', 'show', UNIT, '--property=ActiveState,SubState,User'],
                                capture_output=True, text=True, timeout=3)
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        return result.returncode == 0 and values == {'ActiveState': 'active', 'SubState': 'running', 'User': 'iva'}

    def owner_idle(self):
        with closing(sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            return db.execute("SELECT count(*) FROM local_updates WHERE state='processing'").fetchone()[0] == 0

    def research_idle(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(SOCKET)
            client.sendall(b'{"action":"research-status"}\n')
            data = b''
            while b'\n' not in data:
                chunk = client.recv(4096)
                if not chunk or len(data) + len(chunk) > 16384:
                    raise OSError('invalid_coordinator_response')
                data += chunk
        value = json.loads(data.split(b'\n', 1)[0])
        return (isinstance(value, dict) and value.get('ok') is True and value.get('status') == 'idle'
                and value.get('active') is False and value.get('granted') is False)

    def safe(self):
        try:
            if not self.unit_active():
                return 'service_not_active'
            if not self.owner_idle():
                return 'owner_busy'
            if not self.research_idle():
                return 'research_busy'
            status, sessions = self.http('/session/status')
            if status != 200 or not isinstance(sessions, dict):
                return 'sessions_unknown'
            if sessions:
                return 'sessions_busy'
            return None
        except (OSError, ValueError, TypeError, sqlite3.Error, subprocess.SubprocessError):
            return 'safety_unknown'

    def restart(self):
        result = subprocess.run(['systemctl', 'restart', UNIT], capture_output=True, timeout=30)
        return result.returncode == 0

    def ready(self):
        deadline = time.monotonic() + 10
        observed = self.probe()
        while observed['observation'] != 'healthy' and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
            observed = self.probe()
        return observed


def clean_probe(probe):
    if not isinstance(probe, dict) or probe.get('observation') not in OBSERVATIONS:
        return {'observation': 'probe_unknown', 'global_http_status': None, 'provider_http_status': None}
    result = {'observation': probe['observation'], **{key: probe.get(key) if type(probe.get(key)) is int
                and 100 <= probe[key] <= 599 else None for key in ('global_http_status', 'provider_http_status')}}
    if ((result['observation'] == 'provider_503' and
            (result['global_http_status'] != 200 or result['provider_http_status'] != 503)) or
            (result['observation'] == 'healthy' and
             (result['global_http_status'] != 200 or result['provider_http_status'] != 200))):
        result['observation'] = 'probe_unknown'
    return result


def budget_reason(state, clock):
    if state.get('blocked_until', 0) > clock:
        return 'state_cooldown'
    if state['restart_attempts'] and clock - max(state['restart_attempts']) < COOLDOWN:
        return 'restart_cooldown'
    if len(state['restart_attempts']) >= MAX_RESTARTS:
        return 'hourly_restart_limit'
    return None


def tick(path=STATE, runtime=None, clock=None, events_path=None):
    runtime = runtime or Runtime()
    clock = time.time() if clock is None else clock
    started = time.monotonic()
    with runtime.state_lock(Path(path).parent / 'monitor.lock'):
        state = load_state(path, clock)
        previous_at = state['checked_at']
        state['restart_attempts'] = [stamp for stamp in state['restart_attempts'] if clock - stamp < HOUR]
        observed = clean_probe(runtime.probe())
        count = state['consecutive_provider_503'] if 0 <= clock - previous_at <= MAX_GAP else 0
        if observed['observation'] == 'provider_503':
            count = min(THRESHOLD, count + 1)
        else:
            count = 0
        state.update(observed, checked_at=clock, consecutive_provider_503=count, outcome='observed')

        def finish(outcome):
            state['outcome'] = outcome
            state.pop('event_export_failed', None)
            save_state(path, state)
            if events_path is not None:
                try:
                    save_events(events_path, state)
                except (OSError, ValueError, KeyError):
                    # Durable evidence is already in root state. The next tick
                    # retries export; no failed telemetry write repeats a repair.
                    state['event_export_failed'] = True
            return state

        if previous_at > clock:
            state['blocked_until'] = max(state['blocked_until'], previous_at)
            return finish('clock_rollback')
        if count < THRESHOLD:
            return finish('healthy' if observed['observation'] == 'healthy' else 'observed')
        reason = budget_reason(state, clock)
        if reason:
            return finish(reason)
        reason = runtime.safe()
        if reason:
            return finish(reason)
        try:
            with runtime.recovery_locks():
                # Both checks are repeated under the same locks that protect
                # deployment and every normal OpenCode invocation/abort.
                confirmed = clean_probe(runtime.probe())
                state.update(confirmed)
                if confirmed['observation'] != 'provider_503':
                    state['consecutive_provider_503'] = 0
                    return finish('changed_before_restart')
                reason = runtime.safe()
                if reason:
                    return finish(reason)
                # Persist the attempt BEFORE systemctl. A crash or uncertain
                # service restart must still consume its cooldown/rate budget.
                state['restart_attempts'].append(clock)
                attempt = {'id': 'opencode:service_restart:' + uuid.uuid4().hex, 'attempted_at': clock}
                state.update(consecutive_provider_503=0, last_restart_result='attempted',
                             last_restart_attempt=attempt)
                finish('restart_attempted')
                try:
                    restarted = runtime.restart()
                except (OSError, subprocess.SubprocessError):
                    restarted = False
                if not restarted:
                    state['last_restart_result'] = 'failed'
                    return finish('restart_failed')
                after = clean_probe(runtime.ready())
                state.update(after)
                good = after['observation'] == 'healthy'
                state['last_restart_result'] = 'succeeded' if good else 'unhealthy'
                if good:
                    verified = clock + max(0, time.monotonic() - started)
                    state['checked_at'] = verified
                    event = {'id': attempt['id'], 'source': 'opencode', 'code': 'provider_unavailable',
                             'action': 'service_restart', 'verified_at': verified,
                             'proof': {'attempted_at': clock, 'restart_succeeded': True,
                                       'global_healthy': True, 'provider_available': True,
                                       'provider_503_confirmed': True}}
                    state['recovery_events'] = clean_events([*state['recovery_events'], event])
                return finish('recovered' if good else 'restart_completed_unhealthy')
        except BlockingIOError:
            return finish('recovery_lock_busy')
        except OSError:
            return finish('recovery_lock_unavailable')


def main():
    if os.name != 'posix' or os.geteuid() != 0:
        raise RuntimeError('run_as_root_on_vps')
    if any(path.is_symlink() for path in (STATE.parent, *STATE.parent.parents)):
        raise RuntimeError('unsafe_state_directory')
    STATE.parent.mkdir(mode=0o750, exist_ok=True)
    info = STATE.parent.stat()
    if info.st_uid != 0 or info.st_mode & 0o027:
        raise RuntimeError('private_root_state_required')
    result = tick(events_path=EVENTS)
    # Immutable history belongs in the bounded snapshot, not repeated in the
    # journal every minute. Keep routine health diagnostics small.
    summary = {key: value for key, value in result.items() if key != 'recovery_events'}
    summary['recovery_events_count'] = len(result.get('recovery_events', []))
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BlockingIOError:
        print('{"outcome":"monitor_busy"}')
    except Exception:
        # No exception text: it may originate from paths, auth or HTTP content.
        print('{"outcome":"monitor_failed"}')
        raise SystemExit(1)
