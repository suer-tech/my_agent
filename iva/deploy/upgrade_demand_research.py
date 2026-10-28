"""Upgrade the existing research worker at an idle boundary, without rebuilding Iva."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path('/opt/iva')
JOB='2f9611a667cc493a'
RESEARCH=ROOT/'data/demand-research.sqlite3'
FILES=[
    'iva_integration/demand_research.py','iva_integration/demand_questions.py','iva_integration/demand_agents.py',
    'iva_integration/demand_sources.py','tests/test_demand_sources.py',
    'iva_integration/demand_scope.py','tests/test_demand_scope.py',
    'iva_integration/demand_quote_repair.py','tests/test_demand_quote_repair.py',
    'iva_integration/demand_methodology.py','tests/test_demand_methodology.py',
    'iva_integration/demand_network.py','tests/test_demand_network.py',
    'deploy/research_fetch_service.py','deploy/systemd/iva-research-fetch.service',
    'deploy/install_research_fetch.py','docs/DEMAND-OSINT-METHODS.ru.md',
    'deploy/verify_demand_scope.py','deploy/verify_demand_quote_repair.py',
    'deploy/verify_demand_network.py','tests/test_demand_network_install.py',
    'docs/DEMAND-SCOPE-AND-NETWORK.ru.md',
    'iva_integration/demand_retention.py','iva_integration/demand_schedule_retention.py','iva_integration/demand_storage.py',
    'tests/test_demand_retention.py','tests/test_demand_schedule_retention.py','tests/test_demand_storage.py',
    'deploy/backup.py','tests/test_backup_capacity.py',
    'deploy/verify_demand_retention.py','deploy/install_demand_retention_timer.py','tests/test_demand_retention_install.py',
    'deploy/systemd/iva-demand-retention.service','deploy/systemd/iva-demand-retention.timer',
    'iva_integration/scheduled_worker.py','iva_integration/scheduled_tasks.py','iva_integration/research_priority.py',
    'iva_integration/threads_hourly.py','deploy/threads_hourly_tick.py','deploy/browser_coordinator.py',
    'iva_integration/threads_alerts.py','iva_integration/threads_repair.py',
    'tests/test_demand_research.py','tests/test_demand_questions.py','tests/test_demand_agents.py','tests/test_demand_scheduler.py',
    'tests/test_demand_upgrade.py','deploy/upgrade_demand_research.py',
    'tests/test_demand_priority.py','tests/test_browser_turns.py','tests/test_threads_research_pause.py',
    'deploy/verify_demand_branch.py','tests/test_verify_demand_branch.py',
    'deploy/verify_demand_agents.py','tests/test_verify_demand_agents.py',
    'deploy/verify_demand_quality.py','tests/test_verify_demand_quality.py',
    'deploy/verify_demand_review.py','tests/test_verify_demand_review.py',
    'tests/test_threads_alerts.py','tests/test_threads_repair_queue.py',
    'docs/DEMAND-RESEARCH.ru.md','docs/DEMAND-MULTIAGENT.ru.md','docs/DEMAND-STORAGE.ru.md',
]
COORDINATOR=Path('/usr/local/lib/iva-browser/browser_coordinator.py')
SERVICES=['iva@scheduler','iva@telegram','iva@engine','iva@gateway']


def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
def replace_source(source,target,user='iva',group='iva',mode=None):
    """Readers, including browser timers, see either complete source version."""
    target.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(prefix='.'+target.name+'.deploy-',dir=target.parent)
    os.close(descriptor)
    temporary=Path(name)
    try:
        shutil.copy2(source,temporary)
        shutil.chown(temporary,user=user,group=group)
        if mode is not None:temporary.chmod(mode)
        with temporary.open('rb') as stream:os.fsync(stream.fileno())
        os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)
def finance():return sqlite3.connect(ROOT/'data/local-finance.sqlite3',timeout=15)
def idle():
    with finance() as db:
        quiet=(not db.execute("SELECT count(*) FROM local_updates WHERE state='processing'").fetchone()[0]
               and not db.execute("SELECT count(*) FROM scheduled_jobs WHERE state='running'").fetchone()[0]
               and not db.execute("SELECT count(*) FROM scheduled_outbox o JOIN scheduled_jobs j ON j.id=o.job_id WHERE o.state IN ('pending','sending') AND j.state='active'").fetchone()[0])
        if not quiet:return False
    # The lease spans all normal checkpoints, including active jobs between
    # units and isolated diagnostics outside scheduled_jobs. A technical
    # resource_blocked suspension with its lease released can still be repaired;
    # the versioned cycle/cursor is preserved rather than reset by installation.
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from iva_integration.research_priority import request
    try:priority=request({'action':'research-status'})
    except (OSError,ValueError):return False
    return priority.get('ok') is True and priority.get('status')=='idle' and not priority.get('active')
def snapshot():
    with finance() as db:
        return {name:hashlib.sha256(json.dumps(db.execute(sql).fetchall()).encode()).hexdigest()
                for name,sql in {
                    'operations':'SELECT * FROM operations ORDER BY id',
                    'jobs':'SELECT * FROM scheduled_jobs ORDER BY id',
                    'outbox':'SELECT * FROM scheduled_outbox ORDER BY id',
                }.items()}
def sqlite_copy(source,target):
    src=sqlite3.connect(source);dst=None
    try:
        dst=sqlite3.connect(target)
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('SQLite backup failed')
    finally:
        if dst is not None:dst.close()
        src.close()
def health(config):
    for port,path in [('gateway_port','/health'),('iva_port','/')]:
        deadline=time.monotonic()+55
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{config[port]}{path}',timeout=2) as response:
                    if response.status==200:break
            except OSError:pass
            if time.monotonic()>deadline:raise RuntimeError('Service health check failed: '+port)
            time.sleep(1)
    request=urllib.request.Request(f'http://127.0.0.1:{config["gateway_port"]}/demand-research',
        data=b'{"action":"status"}',headers={'Content-Type':'application/json','Authorization':'Bearer '+config['agent_token']})
    with urllib.request.urlopen(request,timeout=10) as response:status=json.load(response)
    if status.get('ok') is not True:raise RuntimeError('Research tool unavailable')
    sys.path.insert(0,str(ROOT))
    from iva_integration.research_priority import request as coordinator_request
    if coordinator_request({'action':'research-status'}).get('ok') is not True:
        raise RuntimeError('Research resource coordination unavailable')


def main():
    release=Path(sys.argv[1]).resolve()
    if not release.is_relative_to(ROOT/'data') or not release.name.startswith('demand-release-'):
        raise ValueError('Invalid release directory')
    lock=(ROOT/'data/application-deploy.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if not idle():raise RuntimeError('Wait for the active owner turn or schedule to finish')
    expected=json.loads((release/'expected-base.json').read_text())
    for rel in FILES:
        if sha(ROOT/rel)!=expected[rel]:raise RuntimeError('Source changed since review: '+rel)
        if not (release/rel).is_file():raise RuntimeError('Missing release file: '+rel)
    if sha(COORDINATOR)!=expected[str(COORDINATOR)]:raise RuntimeError('Installed coordinator changed since review')
    with finance() as db:
        row=db.execute('SELECT state,spec FROM scheduled_jobs WHERE id=?',(JOB,)).fetchone()
        if not row or row[0]!='active' or json.loads(row[1])['kind']!='demand_research':
            raise RuntimeError('Expected active research schedule missing')
    backup=release/'before';backup.mkdir(mode=0o700)
    existed=[]
    for rel in FILES:
        if (ROOT/rel).exists():
            target=backup/rel;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/rel,target);existed.append(rel)
    shutil.copy2(COORDINATOR,backup/'browser_coordinator.root.py')
    changed=False;research_backup=False;workers_started=False
    try:
        run(['systemctl','stop','iva@telegram'])
        if not idle():raise RuntimeError('Work began before the safe stop')
        run(['systemctl','stop','iva@scheduler','iva@engine','iva@gateway'])
        if not idle():raise RuntimeError('A schedule was interrupted; resume before upgrading')
        original=snapshot()
        sqlite_copy(RESEARCH,backup/'research.sqlite3');research_backup=True
        changed=True
        for rel in FILES:
            replace_source(release/rel,ROOT/rel)
        replace_source(release/'deploy/browser_coordinator.py',COORDINATOR,'root','root',0o644)
        with (release/'tests.log').open('wb') as out:
            run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-m','unittest','discover','-s','tests','-p','test_demand*.py','-v'],
                cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=180)
        with (release/'regression.log').open('wb') as out:
            run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-m','unittest','discover','-s','tests','-p','test_scheduled_tasks.py','-v'],
                cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=180)
        with (release/'coordination-tests.log').open('wb') as out:
            for pattern in ['test_browser_turns.py','test_threads_research_pause.py','test_threads_hourly.py',
                            'test_threads_all_ages.py','test_threads_repair.py','test_threads_development.py','test_max_turn_resume.py','test_verify_demand_branch.py','test_verify_demand_agents.py','test_verify_demand_quality.py',
                            'test_threads_alerts.py','test_threads_repair_queue.py','test_verify_demand_review.py','test_backup_capacity.py']:
                if pattern=='test_max_turn_resume.py':runtime=['/opt/max/venv/bin/python']
                elif pattern=='test_browser_turns.py':runtime=[str(ROOT/'.venv/bin/python')]
                else:runtime=['runuser','-u','iva','--',str(ROOT/'.venv/bin/python')]
                run([*runtime,'-m','unittest','discover','-s','tests','-p',pattern,'-v'],
                    cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,timeout=180)
        # Both readers/writers are stopped. Migrate once as the database owner.
        run(['runuser','-u','iva','--',str(ROOT/'.venv/bin/python'),'-c',
             'from iva_integration.demand_research import Research; r=Research(); assert r.db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; r.close()'],cwd=ROOT)
        if snapshot()!=original:raise RuntimeError('Finance or schedule state changed during installation')
        # Wants-only browser dependencies preserve any active Chromium during
        # coordinator restart. The next research lease requests a safe yield.
        run(['systemctl','restart','iva-browser-coordinator'])
        run(['systemctl','start','iva@gateway','iva@engine'])
        health(json.loads((ROOT/'data/iva-pilot/control.json').read_text()))
        report={'installed':True,'job_id':JOB,'financial_operations_unchanged':True,'schedules_and_outbox_unchanged':True,
                'research_backup':str(backup/'research.sqlite3'),'read_tool_ok':True,
                'sha256':{**{rel:sha(ROOT/rel) for rel in FILES},str(COORDINATOR):sha(COORDINATOR)}}
        (release/'installation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        # From here forward rollback must preserve any newly collected data.
        workers_started=True
        run(['systemctl','start','iva@telegram','iva@scheduler'])
        print(json.dumps({key:value for key,value in report.items() if key!='sha256'}))
    except BaseException:
        if not workers_started:
            try:
                if changed:
                    subprocess.run(['systemctl','stop',*SERVICES],check=True)
                    for rel in FILES:
                        target=ROOT/rel
                        if rel in existed:
                            replace_source(backup/rel,target)
                        else:target.unlink(missing_ok=True)
                    replace_source(backup/'browser_coordinator.root.py',COORDINATOR,'root','root',0o644)
                    if research_backup:sqlite_copy(backup/'research.sqlite3',RESEARCH)
                    shutil.chown(RESEARCH,user='iva',group='iva')
            finally:
                subprocess.run(['systemctl','restart','iva-browser-coordinator'],check=True)
                subprocess.run(['systemctl','start','iva@gateway','iva@engine','iva@telegram','iva@scheduler'],check=True)
        raise


if __name__=='__main__':main()
