"""Native coverage repair of a REAL one-pricing-page foreign-role diagnostic.

Copies only the previous successful isolated diagnostic, never rewinds working
evidence. Keeps all eleven old URL attempts (one readable), leaving one of twelve
URLs for the targeted search. Stops before coordinator review or export. A
successful check proves honest detection/continuation, not market validation or
successful access to every external source.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from iva_integration import demand_agents as agents, demand_research as research
from iva_integration import demand_network
from iva_integration.demand_sources import ResearchCodex, ResearchAnalysisCodex, fetch_source
from deploy.verify_demand_agents import deployment_read_lock, stamp
from deploy.verify_demand_branch import atomic_json, exclusive, research_lease
from deploy.verify_demand_review import readonly_backup

RELEASE = ROOT / 'data/demand-coverage-release-20261026'
OUTPUT = RELEASE / 'verification-coverage'
SEED = ROOT / 'data/demand-discovery-rescue-release-20261026/verification-rescue/historical-foreign'
CYCLE = 'b209a41de445479a'
TASK = 'agt_911e50d86c314aab8c8ebf2600d18f59'
MAX_STEPS = 24
ALLOWED_STAGES = frozenset(('agent_coverage', 'discovery', 'fetch', 'analyze', 'agent_report'))
ASSESSMENT = 'evidence_coverage_assessed'
SEARCH = 'evidence_coverage_search'
CLOSED = 'evidence_coverage_closed'


def prepare_case(snapshot, directory, cycle_id=CYCLE, task_id=TASK):
    """Opt only this private copy into the new contract at its saved boundary."""
    directory.mkdir(mode=0o700, exist_ok=False)
    database = directory / 'research.sqlite3'
    readonly_backup(snapshot, database)
    store = research.Research(database, directory / 'cards')
    try:
        cycle = store.rows('SELECT * FROM demand_cycles WHERE id=?', (cycle_id,))[0]
        task = agents.task(store.db, task_id)
        if cycle['state'] != 'running' or cycle['stage'] != 'agent_review':
            raise RuntimeError('seed_must_be_previous_probe_report_boundary')
        if (task['cycle_id'] != cycle_id or task['role'] != 'foreign' or task['parent_task_id']
                or task['status'] != 'running'):
            raise RuntimeError('seed_foreign_task_changed')
        batches = store.rows('SELECT * FROM demand_batches WHERE cycle_id=? AND agent_task_id=? ORDER BY batch_index', (cycle_id, task_id))
        if len(batches) != 2 or any(row['state'] != 'done' or not row['discovery'] for row in batches):
            raise RuntimeError('seed_two_completed_batches_required')
        original = store.rows('''SELECT c.* FROM demand_candidates c JOIN demand_batches b
            ON b.cycle_id=c.cycle_id AND b.batch_index=c.batch_index
            WHERE c.cycle_id=? AND b.agent_task_id=? ORDER BY c.url''', (cycle_id, task_id))
        sources = research._agent_sources(store, cycle_id, task_id)
        if (len(original) != 11 or len(sources) != 1
                or sum(row['state'] == 'failed' for row in original) != 10
                or any(row['state'] not in ('read', 'failed') for row in original)
                or any(row['state'] == 'read' and row['analysis_state'] != 'done' for row in original)):
            raise RuntimeError('seed_one_read_page_and_ten_failures_required')
        if next(iter(sources.values()))['url'] != 'https://www.cin7.com/pricing/':
            raise RuntimeError('seed_readable_pricing_page_required')
        prior = research._agent_events(store, cycle_id, 'discovery_rescue_scheduled', task_id)
        if len(prior) != 1:
            raise RuntimeError('seed_single_access_rescue_required')
        selected_events = [row for row in store.rows("SELECT * FROM demand_events WHERE cycle_id=? AND stage='agents' AND event='role_report'", (cycle_id,))
                           if json.loads(row['detail']).get('task_id') == task_id]
        if len(selected_events) != 1 or set(json.loads(selected_events[0]['detail'])['source_ids']) != set(sources):
            raise RuntimeError('seed_previous_pricing_report_required')
        other_tasks = store.rows('SELECT id,status,payload,result,source_ids FROM demand_agent_tasks WHERE cycle_id=? AND id<>? ORDER BY id', (cycle_id, task_id))
        if any(row['status'] in ('pending', 'running') for row in other_tasks):
            raise RuntimeError('seed_other_tasks_unfinished')
        plan = json.loads(cycle['plan'])
        if plan.get('version') != 3 or plan.get('max_urls') != 72 or plan.get('evidence_coverage_version'):
            raise RuntimeError('seed_previous_contract_changed')
        archive = {'cycle': cycle, 'task': task, 'batches': batches, 'candidates': original,
                   'sources': sources, 'removed_report_events': selected_events, 'other_tasks': other_tasks}
        with store.db:
            plan['evidence_coverage_version'] = 1
            store.db.execute("UPDATE demand_cycles SET plan=?,stage='agent_coverage',failures=0,error=NULL WHERE id=?", (research.dumps(plan), cycle_id))
            for row in selected_events:
                store.db.execute('DELETE FROM demand_events WHERE id=?', (row['id'],))
            store.event(cycle_id, 'diagnostic', 'copied_pricing_only_checkpoint', {'task_id': task_id,
                'note': 'Only private diagnostic opted into coverage; preserve every prior URL/source and original access-rescue budget.'})
            floor = store.db.execute('SELECT MAX(id) FROM demand_events').fetchone()[0]
        fixture = {'cycle_id': cycle_id, 'task_id': task_id, 'job_id': cycle['job_id'], 'task': cycle['task'],
                   'event_floor': floor, 'archive': archive, 'remaining_task_urls': 1,
                   'fixture_manipulations': [
                       'Copied the completed earlier native diagnostic through read-only SQLite backup, not production evidence.',
                       'Only the copied plan opts into coverage version 1; remove/archive the copied role report and set agent_coverage.',
                       'All eleven prior URL attempts, ten failures, one pricing source, and prior access-rescue event stay intact.',
                       'Stop at agent_review, before child admission, synthesis, export, notification or financial work.']}
        atomic_json(directory / 'fixture.json', fixture)
        return fixture
    finally:
        store.close()


def run_fixture(fixture, directory, client_factory=None, fetcher=fetch_source, sleeper=time.sleep):
    database, cards = directory / 'research.sqlite3', directory / 'cards'
    calls, steps, fetches = [], [], []
    cycle_id, task_id = fixture['cycle_id'], fixture['task_id']
    client_factory = client_factory or (lambda stage: (ResearchCodex if stage == 'discovery' else ResearchAnalysisCodex)(timeout=75, role='orchestrator'))

    def read(url):
        value = fetcher(url)
        fetches.append({'url': url, 'ok': value.get('ok') is True, 'category': value.get('error_category'),
                        'characters': len(value.get('text') or '')})
        return value

    with patch.object(demand_network, 'STATE', directory / 'network-state'):
        for _ in range(MAX_STEPS):
            store = research.Research(database, cards)
            try:
                cycle = store.rows('SELECT stage,state FROM demand_cycles WHERE id=?', (cycle_id,))[0]
                selected = agents.next_task(store.db, cycle_id)
                if cycle['stage'] == 'agent_review':
                    break
                if (cycle['state'] != 'running' or cycle['stage'] not in ALLOWED_STAGES
                        or not selected or selected['id'] != task_id):
                    raise RuntimeError('coverage_probe_left_selected_role')
            finally:
                store.close()
            stage = cycle['stage']
            cli = None if stage == 'fetch' else client_factory(stage)
            original = cli.structured if cli else None

            def observe(prompt, schema):
                began = time.monotonic(); success = False
                try:
                    value = original(prompt, schema); success = True
                    return value
                finally:
                    calls.append({'stage': stage, 'successful': success, 'seconds': round(time.monotonic() - began, 3),
                                  'execution': {key: value for key, value in (getattr(cli, 'last_execution', {}) or {}).items()
                                                if key in ('model', 'reasoning_effort', 'web_search', 'read_only', 'runtime_ms')},
                                  'web_events': len(getattr(cli, 'last_web_events', []) or [])})
            if cli:
                with patch.object(cli, 'structured', observe):
                    result = research.run_step(fixture['job_id'], fixture['task'], db_path=database, cards_dir=cards,
                                               cycle_id=cycle_id, cli=cli, fetcher=read)
            else:
                result = research.run_step(fixture['job_id'], fixture['task'], db_path=database, cards_dir=cards,
                                           cycle_id=cycle_id, fetcher=read)
            entry = {'before': stage, **{key: result.get(key) for key in ('stage', 'status', 'ok', 'notify', 'has_more', 'error_category')}}
            steps.append(entry)
            atomic_json(directory / 'progress.json', {'at': stamp(), 'steps': steps, 'calls': calls, 'fetches': fetches})
            print(json.dumps({'event': 'coverage_probe_step', **entry}, ensure_ascii=True), flush=True)
            if result.get('notify') or not result.get('has_more'):
                raise RuntimeError('coverage_probe_unexpected_terminal_or_notice')
            if result.get('ok') is not True:
                sleeper(60)

    store = research.Research(database, cards)
    try:
        current = store.rows('SELECT state,stage FROM demand_cycles WHERE id=?', (cycle_id,))[0]
        originals = []
        original_batches = []
        for batch in fixture['archive']['batches']:
            original_batches += store.rows('SELECT * FROM demand_batches WHERE cycle_id=? AND batch_index=?', (cycle_id, batch['batch_index']))
            originals += store.rows('SELECT * FROM demand_candidates WHERE cycle_id=? AND batch_index=?', (cycle_id, batch['batch_index']))
        originals.sort(key=lambda item: item['url'])
        searches = research._agent_events(store, cycle_id, SEARCH, task_id)
        assessments = research._agent_events(store, cycle_id, ASSESSMENT, task_id)
        closed = research._agent_events(store, cycle_id, CLOSED, task_id)
        candidates = []
        for marker in searches:
            candidates += store.rows('SELECT * FROM demand_candidates WHERE cycle_id=? AND batch_index=? ORDER BY url', (cycle_id, marker['batch_index']))
        reports = research._agent_events(store, cycle_id, 'role_report', task_id)
        sources = research._agent_sources(store, cycle_id, task_id)
        other_tasks = store.rows('SELECT id,status,payload,result,source_ids FROM demand_agent_tasks WHERE cycle_id=? AND id<>? ORDER BY id', (cycle_id, task_id))
        later = store.rows("SELECT event,stage FROM demand_events WHERE cycle_id=? AND id>? AND (event IN ('review_completed','synthesis_final') OR stage='finalize')", (cycle_id, fixture['event_floor']))
        before = store.rows('SELECT * FROM demand_batches WHERE cycle_id=? ORDER BY batch_index', (cycle_id,))
        if current == {'state': 'running', 'stage': 'agent_review'}:
            with store.db:
                research._agent_advance(store, store.rows('SELECT * FROM demand_cycles WHERE id=?', (cycle_id,))[0])
        after = store.rows('SELECT * FROM demand_batches WHERE cycle_id=? ORDER BY batch_index', (cycle_id,))
        all_candidate_count = store.db.execute('SELECT count(*) FROM demand_candidates WHERE cycle_id=?', (cycle_id,)).fetchone()[0]
    finally:
        store.close()

    initial = assessments[0].get('assessment', {}) if assessments else {}
    missing = initial.get('missing', [])
    missing_keys = {item.get('key') if isinstance(item, dict) else item for item in missing}
    forbidden = {row['url'] for row in fixture['archive']['candidates']}
    blocked = set(searches[0].get('closed_hosts', [])) if searches else set()
    nonsearch = [call for call in calls if call['stage'] != 'discovery']
    demanding = [call for call in calls if call['stage'] in ('agent_coverage', 'agent_report')]
    criteria = {
        'initial_pricing_did_not_satisfy_user_experience': bool(assessments) and initial.get('sufficient') is False and 'user_experience' in missing_keys,
        'targeted_native_search_executed': len(searches) == 1 and any(call['stage'] == 'discovery' and call['successful'] and call['web_events'] > 0 for call in calls),
        'coverage_and_report_medium_or_higher': bool(demanding) and all(call['execution'].get('reasoning_effort') in ('medium','high','xhigh','max','ultra') for call in demanding),
        'assessment_analysis_report_tool_free': bool(nonsearch) and all(call['execution'].get('web_search') == 'disabled' and call['execution'].get('read_only') is True and not call['web_events'] for call in nonsearch),
        'old_sources_attempts_and_batches_preserved': originals == fixture['archive']['candidates'] and original_batches == fixture['archive']['batches'] and all(sources.get(k) == v for k,v in fixture['archive']['sources'].items()),
        'remaining_one_url_and_total_budget_respected': len(candidates) <= fixture['remaining_task_urls'] and all_candidate_count <= 72,
        'old_urls_and_closed_hosts_excluded': all(row['url'] not in forbidden and urlsplit(row['url']).hostname not in blocked for row in candidates),
        'international_context_preserved': all(row['geography'] == 'international' for row in candidates),
        'new_sources_read_and_analyzed_or_explicitly_failed': all(row['state'] == 'failed' or row['state'] == 'read' and row['analysis_state'] == 'done' for row in candidates),
        'coverage_closed_honestly_with_budget_or_missing_limit': len(closed) == 1 and closed[0].get('status') == 'deferred' and bool(closed[0].get('stop_reason')),
        'one_fresh_report_uses_real_corpus': len(reports) == 1 and set(reports[0]['source_ids']) == set(sources),
        'stopped_before_coordinator_review': current == {'state': 'running', 'stage': 'agent_review'},
        'reopening_does_not_repeat_search': before == after,
        'other_tasks_unchanged': other_tasks == fixture['archive']['other_tasks'],
        'no_later_stages_exports_or_notifications': not later and not cards.exists() and all(step.get('notify') is False for step in steps),
    }
    report = {'at': stamp(), 'cycle_id': cycle_id, 'task_id': task_id, 'passed': all(criteria.values()),
              'criteria': criteria, 'full_cycle': False, 'steps': steps, 'calls': calls, 'fetches': fetches,
              'assessments': assessments, 'searches': searches, 'closed': closed,
              'new_candidates': len(candidates), 'read_sources': len(sources),
              'new_source_urls': [row['url'] for key,row in sources.items() if key not in fixture['archive']['sources']],
              'limits': 'One remaining URL on copied prior diagnostic; verifies insufficient-evidence detection and bounded continuation, not full foreign market coverage. No production evidence/finance/export/notification writes.'}
    atomic_json(directory / 'report.json', report)
    return report


def main():
    if len(sys.argv) != 1 or os.name != 'posix' or ROOT != Path('/opt/iva'):
        raise RuntimeError('run_fixed_probe_on_vps_only')
    import pwd
    import sqlite3
    if os.geteuid() != pwd.getpwnam('iva').pw_uid:
        raise RuntimeError('run_as_iva')
    if not RELEASE.is_dir() or OUTPUT.exists() or any(path.is_symlink() for path in (ROOT / 'data', RELEASE, OUTPUT, SEED, SEED / 'research.sqlite3')):
        raise RuntimeError('fresh_private_verification_directory_required')
    prior = json.loads((SEED / 'report.json').read_text())
    if prior.get('passed') is not True or prior.get('read_sources') != 1 or prior.get('new_source_urls') != ['https://www.cin7.com/pricing/']:
        raise RuntimeError('previous_native_probe_not_verified')
    with sqlite3.connect((ROOT / 'data/demand-research.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        terminal = db.execute('SELECT state,stage FROM demand_cycles WHERE id=?', (CYCLE,)).fetchone()
        if not terminal or terminal[0] not in ('completed', 'completed_with_gaps') or terminal[1] != 'done':
            raise RuntimeError('production_cycle_not_terminal')
    os.umask(0o077); OUTPUT.mkdir(mode=0o700)
    with deployment_read_lock(), exclusive(OUTPUT):
        from iva_integration.run_pilot import env as server_env
        from iva_integration import research_priority
        os.environ.update(server_env()); os.environ['FINANCE_CODEX_EXE'] = '/usr/local/bin/iva-codex'
        lease = {'job_id': 'diagnostic-coverage-' + hashlib.sha256(str(OUTPUT).encode()).hexdigest()[:16],
                 'cycle_id': 'diagnostic-pricing-only-foreign-role'}
        with patch.object(research_priority, 'STATUS', OUTPUT / 'priority-status.json'), research_lease(lease, OUTPUT):
            snapshot = OUTPUT / 'source-snapshot.sqlite3'
            readonly_backup(SEED / 'research.sqlite3', snapshot)
            directory = OUTPUT / 'historical-foreign'
            fixture = prepare_case(snapshot, directory)
            report = run_fixture(fixture, directory)
            print(json.dumps({'passed': report['passed'], 'criteria': report['criteria'], 'new_candidates': report['new_candidates']}, ensure_ascii=True), flush=True)
            return 0 if report['passed'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(2)
