"""Replay the actual duplicate-label failure only in an isolated historical copy."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Bind the selected application overlay before older verifier helpers adjust
# sys.path to their own checkout. Staged preflight must test staged core modules.
from iva_integration import demand_agents as agents, demand_research as research, demand_scope as scope
from iva_integration.demand_sources import ResearchAnalysisCodex
from deploy.verify_demand_agents import deployment_read_lock, stamp
from deploy.verify_demand_branch import atomic_json, exclusive, research_lease
from deploy.verify_demand_review import readonly_backup, prepare_case, request_gates

RELEASE = ROOT / 'data/demand-content-quality-release-20261025-r2'
OUTPUT = RELEASE / 'verification-request-keys'
CYCLE = '2e84da5616dd47f0'
TASK = 'agt_962b946824704343a82e9ab14a984b4d'


def run_fixture(fixture, directory, client):
    store = research.Research(directory / 'research.sqlite3', directory / 'cards')
    try:
        gates = request_gates(store, fixture)
        if not gates or not all(row['verified_quote_and_sources'] for row in gates):
            raise RuntimeError('historical_requests_require_unplanned_quote_repair')
        original = copy.deepcopy(fixture['report']['report']['child_requests'])
        if len(original) != 2 or len({row['key'] for row in original}) != 1:
            raise RuntimeError('historical_duplicate_fixture_changed')
        cycle = store.rows('SELECT * FROM demand_cycles WHERE id=?', (CYCLE,))[0]
    finally:
        store.close()
    calls = []
    structured = client.structured
    def observe(prompt, schema):
        result = structured(prompt, schema)
        props = schema['properties']
        kind = 'scope' if 'scope_decisions' in props else 'review'
        calls.append({'kind': kind, 'execution': {
            key: value for key, value in (getattr(client, 'last_execution', {}) or {}).items()
            if key in ('model', 'reasoning_effort', 'web_search', 'read_only', 'runtime_ms')},
            'web_events': len(getattr(client, 'last_web_events', []) or [])})
        return result
    with patch.object(client, 'structured', observe):
        result = research.run_step(fixture['job_id'], fixture['task'],
                                   db_path=directory / 'research.sqlite3', cards_dir=directory / 'cards',
                                   cli=client, cycle_id=CYCLE)
    store = research.Research(directory / 'research.sqlite3', directory / 'cards')
    try:
        task = agents.task(store.db, TASK)
        after = research._agent_events(store, CYCLE, 'role_report', TASK)[-1]['report']['child_requests']
        normalized = scope._bind_child_keys(store, cycle, task, original)
        bindings = research._agent_events(store, CYCLE, 'child_request_keys_bound', TASK)
        reviews = [row for row in store.rows("SELECT detail FROM demand_events WHERE cycle_id=? AND event='review_completed' AND id>?", (CYCLE, fixture['event_floor']))
                   if json.loads(row['detail']).get('task_id') == TASK]
        reviewed = json.loads(reviews[0]['detail']) if len(reviews) == 1 else {}
        sources = research._agent_sources(store, CYCLE, TASK)
        validated = all(agents.validate_child_proposal(row, sources) for row in normalized)
        decisions = reviewed.get('review', {}).get('decisions', [])
        preserved = all({key: value for key, value in left.items() if key != 'key'} ==
                        {key: value for key, value in right.items() if key != 'key'}
                        for left, right in zip(original, normalized))
        before_ids = [row['id'] for row in agents.snapshot(store.db, CYCLE)['tasks']]
        replay = []
        store.db.execute('SAVEPOINT replay_admission')
        try:
            for disposition in ('approve', 'defer'):
                keys = {row['key'] for row in decisions if row['decision'] == disposition}
                proposals = [row for row in normalized if row['key'] in keys]
                if proposals:
                    replay.append(agents.enqueue_children(store.db, TASK, proposals, sources,
                        defer_reason='orchestrator_deferred' if disposition == 'defer' else None))
            after_ids = [row['id'] for row in agents.snapshot(store.db, CYCLE)['tasks']]
        finally:
            store.db.execute('ROLLBACK TO replay_admission')
            store.db.execute('RELEASE replay_admission')
        no_later_stages = not store.rows("SELECT id FROM demand_events WHERE cycle_id=? AND id>? AND stage IN ('discovery','fetch','finalize')", (CYCLE, fixture['event_floor']))
    finally:
        store.close()
    criteria = {
        'actual_historical_duplicate_labels': len(original) == 2 and len({row['key'] for row in original}) == 1,
        'raw_report_unchanged': original == after,
        'only_labels_changed': preserved,
        'both_quotes_and_sources_still_verified': bool(validated),
        'binding_saved_once': len(bindings) == 1,
        'two_unique_decisions': len(decisions) == 2 and {row['key'] for row in decisions} == {row['key'] for row in normalized},
        'native_scope_and_coordinator_calls': [row['kind'] for row in calls] == ['scope', 'review'],
        'medium_tool_free_calls': len(calls) == 2 and all(row['execution'].get('reasoning_effort') in ('medium','high','xhigh','max','ultra') and
            row['execution'].get('web_search') == 'disabled' and row['execution'].get('read_only') is True and not row['web_events'] for row in calls),
        'review_checkpoint_completed': result.get('ok') is True and task['status'] == 'done' and len(reviews) == 1,
        'admission_replay_no_duplicates': before_ids == after_ids and all(not row['admitted'] and not row['deferred'] for row in replay),
        'no_search_export_or_notification': no_later_stages and result.get('notify') is False and not (directory / 'cards').exists(),
    }
    report = {'at': stamp(), 'cycle_id': CYCLE, 'task_id': TASK, 'passed': all(criteria.values()),
              'full_cycle': False, 'criteria': criteria, 'calls': calls,
              'decisions': [{'key': row['key'], 'decision': row['decision']} for row in decisions],
              'children': reviewed.get('children'), 'error_category': result.get('error_category'),
              'limits': 'Historical copied review only; no new searches, production mutations, export or notifications.'}
    atomic_json(directory / 'report.json', report)
    return report


def main():
    if len(sys.argv) != 1 or os.name != 'posix' or ROOT != Path('/opt/iva'):
        raise RuntimeError('run_fixed_probe_on_vps_only')
    if not RELEASE.is_dir() or any(path.is_symlink() for path in (ROOT / 'data', RELEASE, OUTPUT)) or OUTPUT.exists():
        raise RuntimeError('fresh_private_verification_directory_required')
    OUTPUT.mkdir(mode=0o700)
    with deployment_read_lock(), exclusive(OUTPUT):
        from iva_integration.run_pilot import env as server_env
        from iva_integration import research_priority
        os.environ.update(server_env())
        os.environ['FINANCE_CODEX_EXE'] = '/usr/local/bin/iva-codex'
        lease = {'job_id': 'diagnostic-labels-' + hashlib.sha256(str(OUTPUT).encode()).hexdigest()[:16],
                 'cycle_id': 'diagnostic-duplicate-request-keys'}
        with patch.object(research_priority, 'STATUS', OUTPUT / 'priority-status.json'), research_lease(lease, OUTPUT):
            snapshot = OUTPUT / 'source-snapshot.sqlite3'
            readonly_backup(ROOT / 'data/demand-research.sqlite3', snapshot)
            directory = OUTPUT / 'historical-review'
            fixture = prepare_case(snapshot, directory, CYCLE, TASK, False)
            if fixture['archive']['cycle']['state'] == 'running':
                raise RuntimeError('historical_cycle_not_terminal')
            report = run_fixture(fixture, directory, ResearchAnalysisCodex(timeout=90, role='orchestrator'))
            print(json.dumps(report, ensure_ascii=True), flush=True)
            return 0 if report['passed'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(2)
