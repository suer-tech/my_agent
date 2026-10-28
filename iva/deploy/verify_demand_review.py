"""Real native review contract probe on independent historical DB copies.

This is an explicitly rewound recovery fixture, not a new finding or production
replay. Historical children remain in the copies. No child research, synthesis,
memory export, notifications or owner/financial database writes are executed.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.verify_demand_agents import deployment_read_lock, stamp
from deploy.verify_demand_branch import atomic_json, exclusive, research_lease
from iva_integration import demand_agents as agents
from iva_integration import demand_research as research
from iva_integration.demand_sources import ResearchAnalysisCodex

RELEASE = ROOT / 'data/demand-release-20261025-review-retry'
OUTPUT = RELEASE / 'verification'
SOURCE = ROOT / 'data/demand-research.sqlite3'
CYCLE = '274d2b7f58194b39'
CASES = {'fresh_report': ('39d2367e935448c5', 'agt_2bac3d681e7948d884ef0fa02c7ce967'),
         'empty': (CYCLE, 'agt_df3909284f15480eab12e9921f1fb028'),
         'nonempty': (CYCLE, 'agt_83d4400479564797a7a0fb6ac7a1d2e2'),
         'decorative_quote': ('39d2367e935448c5', 'agt_2bac3d681e7948d884ef0fa02c7ce967')}
EFFORTS = ('none', 'low', 'medium', 'high', 'xhigh', 'max')


def paths(release=RELEASE, output=OUTPUT):
    release, output = Path(release), Path(output)
    if output != release / 'verification' or not release.is_dir():
        raise ValueError('unexpected_review_directory')
    for path in (release.parent, release, output):
        if path.is_symlink():
            raise ValueError('review_path_is_symlink')
    output.mkdir(mode=0o700, exist_ok=True)
    outputs = [output / name for name in ('source-snapshot.sqlite3', '.verification.lock', 'progress.jsonl',
                                         'priority-status.json', 'priority-status.json.tmp', 'review-report.json')]
    for case in CASES:
        folder = output / case
        outputs += [folder] + [folder / name for name in (
            'research.sqlite3', 'research.sqlite3-wal', 'research.sqlite3-shm', 'research.sqlite3-journal',
            'cards', 'fixture.json', 'report.json')]
    for path in outputs:
        if path.is_symlink() or not path.resolve().is_relative_to(output.resolve()):
            raise ValueError('review_path_is_symlink')
    if (output / 'source-snapshot.sqlite3').exists():
        raise RuntimeError('review_probe_requires_fresh_directory')
    return output


def readonly_backup(source, target):
    """SQLite backup includes committed WAL data; original connection is mode=ro."""
    source, target = Path(source), Path(target)
    if source.is_symlink() or target.is_symlink() or target.exists() or source.resolve() == target.resolve():
        raise ValueError('invalid_review_backup_path')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original, \
         closing(sqlite3.connect(target)) as copied:
        original.backup(copied)
        if copied.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('review_backup_integrity_failed')
        copied.commit()
    os.chmod(target, 0o600)


def prepare_case(snapshot, directory, cycle_id, task_id, expect_empty, quote_case=False, fresh_report=False):
    directory.mkdir(mode=0o700, exist_ok=False)
    database, cards = directory / 'research.sqlite3', directory / 'cards'
    readonly_backup(snapshot, database)
    store = research.Research(database, cards)
    try:
        cycle = store.rows('SELECT * FROM demand_cycles WHERE id=?', (cycle_id,))[0]
        task = agents.task(store.db, task_id)
        if task['cycle_id'] != cycle_id or task['status'] not in ('done', 'failed'):
            raise ValueError('review_fixture_task_scope_invalid')
        reports = research._agent_events(store, cycle_id, 'role_report', task_id)
        if not reports:
            raise ValueError('historical_role_report_missing')
        report = reports[-1]
        requests = report['report']['child_requests']
        if bool(requests) == expect_empty:
            raise ValueError('historical_request_shape_changed')
        tasks = agents.snapshot(store.db, cycle_id)['tasks']
        if any(row['status'] in ('running', 'pending') and row['id'] != task_id for row in tasks):
            raise ValueError('review_fixture_has_unfinished_other_tasks')
        archive = {'cycle': cycle, 'task': task, 'plan_status': agents.plan(store.db, cycle_id)['status'],
                   'syntheses': store.rows('SELECT * FROM demand_agent_syntheses WHERE cycle_id=?', (cycle_id,)),
                   'core_synthesis_events': store.rows("SELECT * FROM demand_events WHERE cycle_id=? AND event IN ('synthesis_review','synthesis_final')", (cycle_id,))}
        selected_report_history = []
        if fresh_report:
            selected_report_history = [row for row in store.rows(
                "SELECT * FROM demand_events WHERE cycle_id=? AND event IN ('role_report','review_completed','review_contract_rejected','review_state_rejected')", (cycle_id,))
                if json.loads(row['detail']).get('task_id') == task_id]
            archive['selected_report_review_events'] = selected_report_history
        before_tasks = [row['id'] for row in tasks]
        with store.db:
            store.db.execute("UPDATE demand_cycles SET state='running',stage=?,failures=0,error=NULL,result=NULL,finished=NULL WHERE id=?",
                             ('agent_report' if fresh_report else 'agent_review', cycle_id))
            store.db.execute("UPDATE demand_agent_plans SET status='running' WHERE cycle_id=?", (cycle_id,))
            store.db.execute("UPDATE demand_agent_tasks SET status='running',result=NULL,reason=NULL,source_ids='[]' WHERE id=?", (task_id,))
            store.db.execute('DELETE FROM demand_agent_syntheses WHERE cycle_id=?', (cycle_id,))
            store.db.execute("DELETE FROM demand_events WHERE cycle_id=? AND event IN ('synthesis_review','synthesis_final')", (cycle_id,))
            for event in selected_report_history:
                store.db.execute('DELETE FROM demand_events WHERE id=?', (event['id'],))
            store.event(cycle_id, 'diagnostic', 'copied_review_checkpoint', {'task_id': task_id,
                'note': 'Historical copies only; selected review rewound. Existing children retained, no new finding claimed.'})
        event_floor = store.db.execute('SELECT COALESCE(MAX(id),0) FROM demand_events').fetchone()[0]
        fixture = {'cycle_id': cycle_id, 'task_id': task_id, 'job_id': cycle['job_id'], 'task': cycle['task'],
                   'expect_empty': expect_empty, 'quote_case': quote_case, 'fresh_report': fresh_report,
                   'report': report, 'task_ids_before': before_tasks,
                   'historical_child_ids': [row['id'] for row in tasks if row['parent_task_id'] == task_id],
                   'event_floor': event_floor, 'archive': archive,
                   'fixture_manipulations': ['Consistent read-only production backup copied independently per case.',
                       'Only copied cycle/task/plan rewound to agent_review; previous synthesis archived and removed from copy.',
                       'Historical children remain; existing admission does not prove new child execution.']}
        if fresh_report:
            fixture['fixture_manipulations'][1] = 'Only copied cycle/task/plan rewound to agent_report; selected old report/review events archived and removed, other task history retained.'
            fixture['fixture_manipulations'].append('One fresh role report will be generated; no coordinator decision or child execution follows it.')
        atomic_json(directory / 'fixture.json', fixture)
        return fixture
    finally:
        store.close()


def request_gates(store, fixture):
    sources = research._agent_sources(store, fixture['cycle_id'], fixture['task_id'])
    checks = []
    for request in fixture['report']['report']['child_requests']:
        valid, normalized = True, False
        try:
            agents._validate(request, agents.CHILD_SCHEMA)
            agents._substantive(request)
            valid = set(request['source_ids']).issubset(sources) and len(request['trigger_quote'].split()) <= 25
            quote = request['trigger_quote'].strip()
            if valid and not any(agents._literal(quote) in agents._literal(sources[key]['text']) for key in request['source_ids']):
                wrappers = (('«', '»'), ('“', '”'), ('„', '“'), ('"', '"'), ("'", "'"))
                inside = quote[1:-1].strip() if any(quote.startswith(left) and quote.endswith(right) for left, right in wrappers) else ''
                valid = bool(inside) and any(agents._literal(inside) in agents._literal(sources[key]['text']) for key in request['source_ids'])
                if valid:
                    agents._validate({**request, 'trigger_quote': inside}, agents.CHILD_SCHEMA)
                    normalized = True
            urls = {sources[key]['url'].rstrip('/') for key in request['source_ids'] if key in sources}
            for field in ('question', 'scope', 'methods', 'why_needed', 'support_condition', 'refute_condition'):
                valid = valid and all(url.rstrip('.,;)]}/') in urls for url in re.findall(r'https?://[^\s<>"\']+', request[field]))
        except (ValueError, KeyError, TypeError):
            valid = False
        checks.append({'key_hash': hashlib.sha256(request['key'].encode()).hexdigest()[:16],
                       'source_count': len(request['source_ids']), 'verified_quote_and_sources': bool(valid),
                       'decorative_quote_normalization_required': normalized})
    return checks


def checkpoint_replay(database, cards, fixture, review):
    """Reopen stored checkpoint; admission replay is rollback-only, not model work."""
    store = research.Research(database, cards)
    try:
        cycle = store.rows('SELECT * FROM demand_cycles WHERE id=?', (fixture['cycle_id'],))[0]
        before = agents.snapshot(store.db, fixture['cycle_id'])
        identities = [row['id'] for row in before['tasks']]
        selected = agents.task(store.db, fixture['task_id'])
        sources = set(research._agent_sources(store, fixture['cycle_id'], fixture['task_id']))
        results = []
        store.db.execute('SAVEPOINT diagnostic_admission_replay')
        try:
            for disposition in ('approve', 'defer'):
                keys = {item['key'] for item in review['decisions'] if item['decision'] == disposition}
                proposals = [item for item in fixture['report']['report']['child_requests'] if item['key'] in keys]
                if proposals:
                    results.append(agents.enqueue_children(store.db, fixture['task_id'], proposals, sources,
                        defer_reason='orchestrator_deferred' if disposition == 'defer' else None))
            during = [row['id'] for row in agents.snapshot(store.db, fixture['cycle_id'])['tasks']]
        finally:
            store.db.execute('ROLLBACK TO diagnostic_admission_replay')
            store.db.execute('RELEASE diagnostic_admission_replay')
        return {'checkpoint_stage': cycle['stage'], 'selected_status': selected['status'],
                'checkpoint_advanced': cycle['stage'] != 'agent_review' and selected['status'] == 'done',
                'no_duplicate_tasks': identities == during and all(not row['admitted'] and not row['deferred'] for row in results),
                'admission_replay': results, 'model_reexecuted': False,
                'scope': 'Reloaded persisted checkpoint; deterministic admission replay was rolled back. No next research stage was executed.'}
    finally:
        store.close()


def report_context_metadata(prompt, sources):
    """Inspect exact input locally, retain only counts/digests rather than text."""
    try:
        context = json.loads(prompt.split('\nДоступные source_ids и выдержки: ', 1)[1])
        if not isinstance(context, list) or any(not isinstance(row, dict) for row in context):
            raise ValueError('unexpected_context_shape')
        metadata, provided = [], 0
        for row in context:
            identity, text = row.get('id'), row.get('text')
            if identity not in sources or not isinstance(text, str):
                raise ValueError('unknown_context_source')
            stored = sources[identity]['text']
            length = len(text); provided += length
            correct = (row.get('stored_characters') == len(stored) and row.get('provided_characters') == length
                       and row.get('truncated_for_report') is (length < len(stored)) and text == stored[:length])
            metadata.append({'source_id': identity, 'stored_characters': len(stored),
                             'provided_characters': length, 'truncated_for_report': row.get('truncated_for_report'),
                             'metadata_matches_stored_source': correct,
                             'provided_text_sha256': hashlib.sha256(text.encode()).hexdigest()})
        identities = [row['source_id'] for row in metadata]
        unconstrained = sum(min(len(row['text']), 4000) for row in sources.values()) <= 24000
        pages_2048 = [row for row in metadata if row['stored_characters'] == 2048]
        return {'valid_complete_json': True, 'source_count': len(metadata), 'provided_characters': provided,
                'all_sources_included_once': len(identities) == len(sources) and set(identities) == set(sources),
                'metadata_matches_stored_sources': all(row['metadata_matches_stored_source'] for row in metadata),
                'bounded_source_context': provided <= 24000 and all(row['provided_characters'] <= 4000 for row in metadata),
                'short_sources_complete_when_budget_allows': not unconstrained or all(
                    row['provided_characters'] == min(row['stored_characters'], 4000) for row in metadata),
                'pages_2048': {'present': len(pages_2048), 'complete': sum(row['provided_characters'] == 2048 for row in pages_2048)},
                'sources': metadata, 'retained_prompt_or_source_text': False}
    except (ValueError, KeyError, IndexError, TypeError):
        return {'valid_complete_json': False, 'retained_prompt_or_source_text': False}


def run_report_case(fixture, directory, client):
    """Exactly one native specialist report; stop before any coordinator review."""
    database, cards = directory / 'research.sqlite3', directory / 'cards'
    store = research.Research(database, cards)
    try:
        sources = research._agent_sources(store, fixture['cycle_id'], fixture['task_id'])
    finally:
        store.close()
    attempts, completed = [], []
    original = client.structured
    def observe(prompt, schema):
        attempts.append({'context': report_context_metadata(prompt, sources), 'schema': schema})
        value = original(prompt, schema)
        completed.append(True)
        return value
    started = time.monotonic()
    with patch.object(client, 'structured', observe):
        result = research.run_step(fixture['job_id'], fixture['task'], db_path=database,
                                   cards_dir=cards, cli=client, cycle_id=fixture['cycle_id'])
    # Reopen independently: a real stored checkpoint, not only the return value.
    store = research.Research(database, cards)
    try:
        updates = [json.loads(row['detail']) for row in store.rows(
            "SELECT detail FROM demand_events WHERE cycle_id=? AND event='role_report' AND id>? ORDER BY id",
            (fixture['cycle_id'], fixture['event_floor']))]
        updates = [row for row in updates if row.get('task_id') == fixture['task_id']]
        report = updates[-1] if updates else None
        cycle = store.rows('SELECT stage,state FROM demand_cycles WHERE id=?', (fixture['cycle_id'],))[0]
        task = agents.task(store.db, fixture['task_id'])
        identities = [row['id'] for row in agents.snapshot(store.db, fixture['cycle_id'])['tasks']]
        later_work = store.rows("SELECT stage,event FROM demand_events WHERE cycle_id=? AND id>? AND (event='review_completed' OR stage IN ('fetch','discovery','finalize'))",
                                (fixture['cycle_id'], fixture['event_floor']))
        gates = request_gates(store, {**fixture, 'report': report}) if report else []
    finally:
        store.close()
    content = report['report'] if report else {}
    context = attempts[-1]['context'] if attempts else {}
    schema = attempts[-1]['schema'] if attempts else {}
    child_schema = schema.get('properties', {}).get('child_requests', {})
    enumeration = child_schema.get('items', {}).get('properties', {}).get('source_ids', {}).get('items', {}).get('enum')
    branch_reason = content.get('branch_reason', '')
    execution = getattr(client, 'last_execution', {}) or {}
    effort = execution.get('reasoning_effort')
    pages_2048 = context.get('pages_2048', {'present': 0, 'complete': 0})
    criteria = {
        'one_real_native_report_call': len(attempts) == len(completed) == 1,
        'actual_medium_or_higher': effort in EFFORTS and EFFORTS.index(effort) >= EFFORTS.index('medium'),
        'analysis_tools_disabled': execution.get('read_only') is True and execution.get('web_search') == 'disabled'
            and not (getattr(client, 'last_web_events', None) or []),
        'substantive_branch_reason': isinstance(branch_reason, str) and len(branch_reason.strip()) >= 20
            and len(set(re.findall(r'\w+', branch_reason.casefold()))) >= 4,
        'child_schema_only_actual_role_source_ids': (set(enumeration or []) == set(sources)) if sources else child_schema.get('maxItems') == 0,
        'child_sources_and_quotes_verified': all(row['verified_quote_and_sources'] for row in gates),
        'source_context_complete_valid_json': context.get('valid_complete_json') is True,
        'all_source_metadata_preserved': context.get('all_sources_included_once') is True and context.get('metadata_matches_stored_sources') is True,
        'source_context_budget_obeyed': context.get('bounded_source_context') is True,
        'short_sources_complete_when_budget_allows': context.get('short_sources_complete_when_budget_allows') is True,
        'historical_2048_source_provided_in_full': pages_2048['present'] > 0 and pages_2048['complete'] == pages_2048['present'],
        'report_committed_once': result.get('ok') is True and len(updates) == 1,
        'checkpoint_persisted_at_agent_review': cycle == {'stage': 'agent_review', 'state': 'running'} and task['status'] == 'running',
        'no_review_admission_search_export_or_notice': not later_work and identities == fixture['task_ids_before']
            and result.get('notify') is False and not (cards.exists() and any(cards.iterdir())),
    }
    value = {'diagnostic': 'historical-fresh-report-fixture', 'at': stamp(), 'cycle_id': fixture['cycle_id'],
             'task_id': fixture['task_id'], 'full_cycle': False, 'elapsed_seconds': round(time.monotonic()-started, 2),
             'criteria': criteria, 'passed': all(criteria.values()), 'role_report': content,
             'execution': {key: execution[key] for key in ('model','reasoning_effort','web_search','read_only','runtime_ms') if key in execution},
             'source_context': context, 'source_gate_checks': gates, 'result': result,
             'native_report_calls_attempted': len(attempts), 'native_report_calls_completed': len(completed),
             'checkpoint_stage': cycle['stage'], 'model_reexecuted_on_checkpoint_read': False,
             'limitations': ['Only one fresh role report was generated from previously collected public sources.',
                             'Child proposals are not approved or executed; no forced branching quota.',
                             'Three separate historical review cases run afterward; they do not review or approve this new report.',
                             'No production DB, finance, source collection, memory export or notification writes.']}
    atomic_json(directory / 'report.json', value)
    return value


def run_case(fixture, directory, client):
    if fixture.get('fresh_report'):
        return run_report_case(fixture, directory, client)
    database, cards = directory / 'research.sqlite3', directory / 'cards'
    store = research.Research(database, cards)
    try:
        gates = request_gates(store, fixture)
    finally:
        store.close()
    calls = []
    original = client.structured
    def observe(prompt, schema):
        result = original(prompt, schema)
        calls.append({'schema': schema, 'value': result})
        return result
    started = time.monotonic()
    with patch.object(client, 'structured', observe):
        result = research.run_step(fixture['job_id'], fixture['task'], db_path=database,
                                   cards_dir=cards, cli=client, cycle_id=fixture['cycle_id'])
    store = research.Research(database, cards)
    try:
        updates = [json.loads(row['detail']) for row in store.rows(
            "SELECT detail FROM demand_events WHERE cycle_id=? AND event='review_completed' AND id>? ORDER BY id",
            (fixture['cycle_id'], fixture['event_floor']))]
        updates = [row for row in updates if row['task_id'] == fixture['task_id']]
        current = agents.snapshot(store.db, fixture['cycle_id'])
        allowed_sources = research._agent_sources(store, fixture['cycle_id'], fixture['task_id'])
        normalization_events = [json.loads(row['detail']) for row in store.rows(
            "SELECT detail FROM demand_agent_events WHERE cycle_id=? AND task_id=? AND event='child_quote_normalized' ORDER BY id",
            (fixture['cycle_id'], fixture['task_id']))]
    finally:
        store.close()
    review = updates[-1]['review'] if updates else None
    requested = [item['key'] for item in fixture['report']['report']['child_requests']]
    decisions = review.get('decisions', []) if review else []
    execution = getattr(client, 'last_execution', {}) or {}
    effort = execution.get('reasoning_effort')
    replay = checkpoint_replay(database, cards, fixture, review) if review else {'checkpoint_advanced': False, 'no_duplicate_tasks': False}
    schema = calls[-1]['schema'] if calls else {}
    decision_schema = schema.get('properties', {}).get('decisions', {})
    enumeration = decision_schema.get('items', {}).get('properties', {}).get('key', {}).get('enum')
    exact_schema = (decision_schema.get('minItems') == len(requested) and decision_schema.get('maxItems') == len(requested)
                    and (not requested or set(enumeration or []) == set(requested)))
    new_tasks = [row for row in current['tasks'] if row['id'] not in fixture['task_ids_before']]
    permitted = {item['key'] for item in decisions if item['decision'] in ('approve', 'defer')}
    grandchild = None
    if fixture.get('quote_case'):
        requested_key = requested[0] if len(requested) == 1 else None
        chosen = next((item['decision'] for item in decisions if item['key'] == requested_key), None)
        created = [row for row in new_tasks if row['payload']['key'] == requested_key and row['parent_task_id'] == fixture['task_id']]
        exact = bool(created) and all(row['depth'] == 2 and any(
            agents._literal(row['payload']['trigger_quote']) in agents._literal(allowed_sources[key]['text'])
            for key in row['payload']['source_ids'] if key in allowed_sources) for row in created)
        normalized = bool(created) and all(row['payload']['trigger_quote'] == fixture['report']['report']['child_requests'][0]['trigger_quote'].strip()[1:-1].strip() for row in created)
        exercised = chosen == 'approve' and exact and normalized and any(row['status'] in ('pending','running') for row in created)
        grandchild = {'model_decision': chosen, 'created_task_ids': [row['id'] for row in created],
                      'normalized_exact_quote_and_depth_two': exact and normalized,
                      'normalization_event_recorded': any(item.get('key') == requested_key for item in normalization_events),
                      'admission_exercised': exercised, 'child_search_executed': False,
                      'missing_coverage': [] if exercised else ['Model did not admit an active grandchild in this probe; a legitimate defer/reject is not forced into approval.']}
    criteria = {'one_real_native_review_call': len(calls) == 1,
                'actual_medium_or_higher': effort in EFFORTS and EFFORTS.index(effort) >= EFFORTS.index('medium'),
                'analysis_tools_disabled': execution.get('read_only') is True and execution.get('web_search') == 'disabled'
                    and not (getattr(client, 'last_web_events', None) or []),
                'dynamic_schema_exact_requested_keys_and_count': exact_schema,
                'exact_review_decisions_no_implied_approval': review is not None and len(decisions) == len(requested)
                    and {item['key'] for item in decisions} == set(requested),
                'request_sources_and_quotes_verified': all(row['verified_quote_and_sources'] for row in gates),
                'new_tasks_only_from_explicit_decisions': all(row['payload']['key'] in permitted for row in new_tasks),
                'review_committed_once': result.get('ok') is True and len(updates) == 1,
                'checkpoint_resumes_without_review_reexecution': replay['checkpoint_advanced'],
                'no_duplicate_tasks_after_admission_replay': replay['no_duplicate_tasks']}
    if grandchild:
        criteria['explicit_grandchild_decision_and_verified_admission_if_created'] = bool(
            grandchild['model_decision'] in ('approve','defer','reject') and
            (not grandchild['created_task_ids'] or (grandchild['normalized_exact_quote_and_depth_two'] and grandchild['normalization_event_recorded'])) and
            (grandchild['model_decision'] != 'approve' or grandchild['created_task_ids']))
    value = {'diagnostic': 'historical-review-recovery-fixture', 'at': stamp(), 'cycle_id': fixture['cycle_id'],
             'task_id': fixture['task_id'], 'empty_case': fixture['expect_empty'], 'full_cycle': False,
             'elapsed_seconds': round(time.monotonic()-started, 2), 'criteria': criteria, 'passed': all(criteria.values()),
             'execution': {key: execution[key] for key in ('model','reasoning_effort','web_search','read_only','runtime_ms') if key in execution},
             'requested_keys': requested, 'review': review, 'source_gate_checks': gates, 'result': result,
             'historical_children_retained': fixture['historical_child_ids'], 'new_task_ids': [row['id'] for row in new_tasks],
             'checkpoint_replay': replay, 'native_review_calls_completed': len(calls),
             'grandchild_probe': grandchild,
             'limitations': ['Original production history was not changed.', 'Historical children are not new admissions or new executions.',
                            'Approve/defer/reject are actual model decisions; no approval or commercial result is forced.',
                            'Only review acceptance was tested; no child searches, synthesis, memory or notifications were run.']}
    atomic_json(directory / 'report.json', value)
    return value


def main():
    if len(sys.argv) != 1 or os.name != 'posix' or ROOT != Path('/opt/iva'):
        raise RuntimeError('review_probe_run_only_on_vps_without_arguments')
    directory = paths()
    with deployment_read_lock(), exclusive(directory):
        from iva_integration.run_pilot import env as server_env
        from iva_integration import research_priority
        os.environ.update(server_env())
        helper = Path('/usr/local/bin/iva-codex')
        if not helper.is_file() or not os.access(helper, os.X_OK):
            raise RuntimeError('vpn_codex_helper_unavailable')
        os.environ['FINANCE_CODEX_EXE'] = str(helper)
        snapshot = directory / 'source-snapshot.sqlite3'
        readonly_backup(SOURCE, snapshot)
        fixtures = {name: prepare_case(snapshot, directory / name, cycle, identity, name == 'empty',
                                       name in ('decorative_quote', 'fresh_report'), fresh_report=name == 'fresh_report')
                    for name, (cycle, identity) in CASES.items()}
        lease = {'job_id': 'diagnostic-review-' + hashlib.sha256(str(directory).encode()).hexdigest()[:16], 'cycle_id': 'diagnostic-review-cases'}
        reports = {}
        with patch.object(research_priority, 'STATUS', directory / 'priority-status.json'), research_lease(lease, directory):
            for name, fixture in fixtures.items():
                reports[name] = run_case(fixture, directory / name, ResearchAnalysisCodex(timeout=75, role='orchestrator'))
                print(json.dumps({'case': name, 'passed': reports[name]['passed'], 'criteria': reports[name]['criteria'],
                                  'execution': reports[name]['execution']}, ensure_ascii=True), flush=True)
        value = {'diagnostic': 'historical-review-recovery-fixture', 'at': stamp(),
                 'passed': all(row['passed'] for row in reports.values()), 'cases': reports,
                 'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                 'writes': 'Only fixed verification copies/reports and normal research lease; production research opened read-only; no finance, memory or notification writes.'}
        atomic_json(directory / 'review-report.json', value)
        return 0 if value['passed'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'diagnostic': 'historical-review-recovery-fixture', 'passed': False, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(2)
