"""Real alternative-source search on an isolated historical foreign-role copy.

The original cycle must be terminal. Only the copy is rewound. Search, public
HTTP reads, analysis and a new role report are real; stop before review/synthesis,
child admission, export or notifications. No finance database is opened.
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
# Bind a staged core overlay before older verifier helpers alter sys.path.
from iva_integration import demand_agents as agents, demand_research as research
from iva_integration import demand_discovery_rescue as rescue, demand_network
from iva_integration.demand_sources import ResearchCodex, ResearchAnalysisCodex, fetch_source
from deploy.verify_demand_agents import deployment_read_lock, stamp
from deploy.verify_demand_branch import atomic_json, exclusive, research_lease
from deploy.verify_demand_review import readonly_backup

RELEASE = ROOT / 'data/demand-discovery-rescue-release-20261026'
OUTPUT = RELEASE / 'verification-rescue'
CYCLE = 'b209a41de445479a'
TASK = 'agt_911e50d86c314aab8c8ebf2600d18f59'
MAX_STEPS = 20
ALLOWED_STAGES = frozenset(('discovery', 'fetch', 'analyze', 'agent_report'))


def prepare_case(snapshot, directory, cycle_id=CYCLE, task_id=TASK):
    directory.mkdir(mode=0o700, exist_ok=False)
    database = directory / 'research.sqlite3'
    readonly_backup(snapshot, database)
    store = research.Research(database, directory / 'cards')
    try:
        cycle = store.rows('SELECT * FROM demand_cycles WHERE id=?', (cycle_id,))[0]
        task = agents.task(store.db, task_id)
        if cycle['state'] not in ('completed', 'completed_with_gaps') or cycle['stage'] != 'done':
            raise RuntimeError('historical_cycle_not_terminal')
        if (task['cycle_id'] != cycle_id or task['role'] != 'foreign' or task['parent_task_id']
                or task['status'] != 'done'):
            raise RuntimeError('historical_foreign_task_changed')
        batches = store.rows('SELECT * FROM demand_batches WHERE cycle_id=? AND agent_task_id=?', (cycle_id, task_id))
        if len(batches) != 1 or batches[0]['state'] != 'done' or not batches[0]['discovery']:
            raise RuntimeError('historical_single_batch_required')
        originals = store.rows('SELECT * FROM demand_candidates WHERE cycle_id=? AND batch_index=? ORDER BY url',
                               (cycle_id, batches[0]['batch_index']))
        if len(originals) != 5 or any(row['state'] != 'failed' or row['source_id'] for row in originals):
            raise RuntimeError('historical_five_failed_sources_required')
        other_tasks = store.rows('SELECT id,status,payload,result,source_ids FROM demand_agent_tasks WHERE cycle_id=? AND id<>? ORDER BY id', (cycle_id, task_id))
        if any(row['status'] in ('pending', 'running') for row in other_tasks):
            raise RuntimeError('historical_other_tasks_unfinished')
        selected_events = [row for row in store.rows("SELECT * FROM demand_events WHERE cycle_id=? AND stage='agents'", (cycle_id,))
                           if json.loads(row['detail']).get('task_id') == task_id]
        reports = [row for row in selected_events if row['event'] == 'role_report']
        if len(reports) != 1 or json.loads(reports[0]['detail']).get('source_ids') != []:
            raise RuntimeError('historical_empty_role_report_required')
        archive = {'cycle': cycle, 'task': task, 'batch': batches[0], 'candidates': originals,
                   'selected_events': selected_events, 'other_tasks': other_tasks}
        with store.db:
            store.db.execute("UPDATE demand_cycles SET state='running',stage='fetch',failures=0,error=NULL,result=NULL,finished=NULL WHERE id=?", (cycle_id,))
            store.db.execute("UPDATE demand_agent_plans SET status='running' WHERE cycle_id=?", (cycle_id,))
            store.db.execute("UPDATE demand_agent_tasks SET status='running',result=NULL,source_ids='[]',reason=NULL WHERE id=?", (task_id,))
            for row in selected_events:
                store.db.execute('DELETE FROM demand_events WHERE id=?', (row['id'],))
            store.event(cycle_id, 'diagnostic', 'copied_empty_role_checkpoint', {'task_id': task_id,
                'note': 'Only isolated copy rewound before original empty report; other task history retained.'})
            floor = store.db.execute('SELECT MAX(id) FROM demand_events').fetchone()[0]
            fresh = store.rows('SELECT * FROM demand_cycles WHERE id=?', (cycle_id,))[0]
            stage = store.advance(fresh)
        markers = store.rows('SELECT detail FROM demand_events WHERE cycle_id=? AND event=?', (cycle_id, rescue.EVENT))
        if stage != 'discovery' or len(markers) != 1:
            raise RuntimeError('rescue_was_not_scheduled')
        marker = json.loads(markers[0]['detail'])
        if marker['task_id'] != task_id:
            raise RuntimeError('rescue_task_changed')
        fixture = {'cycle_id': cycle_id, 'task_id': task_id, 'job_id': cycle['job_id'],
                   'task': cycle['task'], 'event_floor': floor, 'marker': marker, 'archive': archive,
                   'fixture_manipulations': ['Read-only consistent production snapshot copied into private diagnostic directory.',
                       'Only copied foreign task/cycle/plan reset before empty report; selected task events archived and removed from copy.',
                       'Original five failed candidates and discovery batch preserved; normal advance schedules one new batch.',
                       'Stop at agent_review without coordinator review, child admission, synthesis, export or notification.']}
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
                    raise RuntimeError('rescue_probe_left_selected_role')
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
                    calls.append({'stage': stage, 'successful': success,
                                  'seconds': round(time.monotonic() - began, 3),
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
            print(json.dumps({'event': 'rescue_probe_step', **entry}, ensure_ascii=True), flush=True)
            if result.get('notify') or not result.get('has_more'):
                raise RuntimeError('rescue_probe_unexpected_terminal_or_notice')
            if result.get('ok') is not True:
                sleeper(60)

    store = research.Research(database, cards)
    try:
        current = store.rows('SELECT state,stage FROM demand_cycles WHERE id=?', (cycle_id,))[0]
        original_batch = store.rows('SELECT * FROM demand_batches WHERE cycle_id=? AND batch_index=?', (cycle_id, fixture['marker']['original_batch_index']))[0]
        original_rows = store.rows('SELECT * FROM demand_candidates WHERE cycle_id=? AND batch_index=? ORDER BY url', (cycle_id, fixture['marker']['original_batch_index']))
        candidates = store.rows('SELECT * FROM demand_candidates WHERE cycle_id=? AND batch_index=? ORDER BY url', (cycle_id, fixture['marker']['batch_index']))
        reports = research._agent_events(store, cycle_id, 'role_report', task_id)
        sources = research._agent_sources(store, cycle_id, task_id)
        observations = research._agent_observations(store, cycle_id, task_id)
        invalid_quotes = [item['id'] for item in observations if item['source_id'] not in sources or
                          research.norm(item['quote']) not in research.norm(sources[item['source_id']]['text']) or len(item['quote'].split()) > 25]
        other_tasks = store.rows('SELECT id,status,payload,result,source_ids FROM demand_agent_tasks WHERE cycle_id=? AND id<>? ORDER BY id', (cycle_id, task_id))
        later = store.rows("SELECT event,stage FROM demand_events WHERE cycle_id=? AND id>? AND (event IN ('review_completed','synthesis_final') OR stage='finalize')", (cycle_id, fixture['event_floor']))
        markers_before = store.rows('SELECT id FROM demand_events WHERE cycle_id=? AND event=?', (cycle_id, rescue.EVENT))
        selected = agents.task(store.db, task_id)
        stored_cycle = store.rows('SELECT * FROM demand_cycles WHERE id=?', (cycle_id,))[0]
        with store.db:
            retry_scheduled = rescue.schedule(store, stored_cycle, selected, research.URLS_PER_BATCH, research.CHILD_URLS, research.MAX_CYCLE_URLS)
        markers_after = store.rows('SELECT id FROM demand_events WHERE cycle_id=? AND event=?', (cycle_id, rescue.EVENT))
        no_duplicate = markers_before == markers_after and len(markers_after) == 1 and not retry_scheduled
    finally:
        store.close()
    forbidden = set(fixture['marker']['failed_urls']); closed = set(fixture['marker']['closed_hosts'])
    analysis_calls = [call for call in calls if call['stage'] != 'discovery']
    report_calls = [call for call in calls if call['stage'] == 'agent_report']
    criteria = {
        'historical_five_failures_preserved': original_rows == fixture['archive']['candidates'] and original_batch == fixture['archive']['batch'],
        'native_search_executed': any(call['stage'] == 'discovery' and call['successful'] and call['web_events'] > 0 for call in calls),
        'specialist_report_medium_or_higher': bool(report_calls) and all(call['execution'].get('reasoning_effort') in ('medium','high','xhigh','max','ultra') for call in report_calls),
        'analysis_and_report_tool_free': bool(analysis_calls) and all(call['execution'].get('web_search') == 'disabled' and call['execution'].get('read_only') is True and not call['web_events'] for call in analysis_calls),
        'new_sources_actually_read': bool(sources) and any(item['ok'] for item in fetches),
        'all_new_sources_analyzed': bool(candidates) and all(item['state'] == 'failed' or item['analysis_state'] == 'done' for item in candidates),
        'closed_hosts_and_failed_urls_not_retried': bool(candidates) and all(item['url'] not in forbidden and urlsplit(item['url']).hostname not in closed for item in candidates),
        'within_frozen_task_and_cycle_budget': 0 < len(candidates) <= fixture['marker']['remaining_urls'],
        'international_context_preserved': bool(candidates) and all(item['geography'] == 'international' for item in candidates),
        'observations_have_literal_quotes': not invalid_quotes,
        'one_fresh_report_with_real_sources': len(reports) == 1 and bool(sources) and set(reports[0]['source_ids']) == set(sources),
        'stopped_before_coordinator_review': current == {'state': 'running', 'stage': 'agent_review'},
        'persisted_rescue_not_duplicated': no_duplicate,
        'other_tasks_unchanged': other_tasks == fixture['archive']['other_tasks'],
        'no_later_stages_exports_or_notifications': not later and not cards.exists() and all(step.get('notify') is False for step in steps),
    }
    report = {'at': stamp(), 'cycle_id': cycle_id, 'task_id': task_id, 'passed': all(criteria.values()),
              'criteria': criteria, 'full_cycle': False, 'steps': steps, 'calls': calls, 'fetches': fetches,
              'new_candidates': len(candidates), 'read_sources': len(sources), 'observations': len(observations),
              'invalid_quotes': invalid_quotes, 'new_source_urls': [row['url'] for row in sources.values()],
              'limits': 'Isolated historical role repair through report only; no coordinator review/child execution, production evidence writes, finance, export or notifications.'}
    atomic_json(directory / 'report.json', report)
    return report


def main():
    if len(sys.argv) != 1 or os.name != 'posix' or ROOT != Path('/opt/iva'):
        raise RuntimeError('run_fixed_probe_on_vps_only')
    import pwd
    if os.geteuid() != pwd.getpwnam('iva').pw_uid:
        raise RuntimeError('run_as_iva')
    if not RELEASE.is_dir() or OUTPUT.exists() or any(path.is_symlink() for path in (ROOT / 'data', RELEASE, OUTPUT)):
        raise RuntimeError('fresh_private_verification_directory_required')
    os.umask(0o077); OUTPUT.mkdir(mode=0o700)
    with deployment_read_lock(), exclusive(OUTPUT):
        from iva_integration.run_pilot import env as server_env
        from iva_integration import research_priority
        os.environ.update(server_env()); os.environ['FINANCE_CODEX_EXE'] = '/usr/local/bin/iva-codex'
        lease = {'job_id': 'diagnostic-rescue-' + hashlib.sha256(str(OUTPUT).encode()).hexdigest()[:16],
                 'cycle_id': 'diagnostic-empty-foreign-role'}
        with patch.object(research_priority, 'STATUS', OUTPUT / 'priority-status.json'), research_lease(lease, OUTPUT):
            snapshot = OUTPUT / 'source-snapshot.sqlite3'
            readonly_backup(ROOT / 'data/demand-research.sqlite3', snapshot)
            directory = OUTPUT / 'historical-foreign'
            fixture = prepare_case(snapshot, directory)
            report = run_fixture(fixture, directory)
            print(json.dumps({'passed': report['passed'], 'criteria': report['criteria'], 'read_sources': report['read_sources']}, ensure_ascii=True), flush=True)
            return 0 if report['passed'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(2)
