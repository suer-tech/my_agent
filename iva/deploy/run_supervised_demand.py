"""One supervised software-demand cycle through the normal research worker.

Run as iva in a transient oneshot with no restart policy. It creates no schedule,
does not write the financial database and never invokes scheduler reconciliation.
The real research DB, cards and native vault receive normal research results.
"""
from contextlib import closing, contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import tempfile
import time
import uuid

ROOT = Path('/opt/iva')
OUTPUT = ROOT / 'data/demand-supervised-20261025'
DATABASE = ROOT / 'data/demand-research.sqlite3'
MAX_STATE = 8 * 1024 * 1024
JOB_PATTERN = re.compile(r'supervised-software-(?:a[2-9]-)?[0-9a-f]{24}\Z')
TERMINAL = {'completed', 'completed_with_gaps', 'failed'}
INTERNAL_ERRORS = {
    'supervised_cycle_scope_changed', 'supervised_software_start_unavailable',
    'supervised_multiple_cycles_refused', 'supervised_cycle_identity_changed',
    'supervised_anchored_cycle_missing', 'supervised_invalid_step_result',
    'supervised_result_cycle_mismatch', 'supervised_terminal_export_unconfirmed',
}
TASK = '''Провести один полный цикл исследования неудовлетворённого спроса на ПО
и приложения. Стартовая область: небольшие интернет-магазины и селлеры; ручной
перенос заказов, остатков и возвратов между таблицами и сервисами. Исследуются
программные решения и рабочие процессы пользователей, не торговля товарами для
MAX. Не считать проблему заранее доказанной. Оркестратор планирует независимые
проверки проблем и оплачиваемых обходов, существующих программ и жалоб на них,
зарубежных решений с отдельной оценкой применимости, а также контраргументов и
экономики. Углубления допускаются по действительно найденным вопросам с точными
цитатами, связью с основным вопросом и критериями проверки. Итог различает факты,
гипотезы, опровержения, пробелы данных и технические ограничения. Сохранить
свидетельства, отчёт и карточки обычным способом. Только публичное исследование:
без сообщений, комментариев, покупок, авторизации или личных данных владельца.'''
TASK_SHA = hashlib.sha256(TASK.encode()).hexdigest()


def safe_path(path):
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise RuntimeError('supervised_path_is_symlink')


def output_path(attempt):
    if type(attempt) is not int or not 1 <= attempt <= 9:
        raise ValueError('supervised_attempt_must_be_1_to_9')
    return OUTPUT if attempt == 1 else OUTPUT.with_name(OUTPUT.name + '-attempt' + str(attempt))


def atomic(path, value):
    safe_path(path)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    if len(data) > MAX_STATE:
        raise RuntimeError('supervised_checkpoint_too_large')
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name == 'posix':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def load(directory, clock, attempt=1):
    output_path(attempt)  # Validate even when an isolated test supplies a directory.
    path = directory / 'state.json'
    safe_path(path)
    if not path.exists():
        prefix = 'supervised-software-' + ('' if attempt == 1 else 'a' + str(attempt) + '-')
        value = {'version': 1, 'attempt': attempt, 'job_id': prefix + uuid.uuid4().hex[:24],
                 'task_sha256': TASK_SHA, 'lane': 'software', 'cycle_id': None,
                 'state': 'waiting', 'cursor': {}, 'steps': 0, 'created_at': clock,
                 'updated_at': clock, 'retry_at': 0, 'last_result': None}
        atomic(path, value)  # Identity is durable before any research call.
        return value
    if path.stat().st_size > MAX_STATE:
        raise RuntimeError('supervised_checkpoint_too_large')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('job_id'), str) or not JOB_PATTERN.fullmatch(value['job_id'])
            or value.get('attempt', 1) != attempt
            or value.get('task_sha256') != TASK_SHA or value.get('lane') != 'software'
            or not isinstance(value.get('cursor'), dict) or type(value.get('steps')) is not int
            or value['steps'] < 0):
        raise RuntimeError('supervised_checkpoint_invalid')
    expected_prefix = 'supervised-software-' + ('' if attempt == 1 else 'a' + str(attempt) + '-')
    if not re.fullmatch(re.escape(expected_prefix) + r'[0-9a-f]{24}', value['job_id']):
        raise RuntimeError('supervised_checkpoint_attempt_mismatch')
    value.setdefault('attempt', 1)  # Existing first-run checkpoints remain resumable.
    return value


def summary(state):
    result = state.get('last_result') or {}
    result = result if isinstance(result, dict) else {}
    memory = result.get('memory_export') or {}
    memory = memory if isinstance(memory, dict) else {}
    return {key: state.get(key) for key in
            ('attempt', 'job_id', 'cycle_id', 'lane', 'state', 'steps', 'updated_at', 'retry_at',
             'wait_reason', 'error_type', 'error_code', 'lease_released')} | {
        'result_status': result.get('status'), 'stage': result.get('stage'),
        'ok': result.get('ok'), 'has_more': result.get('has_more'),
        'error_category': result.get('error_category'),
        'cards_persisted': result.get('cards_persisted'),
        'memory_export_ok': memory.get('ok'),
    }


class Runtime:
    def __init__(self, database=DATABASE):
        self.database = database
        self.stop = False

    def clock(self):
        return time.time()

    def wait(self, seconds):
        # A stop signal is honored only between committed work checkpoints.
        until = time.monotonic() + seconds
        while not self.stop and time.monotonic() < until:
            time.sleep(min(1, max(0, until - time.monotonic())))

    def status(self):
        from iva_integration import research_priority
        return research_priority.request({'action': 'research-status'})

    def cycles(self, job_id):
        with closing(sqlite3.connect(Path(self.database).as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                'SELECT id,job_id,sequence,track,topic,plan,state,stage FROM demand_cycles WHERE job_id=? ORDER BY sequence LIMIT 2',
                (job_id,))]

    def validate(self, cycle):
        from iva_integration import demand_scope
        plan = json.loads(cycle['plan']) if isinstance(cycle['plan'], str) else cycle['plan']
        if (cycle.get('sequence') != 0 or cycle.get('track') != 'software'
                or plan.get('version') != 3 or (plan.get('scope') or {}).get('version') != 1
                or demand_scope.brief(cycle)['lane'] != 'software'):
            raise RuntimeError('supervised_cycle_scope_changed')

    @contextmanager
    def deployment_guard(self):
        import fcntl
        path = ROOT / 'data/application-deploy.lock'
        safe_path(path)
        with path.open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            yield

    def step(self, state):
        from iva_integration.scheduled_worker import run_demand
        from iva_integration import demand_research
        if demand_research.TRACKS[0][0] != 'software' or not demand_research.AGENT_RESEARCH_ENABLED:
            raise RuntimeError('supervised_software_start_unavailable')
        return run_demand({'id': state['job_id'], 'cursor': json.dumps(state['cursor'])},
                          {'kind': 'demand_research', 'task': TASK})

    def release(self):
        from iva_integration import research_priority
        return research_priority.release()


def anchor(state, runtime):
    """Find even a terminal cycle committed just before a process crash.

    The normal scheduler clears its cursor after completion. This oneshot keeps
    its anchor forever, so a later hour or restart cannot create sequence one.
    """
    rows = runtime.cycles(state['job_id'])
    if len(rows) > 1:
        raise RuntimeError('supervised_multiple_cycles_refused')
    if rows:
        cycle = rows[0]
        runtime.validate(cycle)
        if cycle.get('job_id') != state['job_id'] or state.get('cycle_id') not in (None, cycle['id']):
            raise RuntimeError('supervised_cycle_identity_changed')
        state['cycle_id'] = cycle['id']
        state['cursor']['demand_cycle_id'] = cycle['id']
    elif state.get('cycle_id') or state['cursor'].get('demand_cycle_id'):
        raise RuntimeError('supervised_anchored_cycle_missing')


def save(directory, state, runtime):
    state['updated_at'] = runtime.clock()
    atomic(directory / 'state.json', state)
    atomic(directory / 'status.json', summary(state))


def release(directory, state, runtime):
    released = False
    for attempt in range(3):
        try:
            released = runtime.release() is True
        except Exception:
            released = False
        if released:
            break
        if attempt < 2:
            runtime.wait(2)
    state['lease_released'] = released
    save(directory, state, runtime)
    return released


def run(directory=None, runtime=None, attempt=1):
    directory = output_path(attempt) if directory is None else Path(directory)
    runtime = runtime or Runtime()
    state = load(directory, runtime.clock(), attempt)
    if state.get('state') in TERMINAL:
        return summary(state)  # No model, acquisition, or second cycle.
    try:
        while True:
            if runtime.stop:
                state.update(state='suspended', wait_reason='explicit_stop_at_checkpoint')
                save(directory, state, runtime)
                release(directory, state, runtime)
                return summary(state)
            anchor(state, runtime)
            status = runtime.status()
            if (status.get('ok') is not True or
                    (status.get('status') != 'idle' and status.get('job_id') != state['job_id'])):
                state.update(state='waiting', wait_reason='existing_research' if status.get('ok') else 'coordinator_unavailable')
                save(directory, state, runtime)
                runtime.wait(30)
                continue
            delay = max(0, state.get('retry_at', 0) - runtime.clock())
            if delay:
                runtime.wait(min(30, delay))
                continue
            with runtime.deployment_guard():
                # Reload/anchor before every call, including post-crash terminal
                # export. Existing core disk/model/browser guards stay intact.
                anchor(state, runtime)
                state.update(state='in_flight', wait_reason=None, error_type=None, error_code=None,
                             lease_released=None)
                save(directory, state, runtime)
                result = runtime.step(state)
                if not isinstance(result, dict) or not isinstance(result.get('cursor'), dict):
                    raise RuntimeError('supervised_invalid_step_result')
                state['cursor'] = result['cursor']
                state['last_result'] = result
                state['steps'] += 1
                anchor(state, runtime)
                if result.get('cycle_id') and result['cycle_id'] != state.get('cycle_id'):
                    raise RuntimeError('supervised_result_cycle_mismatch')
                requested = result.get('retry_after_seconds', 0 if result.get('ok') else 60)
                delay = max(0, min(3600, requested)) if type(requested) is int else 60
                state['retry_at'] = runtime.clock() + delay
                if result.get('status') == 'resource_blocked':
                    state['state'] = 'suspended'
                elif result.get('has_more') is True:
                    state['state'] = 'running'
                elif result.get('ok') is True and result.get('cards_persisted') is True:
                    memory = result.get('memory_export')
                    if (not isinstance(memory, dict) or memory.get('has_more') is True
                            or memory.get('errors') or (memory.get('ok') is not True and not memory.get('conflicts'))):
                        raise RuntimeError('supervised_terminal_export_unconfirmed')
                    state['state'] = ('completed_with_gaps' if result.get('status') in
                                      ('partial', 'completed_with_gaps') or memory.get('conflicts') else 'completed')
                else:
                    state['state'] = 'failed'
                # Persist the terminal/export result BEFORE releasing resources.
                if state['state'] in TERMINAL:
                    atomic(directory / 'final-result.json', result)
                save(directory, state, runtime)
            print(json.dumps(summary(state), ensure_ascii=False), flush=True)
            if state['state'] in TERMINAL or state['state'] == 'suspended':
                release(directory, state, runtime)
                return summary(state)
    except Exception as error:
        # Preserve the pre-call cursor; DB discovery recovers an unrecorded first
        # cycle or final commit next time. Exception text may contain source data.
        state.update(state='suspended', error_type=type(error).__name__,
                     error_code=str(error) if str(error) in INTERNAL_ERRORS else None,
                     wait_reason='technical_suspension', retry_at=runtime.clock() + 60)
        save(directory, state, runtime)
        release(directory, state, runtime)
        return summary(state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('run', 'status'))
    parser.add_argument('--attempt', type=int, choices=range(1, 10), default=1)
    args = parser.parse_args()
    directory = output_path(args.attempt)
    safe_path(directory)
    if args.action == 'status':
        path = directory / 'status.json'
        safe_path(path)
        print(path.read_text(encoding='utf-8') if path.exists() else '{"state":"not_started"}')
        return
    if os.name != 'posix' or Path.cwd() != ROOT:
        raise RuntimeError('run_on_iva_vps')
    import pwd
    import fcntl
    if os.geteuid() != pwd.getpwnam('iva').pw_uid:
        raise RuntimeError('run_as_iva')
    sys.path.insert(0, str(ROOT))
    from iva_integration.run_pilot import env
    os.environ.update(env())
    os.umask(0o077)
    directory.mkdir(mode=0o700, exist_ok=True)
    lock_path = directory / 'worker.lock'
    safe_path(lock_path)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime = Runtime()
        signal.signal(signal.SIGTERM, lambda *_: setattr(runtime, 'stop', True))
        signal.signal(signal.SIGINT, lambda *_: setattr(runtime, 'stop', True))
        try:
            result = run(directory, runtime=runtime, attempt=args.attempt)
        finally:
            # A failed checkpoint write must not strand an owned lease. Normal
            # completion already released after commit; duplicate release is safe.
            try:
                runtime.release()
            except Exception:
                pass
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result['state'] not in ('completed', 'completed_with_gaps'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
