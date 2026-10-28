"""Install reviewed OCR lead worker and native Iva control without financial writes.

Invoked on VPS from a root-only staged release. Source/base manifests are supplied
by the pinned SSH staging client. Timers are never enabled by this installer.
"""
import argparse
from contextlib import closing, contextmanager, ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/opt/iva')
LEAD = Path('/opt/lead-flow')
RELEASE = ROOT / 'data/lead-flow-release-20261026'


def run(args, **kwargs):
    options = {'check': True, 'text': True, 'timeout': 30}
    if 'stdout' not in kwargs and 'stderr' not in kwargs:
        options['capture_output'] = True
    return subprocess.run(args, **(options | kwargs))


def hashed(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError('symlink_target_refused')
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def backup_path(target):
    return RELEASE/'before'/Path(*target.parts[1:])


def put(source, target, owner, mode):
    hashed(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = backup_path(target)
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
    temporary = target.with_name('.' + target.name + '.lead-update')
    with temporary.open('xb') as stream:
        stream.write(source.read_bytes()); stream.flush(); os.fsync(stream.fileno())
    os.chown(temporary, *owner); os.chmod(temporary, mode)
    os.replace(temporary, target)


def private_dir(path, owner, mode):
    hashed(path) if path.is_file() else None
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError('symlink_directory_refused')
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, *owner); os.chmod(path, mode)


def wait_inactive(unit, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = run(['systemctl', 'show', unit, '--property=ActiveState', '--value']).stdout.strip()
        if state == 'inactive':
            return
        if state == 'failed':
            raise RuntimeError('service_stop_failed:' + unit)
        time.sleep(0.2)
    raise RuntimeError('service_stop_pending:' + unit)


@contextmanager
def idle_model_locks():
    """Nonblocking observation/admission, no model call or credential read."""
    with ExitStack() as stack:
        for path in (Path('/home/iva/.codex/iva-auth.lock'), ROOT/'data/opencode-runtime/inference.lock'):
            if not path.is_file() or path.is_symlink():
                raise RuntimeError('model_admission_lock_missing')
            handle = stack.enter_context(path.open('r+'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def stop_idle_engine(scheduler_active, lifecycle):
    """Close admission briefly, then wait for engine exit OUTSIDE the writer lock."""
    from deploy.install_recovery_notices import require_quiet
    with idle_model_locks(), closing(sqlite3.connect(
            (ROOT/'data/local-finance.sqlite3').as_uri()+'?mode=rw', uri=True,
            timeout=0.2, isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            require_quiet()
            if db.execute("SELECT count(*) FROM scheduled_outbox WHERE state='sending'").fetchone()[0]:
                raise RuntimeError('scheduled_delivery_inflight')
            # Scheduler has no long-poll shutdown: at a confirmed idle boundary
            # stop it synchronously so it cannot claim a new model job mid-build.
            if scheduler_active:
                lifecycle['scheduler_stop_requested'] = True
                run(['systemctl', 'stop', 'iva@scheduler.service'], timeout=3)
            lifecycle['engine_stop_requested'] = True
            run(['systemctl', '--no-block', 'stop', 'iva@engine.service'], timeout=2)
        finally:
            db.execute('ROLLBACK')
    wait_inactive('iva@engine.service')


def verify_native_ready():
    private = json.loads((ROOT/'data/iva-pilot/control.json').read_text())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for key, suffix in (('iva_port', '/'), ('gateway_port', '/health')):
        port = private.get(key)
        if type(port) is not int or not 1 <= port <= 65535:
            raise RuntimeError('invalid_health_port')
        deadline = time.monotonic()+45
        while True:
            try:
                with opener.open(f'http://127.0.0.1:{port}{suffix}', timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('native_service_not_ready:' + key)
            time.sleep(0.25)
    request = urllib.request.Request(f'http://127.0.0.1:{private["gateway_port"]}/lead-research',
        data=b'{"action":"status"}', headers={'Content-Type':'application/json',
        'Authorization':'Bearer '+private['agent_token']})
    with opener.open(request, timeout=10) as response:
        result = json.loads(response.read(262144))
    if result.get('ok') is not True or result.get('email_sending') is not False:
        raise RuntimeError('lead_tool_not_verified')


def install_control(manifest, root_group):
    """Add a compiled tool at an idle boundary; restore source/output on failure."""
    from deploy.install_recovery_notices import (pause_telegram, restore_telegram,
        restart_gateway, require_quiet, unit_state, protected_snapshot)
    app = ROOT/'data/iva-pilot/app'
    output = app/'.output'
    before_output = RELEASE/'before-control-output'
    before_summary = RELEASE/'before-control-agent-summary.json'
    summary = app/'.eve/agent-summary.json'
    if not output.is_dir() or output.is_symlink() or before_output.exists():
        raise RuntimeError('compiled_backup_boundary_unavailable')
    if unit_state('is-active','iva@engine.service') != 'active' or unit_state('is-active','iva@gateway.service') != 'active':
        raise RuntimeError('native_runtime_not_stably_active')
    require_quiet()
    original = protected_snapshot()
    telegram_active = unit_state('is-active','iva@telegram.service') == 'active'
    scheduler_active = unit_state('is-active','iva@scheduler.service') == 'active'
    metadata = {}
    for item in manifest:
        target = Path(item['target'])
        if target.exists():
            stat = target.stat()
            metadata[str(target)] = {'uid':stat.st_uid,'gid':stat.st_gid,'mode':stat.st_mode & 0o777}
        else:
            metadata[str(target)] = None
    (RELEASE/'control-before-metadata.json').write_text(json.dumps(metadata,indent=2))
    if summary.exists():
        shutil.copy2(summary,before_summary)
    old_tools = {x.get('name') for x in json.loads(summary.read_text()).get('tools',[])} if summary.exists() else set()
    lifecycle = {'engine_stop_requested':False,'scheduler_stop_requested':False}
    compiled_moved = False
    gateway_reloaded = False
    wrote_sources = False
    try:
        if telegram_active:
            pause_telegram()
        # All waits/builds below happen after the short admission transaction.
        stop_idle_engine(scheduler_active,lifecycle)
        for item in manifest:
            put(RELEASE/'source'/item['source'], Path(item['target']),
                (0,0) if item.get('root_only') else root_group, item.get('mode',0o640))
            wrote_sources = True
        run(['visudo','-cf','/etc/sudoers.d/iva-lead-research'])
        run(['systemctl','daemon-reload'])
        # Atomic rename retains the exact previously running compiled bundle.
        os.rename(output,before_output)
        compiled_moved = True
        build = "from iva_integration.run_pilot import APP,env; import subprocess; subprocess.run(['node','node_modules/eve/bin/eve.js','build'],cwd=APP,env=env(),check=True)"
        with (RELEASE/'control-build.log').open('w') as log:
            run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-c',build],
                cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=300)
        built = json.loads(summary.read_text())
        tools = {x.get('name') for x in built.get('tools',[])}
        if 'lead_research' not in tools or not old_tools.issubset(tools):
            raise RuntimeError('compiled_tool_manifest_changed_unexpectedly')
        gateway_reloaded = True
        restart_gateway()
        run(['systemctl','start','iva@engine.service'])
        verify_native_ready()
        if protected_snapshot() != original:
            raise RuntimeError('protected_runtime_state_changed')
        return {'engine_rebuilt':True,'engine_restarted':True,'gateway_restarted':True,
            'native_tool_verified':True,'protected_state_unchanged':True}
    except BaseException:
        # Owner transport/scheduler are still paused, so rollback cannot race a
        # newly admitted owner or scheduled job. Never restore any data database.
        if lifecycle['engine_stop_requested']:
            run(['systemctl','--no-block','stop','iva@engine.service'])
            wait_inactive('iva@engine.service')
        if wrote_sources:
            for item in manifest:
                target = Path(item['target'])
                previous = metadata[str(target)]
                if previous is None:
                    if target.exists():
                        target.unlink()
                else:
                    backup = backup_path(target)
                    if backup.exists():
                        put(backup,target,(previous['uid'],previous['gid']),previous['mode'])
        if compiled_moved:
            if output.exists():
                failed = RELEASE/'failed-control-output'
                if failed.exists():
                    raise RuntimeError('rollback_output_collision')
                os.rename(output,failed)
            os.rename(before_output,output)
        if before_summary.exists():
            shutil.copy2(before_summary,summary)
        run(['systemctl','daemon-reload'])
        if gateway_reloaded:
            restart_gateway()
        if lifecycle['engine_stop_requested']:
            run(['systemctl','start','iva@engine.service'])
        raise
    finally:
        if lifecycle['scheduler_stop_requested']:
            run(['systemctl','start','iva@scheduler.service'])
        if telegram_active:
            restore_telegram()


def install(kind):
    account = pwd.getpwnam('iva')
    iva = (account.pw_uid, account.pw_gid); root_group = (0, account.pw_gid)
    manifest = json.loads((RELEASE / ('manifest-' + kind + '.json')).read_text())
    for item in manifest:
        target = Path(item['target'])
        if not any(target.is_relative_to(root) for root in (ROOT, LEAD, Path('/etc/systemd/system'), Path('/usr/local/bin'), Path('/etc/sudoers.d'))):
            raise RuntimeError('unexpected_target_root')
        if hashed(RELEASE / 'source' / item['source']) != item['sha256']:
            raise RuntimeError('staged_source_changed')
        if hashed(target) != item['before']:
            raise RuntimeError('runtime_changed_since_staging:' + str(target))
    for unit in ('lead-flow-research.service','lead-flow-research-manual.service'):
        state = subprocess.run(['systemctl','is-active',unit], capture_output=True,text=True).stdout.strip()
        if state in ('active','activating','deactivating'):
            raise RuntimeError('lead_worker_busy')
    if kind == 'control':
        sys.path.insert(0, str(ROOT))
        from deploy.install_recovery_notices import require_quiet
        # Validate staged privileges before pausing any live service.
        for item in manifest:
            if item['target'].startswith('/etc/sudoers.d/'):
                run(['visudo','-cf',str(RELEASE/'source'/item['source'])])
        require_quiet()
    private_dir(LEAD, root_group, 0o750)
    private_dir(LEAD/'data', iva, 0o700)
    private_dir(Path('/etc/lead-flow'), root_group, 0o750)
    private_dir(ROOT/'data/lead-flow', iva, 0o700)
    private_dir(Path('/var/lib/iva-lead-research'), root_group, 0o750)
    if kind == 'control':
        detail = install_control(manifest,root_group)
        report = {'kind':kind,'installed_files':len(manifest),'hashes_verified':all(hashed(Path(x['target']))==x['sha256'] for x in manifest),
                  'timers_enabled_by_installer':False,'emails_sent':0,'old_research_schedule_changed':False,**detail}
        (RELEASE/('installation-'+kind+'.json')).write_text(json.dumps(report,indent=2))
        print(json.dumps(report))
        return
    for item in manifest:
        put(RELEASE/'source'/item['source'], Path(item['target']),
            (0,0) if item.get('root_only') else root_group, item.get('mode',0o640))
    if kind == 'runtime':
        config = Path('/etc/lead-flow/research.json')
        if not config.exists():
            value = json.loads((LEAD/'worker/config.example.json').read_text())
            value.update(enabled=False, max_candidates=3, max_pages=18, max_model_calls=18)
            value['queries'] = value['queries'][:2]
            temp = RELEASE/'pilot-config.json'
            temp.write_text(json.dumps(value,ensure_ascii=False,indent=2))
            put(temp, config, root_group, 0o640)
        mail = Path('/etc/lead-flow/mail.json')
        if not mail.exists() and (LEAD/'mailer/config.example.json').exists():
            put(LEAD/'mailer/config.example.json',mail,root_group,0o640)
    if kind == 'control':
        run(['visudo','-cf','/etc/sudoers.d/iva-lead-research'])
    run(['systemctl','daemon-reload'])
    units = [str(Path(item['target'])) for item in manifest if item['target'].endswith(('.service','.timer'))]
    if units:
        run(['systemd-analyze','verify', *units])
    # Module reload has a tested owner/research idle boundary and graceful
    # Telegram pause. The financial admission transaction is rolled back.
    if kind == 'control':
        from deploy.install_recovery_notices import pause_telegram, restore_telegram, restart_gateway, unit_state
        active = unit_state('is-active','iva@telegram.service') == 'active'
        try:
            if active: pause_telegram()
            restart_gateway()
        finally:
            if active: restore_telegram()
    report = {'kind':kind,'installed_files':len(manifest),'hashes_verified':all(hashed(Path(x['target']))==x['sha256'] for x in manifest),
              'timers_enabled_by_installer':False,'emails_sent':0,'old_research_schedule_changed':False}
    (RELEASE/('installation-'+kind+'.json')).write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('kind',choices=('runtime','control'));args=parser.parse_args()
    if os.geteuid()!=0:raise RuntimeError('root_installer_required')
    with (ROOT/'data/application-deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        install(args.kind)


if __name__=='__main__':main()
