"""Live citation repair on a private copy of the observed failed foreign report.

No new search, child execution, synthesis, exports, notifications or finance I/O.
The original completed history remains immutable; only the copy is rewound.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from contextlib import closing
from unittest.mock import patch

CYCLE = 'fd1447d5470f4512'
TASK = 'agt_1591e6b7ffde4c49af341fc852d8c3b9'
BAD_KEY = 'foreign_referral_outcomes'


def historical_fingerprint(database):
    """Measure only the old diagnostic cycle, not unrelated current writes."""
    with closing(sqlite3.connect(Path(database).as_uri() + '?mode=ro', uri=True)) as connection:
        state = {}
        for table, field in [('demand_cycles', 'id'), ('demand_agent_tasks', 'cycle_id'),
                             ('demand_agent_syntheses', 'cycle_id'), ('demand_events', 'cycle_id')]:
            state[table] = connection.execute('SELECT * FROM ' + table + ' WHERE ' + field + '=? ORDER BY rowid',
                                              (CYCLE,)).fetchall()
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path('/opt/iva'))
    parser.add_argument('--code-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source_root, code_root, directory = args.source_root, args.code_root, args.output
    if os.name != 'posix' or source_root != Path('/opt/iva') or not code_root.is_absolute() or not code_root.is_dir():
        raise RuntimeError('quote_probe_requires_vps_and_valid_staged_code')
    if (not directory.is_absolute() or directory.exists() or not directory.parent.is_dir()
            or not directory.parent.name.startswith('demand-release-') or directory.parent.parent != source_root / 'data'):
        raise ValueError('invalid_quote_probe_output')
    for path in [directory, *directory.parents, code_root, *code_root.parents]:
        if path.is_symlink():
            raise ValueError('quote_probe_symlink_rejected')
    sys.path[:0] = [str(code_root), str(source_root)]
    from iva_integration import demand_agents as agents, demand_research as research, demand_quote_repair, research_priority
    from deploy.verify_demand_agents import deployment_read_lock, stamp
    from deploy.verify_demand_branch import atomic_json, exclusive, research_lease
    from deploy.verify_demand_review import readonly_backup, prepare_case, checkpoint_replay
    # Diagnostic helpers can prepend their own roots. Keep changed modules from
    # staging while unmodified local_finance/runtime modules fall back to live.
    sys.path[:0] = [str(code_root), str(source_root)]
    from iva_integration.demand_sources import ResearchAnalysisCodex
    for module in (agents, research, demand_quote_repair):
        if Path(module.__file__).resolve() != code_root / 'iva_integration' / (module.__name__.split('.')[-1] + '.py'):
            raise RuntimeError('quote_probe_staged_code_not_loaded')
    production = source_root / 'data/demand-research.sqlite3'
    def require_idle():
        with closing(sqlite3.connect(production.as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute("SELECT 1 FROM demand_cycles WHERE state='running' LIMIT 1").fetchone():
                raise RuntimeError('quote_probe_production_cycle_active')
    require_idle()
    directory.mkdir(mode=0o700)
    with deployment_read_lock(source_root / 'data/application-deploy.lock'), exclusive(directory):
        from iva_integration import run_pilot
        with patch.object(run_pilot, 'APP', source_root / 'data/iva-pilot/app'):
            os.environ.update(run_pilot.env())
        os.environ['FINANCE_CODEX_EXE'] = '/usr/local/bin/iva-codex'
        before_history = historical_fingerprint(production)
        snapshot = directory / 'source-snapshot.sqlite3'
        readonly_backup(production, snapshot)
        fixture = prepare_case(snapshot, directory / 'case', CYCLE, TASK, expect_empty=False)
        database, cards = directory / 'case/research.sqlite3', directory / 'case/cards'
        original = fixture['report']['report']
        before = next(item for item in original['child_requests'] if item['key'] == BAD_KEY)
        lease = {'job_id': 'diagnostic-quote-' + hashlib.sha256(str(directory).encode()).hexdigest()[:16],
                 'cycle_id': 'diagnostic-translated-quote'}
        cli = ResearchAnalysisCodex(timeout=75, role='orchestrator', minimum_reasoning_effort='medium')
        calls = []
        correction_responses = []
        structured = cli.structured
        def counted(prompt, schema):
            if len(calls) >= 3:
                raise RuntimeError('quote_probe_native_call_budget_exhausted')
            execution = {'status': 'started'}
            calls.append(execution)
            try:
                result = structured(prompt, schema)
            except Exception as error:
                execution.update(status='failed', category=type(error).__name__)
                raise
            execution.update(status='completed', **{key: value for key, value in cli.last_execution.items()
                             if key in ('model', 'reasoning_effort', 'web_search', 'read_only', 'runtime_ms')})
            if isinstance(result, dict) and {'status', 'source_ids', 'trigger_quote'} <= set(schema.get('properties', {})):
                correction_responses.append({
                    'status': str(result.get('status', ''))[:20],
                    'source_ids': [str(item)[:100] for item in result.get('source_ids', [])[:6]],
                    'trigger_quote': str(result.get('trigger_quote', ''))[:500],
                    'reason': str(result.get('reason', ''))[:1500]})
            return result
        cli.structured = counted
        results = []
        with patch.object(research_priority, 'STATUS', directory / 'priority-status.json'), research_lease(lease, directory):
            require_idle()
            for _ in range(7):
                result = research.run_step(fixture['job_id'], fixture['task'], db_path=database,
                                           cards_dir=cards, cycle_id=CYCLE, cli=cli)
                results.append({key: result.get(key) for key in ('ok', 'stage', 'status', 'error_category')})
                if result.get('stage') != 'agent_review':
                    break
            store = research.Research(database, cards)
            try:
                saved = agents.task(store.db, TASK)
                result = saved.get('result') or {}
                requests = (result.get('report') or {}).get('child_requests', [])
                after = next((item for item in requests if item['key'] == BAD_KEY), None)
                sources = research._agent_sources(store, CYCLE, TASK)
                literal = False
                if after:
                    agents.validate_child_proposal(after, sources)
                    literal = True
                repairs = [json.loads(row['detail']) for row in store.rows(
                    "SELECT detail FROM demand_events WHERE cycle_id=? AND event='child_quote_repair'", (CYCLE,))]
                repaired = any(item.get('key') == BAD_KEY and item.get('outcome') == 'repaired' for item in repairs)
                original_retained = research._agent_events(store, CYCLE, 'role_report', TASK)[-1]['report'] == original
                valid_before = []
                for item in original['child_requests']:
                    try:
                        agents.validate_child_proposal(item, sources)
                    except ValueError:
                        pass
                    else:
                        valid_before.append(item)
                all_gates = all(agents.validate_child_proposal(item, sources) for item in requests)
                history = [item['id'] for item in agents.snapshot(store.db, CYCLE)['tasks']]
            finally:
                store.close()
            updated_fixture = {**fixture, 'report': {**fixture['report'], 'report': result.get('report', original)}}
            replay = checkpoint_replay(database, cards, updated_fixture, result.get('review', {'decisions': []}))
        criteria = {
            'actual_translated_citation_repaired': repaired and literal,
            'corrected_citation_differs_from_translation': bool(after and after['trigger_quote'] != before['trigger_quote']),
            'question_methods_criteria_unchanged': bool(after and
                {k: v for k, v in after.items() if k not in ('source_ids', 'trigger_quote')} ==
                {k: v for k, v in before.items() if k not in ('source_ids', 'trigger_quote')}),
            'all_retained_requests_still_pass_strict_source_gate': bool(all_gates),
            'original_report_retained': original_retained,
            'already_valid_sibling_requests_preserved': all(item in requests for item in valid_before),
            'production_historical_cycle_unchanged': historical_fingerprint(production) == before_history,
            'parent_report_completed': saved['status'] == 'done',
            'historical_children_not_removed': set(fixture['historical_child_ids']).issubset(history),
            'no_duplicate_children_after_admission_replay': replay['no_duplicate_tasks'],
            'checkpoint_advanced_without_search': replay['checkpoint_advanced'],
            'no_exports_created': not cards.exists(),
            'native_calls_tool_free_medium': bool(calls) and all(item.get('status') == 'completed'
                and item.get('read_only') is True and item.get('web_search') == 'disabled'
                and item.get('reasoning_effort') in ('medium', 'high', 'xhigh', 'max') for item in calls),
        }
        report = {'at': stamp(), 'passed': all(criteria.values()), 'criteria': criteria,
                  'native_calls': calls, 'step_results': results,
                  'repair_attempts': len([item for item in repairs if item.get('outcome') == 'started']),
                  'repair_outcomes': [item.get('outcome') for item in repairs],
                  'repair_validation': [{key: item[key] for key in ('attempt', 'category', 'word_count', 'word_limit', 'quote_characters')
                                         if key in item} for item in repairs if item.get('outcome') == 'invalid'],
                  'private_correction_responses': correction_responses,
                  'original_sibling_requests': len(original['child_requests']),
                  'valid_siblings_retained': len(valid_before),
                  'retained_requests_after_repair': len(requests),
                  'copied_cycle_id': CYCLE, 'copied_task_id': TASK,
                  'writes': 'Private research copies/reports and normal research lease only.',
                  'limitations': ['Original completed history was not changed.',
                                 'No child searches, synthesis, exports, notifications or financial writes were executed.',
                                 'Child admission/defer/reject follows the actual coordinator decision and remaining limits.']}
        atomic_json(directory / 'quote-repair-report.json', report)
        print(json.dumps({key: value for key, value in report.items() if key != 'private_correction_responses'}, ensure_ascii=False))
        return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
