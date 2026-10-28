"""Exercise retention against aged consistent *copies* of real VPS databases.

No models, scheduler execution, notifications, cards exports or production writes.
The private verification directory contains ledger copies; stdout/report contain
only statistics and verification flags, never records, prompts or credentials.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def safe_directory(source_root, output):
    source_root, output = Path(source_root), Path(output)
    require(source_root.is_absolute() and output.is_absolute(), 'absolute_paths_required')
    require(source_root.is_dir() and not source_root.is_symlink(), 'invalid_source_root')
    base = (source_root / 'data').resolve(strict=True)
    require(output.parent.is_dir() and not output.exists() and not output.is_symlink(), 'fresh_verification_directory_required')
    require(output.parent.resolve().is_relative_to(base), 'verification_must_be_inside_source_data')
    for path in [output, *output.parents]:
        require(not path.is_symlink(), 'verification_symlink_rejected')
        if path == source_root:
            break
    output.mkdir(mode=0o700)
    os.chmod(output, 0o700)
    return output


def readonly_backup(source, target):
    require(source.is_file() and not source.is_symlink(), 'invalid_snapshot_source')
    require(not target.exists() and not target.is_symlink(), 'snapshot_target_already_exists')
    require(source.resolve() != target.resolve(), 'snapshot_cannot_replace_source')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=15)) as original, \
            closing(sqlite3.connect(target)) as copied:
        original.backup(copied, pages=256)
        require(copied.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'snapshot_integrity_failed')
    os.chmod(target, 0o600)


def rows(db, sql, args=()):
    cursor = db.execute(sql, args)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      default=lambda item: {'bytes_hex': bytes(item).hex()}).encode('utf-8')


def fingerprint_records(records):
    hashes = sorted(hashlib.sha256(encoded(record)).digest() for record in records)
    return {'rows': len(hashes), 'sha256': hashlib.sha256(b''.join(hashes)).hexdigest()}


def tables(db):
    return [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def fingerprint_table(db, table):
    require(table.replace('_', '').isalnum(), 'unsafe_table_identifier')
    return fingerprint_records(rows(db, 'SELECT * FROM "' + table + '"'))


def logical_bytes(db):
    total = 0
    for table in tables(db):
        require(table.replace('_', '').isalnum(), 'unsafe_table_identifier')
        columns = [row[1] for row in db.execute('PRAGMA table_info("' + table + '")')]
        require(all(name.replace('_', '').isalnum() for name in columns), 'unsafe_column_identifier')
        expression = '+'.join('COALESCE(length(CAST("' + name + '" AS BLOB)),0)' for name in columns)
        total += db.execute('SELECT COALESCE(sum(' + expression + '),0) FROM "' + table + '"').fetchone()[0]
    return total


class CounterSink(io.RawIOBase):
    def __init__(self):
        super().__init__()
        self.count = 0

    def writable(self):
        return True

    def write(self, value):
        self.count += len(value)
        return len(value)


def gzip_bytes(path):
    sink = CounterSink()
    with path.open('rb') as source, gzip.GzipFile(filename='', mode='wb', fileobj=sink, compresslevel=6, mtime=0) as zipped:
        shutil.copyfileobj(source, zipped, length=1024 * 1024)
    return sink.count


def measurement(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        value = {'logical_bytes': logical_bytes(db),
                 'pages': db.execute('PRAGMA page_count').fetchone()[0],
                 'free_pages': db.execute('PRAGMA freelist_count').fetchone()[0]}
    return {**value, 'file_bytes': path.stat().st_size, 'gzip_bytes': gzip_bytes(path)}


def cycle_protection(db, ids):
    result = {}
    for table in ('demand_cycles', 'demand_candidates', 'demand_batches', 'demand_events',
                  'demand_agent_plans', 'demand_agent_tasks', 'demand_agent_events', 'demand_agent_syntheses'):
        column = 'id' if table == 'demand_cycles' else 'cycle_id'
        records = []
        for identity in ids:
            records.extend(rows(db, 'SELECT * FROM ' + table + ' WHERE ' + column + '=?', (identity,)))
        result[table] = fingerprint_records(records)
    source_ids = set()
    for identity in ids:
        source_ids.update(row[0] for row in db.execute('SELECT source_id FROM demand_candidates WHERE cycle_id=? AND source_id IS NOT NULL', (identity,)))
    result['source_bodies'] = fingerprint_records([row for identity in source_ids for row in
        rows(db, 'SELECT * FROM demand_sources WHERE id=?', (identity,))])
    return result


def normalize_coverage(value):
    return {key: child for key, child in value.items() if key not in ('compacted_at', 'coverage_view')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path('/opt/iva'))
    parser.add_argument('--code-root', type=Path, default=Path('/opt/iva'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--simulate-scheduler-idle', action='store_true',
                        help='In the private copy only, change running research job to active after capturing its cycle protections.')
    arguments = parser.parse_args()
    code_root = arguments.code_root.resolve(strict=True)
    require((code_root / 'iva_integration/demand_retention.py').is_file(), 'staged_retention_code_missing')
    sys.path.insert(0, str(arguments.source_root.resolve(strict=True)))
    sys.path.insert(0, str(code_root))
    from iva_integration import demand_research as research
    from iva_integration import demand_retention as retention
    from iva_integration import demand_schedule_retention as schedules

    output = safe_directory(arguments.source_root, arguments.output)
    sources = {'research': arguments.source_root / 'data/demand-research.sqlite3',
               'finance': arguments.source_root / 'data/local-finance.sqlite3'}
    require(shutil.disk_usage(output).free >= sum(path.stat().st_size for path in sources.values()) * 3 + 64 * 1024**2,
            'insufficient_space_for_isolated_verification')
    copies = {key: output / (key + '.sqlite3') for key in sources}
    for key in sources:
        readonly_backup(sources[key], copies[key])
    report = {'ok': False, 'scope': 'read_only_production_backups_then_private_aged_copies',
              'models_called': 0, 'production_mutations': 0, 'hypotheses_created': 0}
    clock = datetime.now(timezone.utc)
    old = (clock - timedelta(days=40)).isoformat(timespec='seconds')
    cutoff = clock - timedelta(days=14)
    store = research.Research(copies['research'], output / 'cards-not-created')
    finance = sqlite3.connect(copies['finance'], timeout=15)
    try:
        protected = {row['id'] for row in store.rows("SELECT id FROM demand_cycles WHERE state='running' OR stage='export_pending'")}
        idle_fixture_jobs = []
        for job in rows(finance, 'SELECT id,spec,cursor,state FROM scheduled_jobs'):
            if json.loads(job['spec']).get('kind') == 'demand_research':
                protected.update(schedules._references(json.loads(job['cursor']))[0])
                if arguments.simulate_scheduler_idle and job['state'] == 'running':
                    idle_fixture_jobs.append(job['id'])
        completed = store.rows("SELECT id FROM demand_cycles WHERE stage='done' AND state IN ('completed','completed_with_gaps') ORDER BY created")
        # One additional historical cycle simulates a scheduler-owned pin even
        # when the captured server happens to be between active cycles.
        if len(completed) > 1:
            protected.add(completed[-1]['id'])
        with store.db:
            store.db.execute('UPDATE demand_cycles SET finished=? WHERE finished IS NOT NULL', (old,))
            store.db.execute('UPDATE demand_sources SET fetched_at=?', (old,))
        with finance:
            finance.execute('UPDATE scheduled_runs SET finished=? WHERE finished IS NOT NULL', (old,))
            finance.executemany("UPDATE scheduled_jobs SET state='active' WHERE id=? AND state='running'",
                                [(identity,) for identity in idle_fixture_jobs])
        report['copy_fixture'] = {'old_dates_simulated': True, 'running_research_jobs_simulated_idle': len(idle_fixture_jobs),
                                  'production_state_changed': False, 'current_cycle_and_cursor_protections_retained': True}

        knowledge_tables = ('demand_hypotheses', 'demand_evidence', 'demand_revisions',
                            'demand_questions', 'demand_question_events', 'demand_cycle_hypotheses')
        knowledge = {table: fingerprint_table(store.db, table) for table in knowledge_tables}
        source_metadata = fingerprint_records(store.rows('SELECT id,url,title,content_hash,fetched_at,published_at FROM demand_sources'))
        protected_before = cycle_protection(store.db, protected)
        finance_tables = {table: fingerprint_table(finance, table) for table in tables(finance) if table != 'scheduled_runs'}
        run_records = {row['id']: row for row in rows(finance, 'SELECT * FROM scheduled_runs')}
        jobs = {row['id']: json.loads(row['spec']).get('kind') for row in rows(finance, 'SELECT id,spec FROM scheduled_jobs')}
        history = {cycle['id']: research._reported_cycle(store, cycle) for cycle in
                   store.rows("SELECT * FROM demand_cycles WHERE stage='done'")}
        report['before'] = {key: measurement(path) for key, path in copies.items()}

        dry_before = fingerprint_records(store.rows('SELECT * FROM demand_cycles'))
        dry = retention.compact(store, clock=clock, protected_cycle_ids=protected)
        require(dry_before == fingerprint_records(store.rows('SELECT * FROM demand_cycles')), 'research_dry_run_changed_data')
        research_results, compacted_ids = [], set()
        for _ in range(100):
            result = retention.compact(store, clock=clock, protected_cycle_ids=protected, dry_run=False)
            research_results.append(result)
            compacted_ids.update(result['cycle_ids'])
            if not result['more_eligible']:
                break
        else:
            raise RuntimeError('research_probe_batch_limit')
        require(research_results[0]['logical_bytes_after'] == dry['logical_bytes_after'], 'research_dry_apply_estimate_mismatch')
        require(compacted_ids, 'no_complete_historical_cycles_exercised')
        for identity in compacted_ids:
            after = research.execute({'action': 'report', 'id': identity}, copies['research'], store.cards)['cycles'][0]
            before = history[identity]
            require(after['coverage_view'] == 'preserved_at_compaction', 'archive_api_marker_missing')
            require(normalize_coverage(after['coverage']) == normalize_coverage(before['coverage']), 'historical_coverage_changed')
            require(after.get('orchestration') == before.get('orchestration'), 'historical_research_team_changed')
            require(after['state'] == before['state'], 'historical_outcome_changed')
            research.execute({'action': 'sources', 'id': identity}, copies['research'], store.cards)
        research.execute({'action': 'hypotheses'}, copies['research'], store.cards)
        again = retention.compact(store, clock=clock, protected_cycle_ids=protected, dry_run=False)
        require(again['cycles_compacted'] == 0 and again['source_bodies_pruned'] == 0 and again['logical_bytes_reclaimed'] == 0,
                'research_retention_not_idempotent')
        require(knowledge == {table: fingerprint_table(store.db, table) for table in knowledge_tables}, 'research_knowledge_changed')
        require(source_metadata == fingerprint_records(store.rows('SELECT id,url,title,content_hash,fetched_at,published_at FROM demand_sources')),
                'source_provenance_changed')
        require(protected_before == cycle_protection(store.db, protected), 'protected_cycle_or_input_changed')

        schedule_results, changed_runs = [], set()
        finance.execute('PRAGMA secure_delete=ON')
        schedule_preview = schedules.plan(finance, cutoff, protected_cycle_ids=protected, now=clock)
        for _ in range(100):
            result = schedules.apply(finance, cutoff, protected_cycle_ids=protected, now=clock)
            schedule_results.append(result)
            changed_runs.update(result['candidate_run_ids'])
            if not result['more_eligible']:
                break
        else:
            raise RuntimeError('schedule_probe_batch_limit')
        require(schedule_results[0]['updated_rows'] == schedule_preview['candidate_rows'], 'schedule_dry_apply_count_mismatch')
        if len(completed) > 1 and len(run_records) > schedules.KEEP_RECENT * 2:
            require(changed_runs, 'no_scheduler_compaction_exercised_use_idle_snapshot_or_simulate_scheduler_idle')
        require(schedules.apply(finance, cutoff, protected_cycle_ids=protected, now=clock)['updated_rows'] == 0,
                'schedule_retention_not_idempotent')
        after_runs = {row['id']: row for row in rows(finance, 'SELECT * FROM scheduled_runs')}
        require(set(after_runs) == set(run_records), 'scheduled_runs_added_or_removed')
        for identity, original in run_records.items():
            after = after_runs[identity]
            if identity not in changed_runs:
                require(after == original, 'unselected_scheduler_run_changed')
                continue
            require(jobs[original['job_id']] == 'demand_research', 'nonresearch_scheduler_run_changed')
            require({key: value for key, value in original.items() if key != 'result'} ==
                    {key: value for key, value in after.items() if key != 'result'}, 'scheduler_run_metadata_changed')
            prior_value, compact_value = json.loads(original['result']), json.loads(after['result'])
            require(compact_value['storage']['sha256'] == hashlib.sha256(original['result'].encode()).hexdigest(),
                    'scheduler_result_digest_mismatch')
            require(all(compact_value.get(key) == prior_value.get(key) for key in schedules.PRESERVE),
                    'scheduler_result_meaning_changed')
        require(finance_tables == {table: fingerprint_table(finance, table) for table in tables(finance) if table != 'scheduled_runs'},
                'financial_cursor_outbox_or_other_table_changed')
        require(not store.cards.exists(), 'probe_exported_cards')
        report.update(ok=True, checks={
            'hypotheses_evidence_revisions_questions_dedup_unchanged': True,
            'source_metadata_unchanged': True, 'protected_cycles_and_inputs_unchanged': True,
            'finance_jobs_cursors_outbox_other_tables_unchanged': True,
            'only_selected_research_run_results_changed': True,
            'archive_report_sources_hypotheses_apis': True, 'history_outcomes_unchanged': True,
            'research_and_scheduler_idempotent': True, 'no_cards_or_exports_created': True},
            counts={'protected_cycles': len(protected), 'cycles_compacted': len(compacted_ids),
                    'source_bodies_pruned': sum(item['source_bodies_pruned'] for item in research_results),
                    'research_batches': len(research_results), 'schedule_batches': len(schedule_results),
                    'scheduler_runs_compacted': len(changed_runs), 'scheduler_runs_total': len(run_records)},
            research_logical_bytes_reclaimed=sum(item['logical_bytes_reclaimed'] for item in research_results),
            scheduler_result_bytes_reclaimed=sum(item['reclaimed_logical_bytes'] for item in schedule_results),
            schedule_skips=schedule_preview['skipped'])
    finally:
        store.close()
        finance.close()
    report['after_compaction'] = {key: measurement(path) for key, path in copies.items()}
    for path in copies.values():
        with closing(sqlite3.connect(path)) as db:
            db.execute('VACUUM')  # This is the private verification copy, never production.
            require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'compacted_copy_integrity_failed')
    report['after_copy_vacuum'] = {key: measurement(path) for key, path in copies.items()}
    report['vacuum_scope'] = 'verification_copies_only; production_financial_database_not_vacuumed'
    destination = output / 'retention-report.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(destination, 0o600)
    print(json.dumps(report, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
