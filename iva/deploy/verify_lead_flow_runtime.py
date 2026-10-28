"""Read-only runtime audit; the only write is a private release JSON report.

No model, mail, Ledger, configuration change, service control or credential read.
Expected source digests must come from a reviewed manifest, never from this audit.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

ROOT = Path('/opt/iva')
LEAD = Path('/opt/lead-flow')
RELEASE = ROOT/'data/lead-flow-release-20261026'
GENERAL_JOB = '2f9611a667cc493a'
CORE_UNITS = ('iva@gateway.service','iva@engine.service','iva@telegram.service',
    'iva@memory.service','iva@scheduler.service','iva-opencode.service',
    'iva-vpn.service','iva-research-fetch.service')
LEAD_UNITS = ('lead-flow-research.service','lead-flow-research-manual.service',
    'lead-flow-research.timer','lead-flow-maintenance.service','lead-flow-maintenance.timer',
    'lead-flow-mailer.service','lead-flow-mailer.timer')


def check(report, name, passed, **detail):
    report['checks'].append({'name':name,'passed':bool(passed),**detail})


def source_digest(path):
    if not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)):
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_allowed(path):
    allowed = (ROOT/'iva_integration',ROOT/'deploy',ROOT/'tests',ROOT/'docs',
        ROOT/'data/iva-pilot/app/agent/tools',ROOT/'data/iva-pilot/app/agent/instructions',
        LEAD/'worker',LEAD/'mailer',LEAD/'research',LEAD/'deploy',LEAD/'tests',LEAD/'static',
        Path('/etc/systemd/system'),Path('/etc/sudoers.d'),Path('/usr/local/bin'))
    if path.name.startswith('.env') or path.suffix in ('.sqlite3','.db','.env') or path.name in ('auth.json','mail.json','control.json','research.json'):
        return False
    return (path in (Path('/usr/local/lib/iva-research-fetch/server.py'),
                    Path('/usr/local/lib/lead-flow-crm/request.py'))
        or any(path.is_relative_to(base) for base in allowed)
        or (path.parent == LEAD and path.suffix in ('.py','.md','.txt','.json')))


def expected_hashes(paths):
    values = {}
    for path in paths:
        if path.stat().st_size > 2_000_000 or path.is_symlink():
            raise ValueError('invalid_manifest_file')
        content = json.loads(path.read_text(encoding='utf-8-sig'))
        entries = content if isinstance(content,list) else [{'target':target,'sha256':value} for target,value in content.items()]
        for item in entries:
            target, digest = Path(item['target']), item['sha256']
            if not target.is_absolute() or '..' in target.parts or not source_allowed(target) or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
                raise ValueError('invalid_manifest_entry')
            if str(target) in values and values[str(target)] != digest:
                raise ValueError('conflicting_reviewed_manifests')
            values[str(target)] = digest
    if not values:
        raise ValueError('empty_reviewed_manifest')
    return values


def verify_sources(report, manifests):
    expected = expected_hashes(manifests)
    for name, digest in expected.items():
        actual = source_digest(Path(name))
        check(report,'source_hash',actual == digest,path=name,expected=digest,actual=actual)


def service_states():
    result = subprocess.run(['/usr/bin/systemctl','show',*CORE_UNITS,*LEAD_UNITS,
        '--property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,ExecMainStatus,NextElapseUSecRealtime'],
        capture_output=True,text=True,check=True,timeout=10)
    if len(result.stdout) > 65536:
        raise ValueError('systemd_output_oversized')
    states = {}
    for block in result.stdout.strip().split('\n\n'):
        fields = dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        if fields.get('Id') in CORE_UNITS+LEAD_UNITS:
            states[fields['Id']] = fields
    return states


def general_schedule():
    path = ROOT/'data/local-finance.sqlite3'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        row = db.execute('SELECT id,state FROM scheduled_jobs WHERE id=?',(GENERAL_JOB,)).fetchone()
    return {'id':row[0],'state':row[1]} if row else {'id':GENERAL_JOB,'state':None}


def collect(manifests, expected_research='disabled', expected_maintenance='enabled', expected_mode='preview', require_idle=False):
    report = {'checked_at':datetime.now(timezone.utc).isoformat(),'checks':[],
        'model_calls':0,'emails_sent':0,'runtime_writes':0,
        'limits':['Service liveness does not prove inference or mail delivery.',
                  'This audit does not log in to CRM, SMTP or IMAP.']}
    for name, function in (
            ('reviewed_source_hashes',lambda:verify_sources(report,manifests)),
            ('runtime_state',lambda:_runtime(report,expected_research,expected_maintenance,expected_mode,require_idle))):
        try:
            function()
        except Exception as exc:
            check(report,name,False,error_type=type(exc).__name__)
    report['ok'] = bool(report['checks']) and all(item['passed'] for item in report['checks'])
    return report


def _runtime(report, expected_research, expected_maintenance, expected_mode, require_idle):
    sys.path.insert(0,str(ROOT))
    from iva_integration.lead_research import status
    from iva_integration.research_priority import request
    report['lead_status'] = status()
    check(report,'lead_status',report['lead_status'].get('ok') is True)
    check(report,'research_mode',report['lead_status'].get('mode') == expected_mode)
    check(report,'research_config_enabled',report['lead_status'].get('schedule_enabled_in_config') is (expected_research == 'enabled'))
    if expected_mode == 'ingest':
        crm = report['lead_status'].get('crm', {})
        check(report,'crm_export_configured',crm.get('export_enabled_in_config') is True and crm.get('transport') == 'iva_vpn')
        check(report,'crm_private_transport_ready',all(crm.get(key) is True for key in ('helper_present','socket_present','socket_active')))
        check(report,'crm_scoped_ingest_verified',crm.get('proof_status') == 'verified' and crm.get('proofscope') == 'scoped_ingest')
    report['services'] = service_states()
    if report['lead_status'].get('discovery_outreach') is True:
        check(report,'discovery_interval',report['lead_status'].get('research_interval_minutes') == 120)
        check(report,'discovery_candidate_limit',report['lead_status'].get('max_candidates_per_cycle') == 12)
        mailer=report['services'].get('lead-flow-mailer.timer',{})
        check(report,'mailer_timer_active',mailer.get('UnitFileState')=='enabled' and mailer.get('ActiveState')=='active')
    for unit in CORE_UNITS:
        check(report,'core_service_active',report['services'].get(unit,{}).get('ActiveState') == 'active',unit=unit)
    for unit, expectation in (('lead-flow-research.timer',expected_research),('lead-flow-maintenance.timer',expected_maintenance)):
        observed = report['services'].get(unit,{})
        check(report,'timer_state',observed.get('UnitFileState') == expectation and observed.get('ActiveState') == ('active' if expectation == 'enabled' else 'inactive'),unit=unit)
    for unit in ('lead-flow-research.service','lead-flow-research-manual.service','lead-flow-maintenance.service'):
        observed = report['services'].get(unit,{})
        check(report,'oneshot_not_failed',observed.get('LoadState') == 'loaded' and observed.get('ActiveState') in ('inactive','active','activating') and observed.get('Result') == 'success',unit=unit)
    report['general_schedule'] = general_schedule()
    check(report,'general_research_still_paused',report['general_schedule']['state'] == 'paused')
    lease = request({'action':'research-status'})
    report['research_lease'] = {key:lease.get(key) for key in ('ok','status','active','granted')}
    check(report,'coordinator_read_available',lease.get('ok') is True)
    if require_idle:
        check(report,'research_lease_idle',lease.get('status') == 'idle' and not lease.get('active') and not lease.get('granted'))
    app = ROOT/'data/iva-pilot/app'
    source = ROOT/'iva_integration/lead_research.ts'
    generated = app/'agent/tools/lead_research.ts'
    check(report,'native_generated_tool_matches_source',source_digest(source) is not None and source_digest(source) == source_digest(generated))
    summary = json.loads((app/'.eve/agent-summary.json').read_text())
    check(report,'compiled_tool_present',any(item.get('name') == 'lead_research' for item in summary.get('tools',[])))
    check(report,'compiled_guidance_present',any('OCR LEAD RESEARCH:' in item.get('content','') for item in summary.get('instructions',[])))
    check(report,'generated_guidance_present',(app/'agent/instructions/98-lead-research.md').is_file())
    database = LEAD/'data/research.sqlite3'
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        check(report,'research_db_quick_check',db.execute('PRAGMA quick_check').fetchone()[0] == 'ok')


def save_private(report, name):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+\.json',name) or not RELEASE.is_dir() or RELEASE.is_symlink():
        raise ValueError('invalid_report_destination')
    target = RELEASE/name
    fd, temporary = tempfile.mkstemp(prefix='.runtime-audit-',dir=RELEASE)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',action='append',type=Path)
    parser.add_argument('--expected-research',choices=('enabled','disabled'),default='disabled')
    parser.add_argument('--expected-maintenance',choices=('enabled','disabled'),default='enabled')
    parser.add_argument('--expected-mode',choices=('preview','ingest'),default='preview')
    parser.add_argument('--require-idle',action='store_true')
    parser.add_argument('--report-name',default='final-runtime-verification.json')
    args = parser.parse_args()
    manifests = args.manifest or [RELEASE/'manifest-runtime.json',RELEASE/'manifest-control.json']
    report = collect(manifests,args.expected_research,args.expected_maintenance,args.expected_mode,args.require_idle)
    target = save_private(report,args.report_name)
    print(json.dumps({'ok':report['ok'],'checks':len(report['checks']),
        'passed':sum(item['passed'] for item in report['checks']),'report':str(target),
        'model_calls':0,'emails_sent':0,'runtime_writes':0}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
