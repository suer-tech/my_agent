"""Bounded native scope probes on isolated databases, never a live-cycle replay.

The historical mixed plan is reviewed again under the new direction contract.
A fresh plan and two explicitly synthetic child cases exercise admission only:
no searches, source fetching, child execution, exports or notifications follow.
Run on the VPS only after staging the code; a fresh private output is required.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from unittest.mock import patch


HISTORICAL_CYCLE = 'fd1447d5470f4512'
MAX_NATIVE_CALLS = 6
EFFORTS = ('none', 'low', 'medium', 'high', 'xhigh', 'max')


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def readonly_backup(source, target):
    source, target = Path(source), Path(target)
    if source.is_symlink() or target.is_symlink() or target.exists() or source.resolve() == target.resolve():
        raise ValueError('scope_probe_backup_path_invalid')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original, \
         closing(sqlite3.connect(target)) as copied:
        original.backup(copied)
        if copied.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('scope_probe_backup_integrity_failed')
        copied.commit()
    os.chmod(target, 0o600)


def output_path(source_root, code_root, output):
    source_root, code_root, output = map(Path, (source_root, code_root, output))
    if source_root != Path('/opt/iva') or not code_root.is_dir():
        raise ValueError('scope_probe_root_invalid')
    if output.exists() or not output.parent.is_dir():
        raise ValueError('scope_probe_output_must_be_new')
    if not output.resolve().is_relative_to((source_root / 'data').resolve()):
        raise ValueError('scope_probe_output_outside_private_data')
    for candidate in (source_root, code_root, output, *output.parents):
        if candidate.is_symlink():
            raise ValueError('scope_probe_symlink_rejected')
    output.mkdir(mode=0o700)
    return output


def material_digest(store):
    """Scope review may append audit events, but cannot rewrite historical work."""
    tables = ('demand_cycles', 'demand_agent_plans', 'demand_agent_tasks',
              'demand_candidates', 'demand_sources', 'demand_hypotheses',
              'demand_evidence', 'demand_questions')
    values = {}
    existing = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in tables:
        if name in existing:
            rows = store.rows('SELECT * FROM ' + name + ' ORDER BY rowid')
            values[name] = {'count': len(rows), 'digest': hashlib.sha256(
                json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    return values


class ObservedClient:
    def __init__(self, native, records, label):
        self.native, self.records, self.label = native, records, label

    def structured(self, prompt, schema):
        if len(self.records) >= MAX_NATIVE_CALLS:
            raise RuntimeError('scope_probe_native_call_ceiling')
        record = {'case': self.label, 'kind': 'scope_audit' if 'scope_decisions' in schema['properties'] else 'plan',
                  'completed': False, 'owner_marker_leaked': 'OWNER_SCOPE_PROBE_912' in prompt}
        self.records.append(record)
        started = time.monotonic()
        try:
            result = self.native.structured(prompt, schema)
            record['completed'] = True
            return result
        finally:
            record['elapsed_seconds'] = round(time.monotonic() - started, 2)
            execution = getattr(self.native, 'last_execution', {}) or {}
            record['execution'] = {key: execution[key] for key in
                ('model', 'reasoning_effort', 'web_search', 'read_only', 'runtime_ms') if key in execution}
            record['web_event_count'] = len(getattr(self.native, 'last_web_events', None) or [])


def run_cases(snapshot, output, client_factory, historical_id=HISTORICAL_CYCLE):
    from iva_integration import demand_agents as agents
    from iva_integration import demand_research as research
    from iva_integration import demand_scope as scope

    calls = []
    history_db = output / 'historical-review.sqlite3'
    readonly_backup(snapshot, history_db)
    store = research.Research(history_db, output / 'historical-cards')
    try:
        found = store.rows('SELECT * FROM demand_cycles WHERE id=?', (historical_id,))
        if len(found) != 1 or found[0]['track'] != 'software':
            raise ValueError('scope_probe_historical_software_cycle_missing')
        original_cycle = found[0]
        historical = agents.plan(store.db, historical_id)
        if not historical:
            raise ValueError('scope_probe_historical_agent_plan_missing')
        plan = json.loads(original_cycle['plan'])
        plan['scope'] = scope.contract(original_cycle['track'], original_cycle['topic'],
            question=plan.get('question'), revisit=plan.get('revisit'), open_exploration=plan.get('open_exploration'))
        # The old stored cycle remains byte-for-byte intact: only this in-memory
        # review input receives a new scope contract. Old child history remains.
        cycle = {**original_cycle, 'plan': json.dumps(plan, ensure_ascii=False)}
        before = material_digest(store)
        rejection = None
        try:
            scope.validate_plan(store, cycle, json.loads(historical['payload']),
                                ObservedClient(client_factory(), calls, 'historical_mixed_plan'))
        except scope.ScopeError as exc:
            rejection = str(exc)
        feedback = scope.plan_feedback(store, cycle)
        historical_result = {'rejection': rejection, 'decisions': feedback,
            'criteria': {'real_historical_plan_rejected_before_search': rejection == 'research_scope_plan_rejected',
                         'historical_material_unchanged': material_digest(store) == before}}
    finally:
        store.close()

    fresh = research.Research(output / 'fresh-plan.sqlite3', output / 'fresh-cards')
    try:
        owner = 'OWNER_SCOPE_PROBE_912: Исследовать спрос на ПО, а также товары и услуги для продвижения через MAX.'
        with patch.object(research, 'TRACKS', (('software', original_cycle['topic']),)), \
             patch.object(research, 'AGENT_RESEARCH_ENABLED', True):
            cycle = fresh.cycle('diagnostic-scope-fresh', owner)
        errors = []
        for attempt in range(2):
            try:
                research._agent_plan(fresh, cycle, ObservedClient(client_factory(), calls, 'fresh_plan_' + str(attempt + 1)))
                break
            except scope.ScopeError as exc:
                errors.append(str(exc))
                if str(exc) != 'research_scope_plan_rejected':
                    break
        current = fresh.rows('SELECT * FROM demand_cycles WHERE id=?', (cycle['id'],))[0]
        tasks = agents.snapshot(fresh.db, cycle['id'])['tasks']
        accepted = agents.plan(fresh.db, cycle['id'])
        fresh_result = {'retry_errors': errors, 'plan': json.loads(accepted['payload']) if accepted else None,
            'criteria': {'new_plan_passed_semantic_audit': current['stage'] == 'discovery' and len(tasks) == 4,
                         'no_search_or_fetch_executed': not fresh.rows('SELECT * FROM demand_candidates')
                             and not fresh.rows('SELECT * FROM demand_sources'),
                         'broad_owner_goal_not_passed_to_model': not any(row['owner_marker_leaked'] for row in calls),
                         'no_card_export': not fresh.cards.exists() or not any(fresh.cards.iterdir())}}
    finally:
        fresh.close()

    children = research.Research(output / 'synthetic-children.sqlite3', output / 'synthetic-cards')
    try:
        with patch.object(research, 'TRACKS', (('software', 'ПО для синхронизации складских остатков небольших продавцов'),)), \
             patch.object(research, 'AGENT_RESEARCH_ENABLED', True):
            cycle = children.cycle('diagnostic-scope-synthetic', 'Only synthetic semantic admission cases')
        parent = {'id': 'synthetic-parent', 'role': 'buyer_problem',
                  'question': 'Как продавцы вручную синхронизируют остатки и заказы?',
                  'scope': 'Ручная сверка остатков между складом продавца и витриной магазина',
                  'methods': 'Разобрать рабочий процесс и существующие программные альтернативы'}
        proposals = [
            {'key': 'software_inventory', 'question': 'Нужно ли продавцам хозблоков ПО для синхронизации складских остатков?',
             'scope': 'Именно программная синхронизация наличия и заказов; физический товар является предметом учёта',
             'why_needed': 'Сохраняется тот же покупатель-продавец и тот же процесс сверки остатков, уточняется один сегмент',
             'trigger_quote': 'Synthetic scenario: sellers manually reconcile stock and orders.'},
            {'key': 'sell_goods_commission', 'question': 'Выгодно ли продавать хозблоки за комиссию через MAX?',
             'scope': 'Подбор производителей и получение комиссии за продажу физических хозблоков конечным покупателям',
             'why_needed': 'Предлагается отдельная идея заработка на продаже товара, без разработки программного решения',
             'trigger_quote': 'Synthetic scenario: sellers manually reconcile stock and orders.'},
        ]
        client = ObservedClient(client_factory(), calls, 'synthetic_business_directions')
        result = scope.guard_children(children, cycle, parent, proposals, client)
        before_replay = len(calls)
        replay = scope.guard_children(children, cycle, parent, proposals, client)
        child_result = {'allowed_keys': [item['key'] for item in result['requests']], 'limitations': result['limitations'],
            'criteria': {'software_about_physical_goods_allowed': [item['key'] for item in result['requests']] == ['software_inventory'],
                         'physical_sales_cross_lane_rejected': any(item['key'] == 'sell_goods_commission' and
                             item['reason'] == 'research_scope_cross_lane' for item in result['limitations']),
                         'same_proposal_replay_uses_cached_audit': replay == result and len(calls) == before_replay,
                         'no_child_task_admitted_or_executed': not children.rows('SELECT * FROM demand_agent_tasks')},
            'scope': 'Synthetic purpose-classification fixtures only; quotations are not evidence and no enqueue or source verification was attempted.'}
    finally:
        children.close()

    execution_ok = all(row['completed'] and row['execution'].get('reasoning_effort') in EFFORTS and
        EFFORTS.index(row['execution']['reasoning_effort']) >= EFFORTS.index('medium') and
        row['execution'].get('web_search') == 'disabled' and row['execution'].get('read_only') is True and
        row['web_event_count'] == 0 for row in calls)
    cases = {'historical': historical_result, 'fresh': fresh_result, 'children': child_result}
    return {'at': stamp(), 'diagnostic': 'research-direction-native-admission',
            'passed': bool(calls) and execution_ok and all(all(value['criteria'].values()) for value in cases.values()),
            'native_calls': calls, 'native_execution_medium_tools_disabled': execution_ok, 'cases': cases,
            'limitations': ['No full research cycle or empirical demand finding was produced.',
                'Synthetic child cases exercise semantic boundaries only; no real source citation or child execution is claimed.',
                'Semantic auditing is a probabilistic guard; actual decisions are retained for review.',
                'Production research database opened only read-only for backup. All subsequent writes are isolated probes and audit files.',
                'No finance, messaging, owner-vault, browser, scheduler or public-site mutations. Normal coordinator lease is used.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path('/opt/iva'))
    parser.add_argument('--code-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cycle-id', default=HISTORICAL_CYCLE)
    args = parser.parse_args()
    if os.name != 'posix':
        raise RuntimeError('scope_probe_requires_vps')
    output = output_path(args.source_root, args.code_root, args.output)
    source_root, code_root = args.source_root.resolve(), args.code_root.resolve()
    sys.path[:0] = [str(code_root), str(source_root)]
    from iva_integration import demand_scope, demand_research, research_priority
    from deploy.verify_demand_agents import deployment_read_lock
    from deploy.verify_demand_branch import atomic_json, exclusive, research_lease
    # Existing diagnostic helpers may put their own source root first.
    sys.path[:0] = [str(code_root), str(source_root)]
    from iva_integration.demand_sources import ResearchAnalysisCodex
    if Path(demand_scope.__file__).resolve() != code_root / 'iva_integration/demand_scope.py' or \
       Path(demand_research.__file__).resolve() != code_root / 'iva_integration/demand_research.py':
        raise RuntimeError('scope_probe_staged_code_not_loaded')
    from iva_integration import run_pilot
    with patch.object(run_pilot, 'APP', source_root / 'data/iva-pilot/app'):
        os.environ.update(run_pilot.env())
    helper = Path('/usr/local/bin/iva-codex')
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise RuntimeError('scope_probe_vpn_helper_unavailable')
    os.environ['FINANCE_CODEX_EXE'] = str(helper)
    snapshot = output / 'source-snapshot.sqlite3'
    lease = {'job_id': 'diagnostic-scope-' + hashlib.sha256(str(output).encode()).hexdigest()[:16],
             'cycle_id': 'diagnostic-scope-admission'}
    report = None
    with deployment_read_lock(source_root / 'data/application-deploy.lock'), exclusive(output):
        readonly_backup(source_root / 'data/demand-research.sqlite3', snapshot)
        with patch.object(research_priority, 'STATUS', output / 'priority-status.json'), research_lease(lease, output):
            report = run_cases(snapshot, output, lambda: ResearchAnalysisCodex(
                timeout=75, role='orchestrator', minimum_reasoning_effort='medium'), args.cycle_id)
        report['source_snapshot_sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        atomic_json(output / 'scope-report.json', report)
    print(json.dumps({'passed': report['passed'], 'native_calls': len(report['native_calls']),
                      'criteria': {key: value['criteria'] for key, value in report['cases'].items()}}, ensure_ascii=True), flush=True)
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'diagnostic': 'research-direction-native-admission', 'passed': False,
                          'error_type': type(error).__name__, 'error_code': str(error)[:160] if isinstance(error, (ValueError, RuntimeError)) else None}), flush=True)
        raise SystemExit(2)
