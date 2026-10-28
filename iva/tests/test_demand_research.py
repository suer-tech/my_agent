import copy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from iva_integration import demand_research as dr


STAMP = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)
FIRST = 'Мне каждый день приходится вручную переносить заказы из таблицы в программу.'
SECOND = 'Программа предлагает автоматический перенос заказов по платной подписке.'
BUYER_TWO = 'На сверку остатков уходит вечер; ищу интеграцию, которая устраняет повторный ввод позиций.'
COUNTER = 'Бесплатная версия уже переносит все заказы без ограничений.'


def canonical(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname:
        raise ValueError('bad URL')
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


class FakeCli:
    def __init__(self, discovery=None, analysis=None, events=True):
        self.calls = []
        self.discovery = discovery or {
            'queries': ['перенос заказов вручную жалобы', 'автоматический перенос заказов тариф'],
            'candidates': [
                {'url': 'https://buyers.example/thread/1', 'purpose': 'support', 'source_type': 'request'},
                {'url': 'https://vendor.test/prices', 'purpose': 'alternative', 'source_type': 'offer'},
                {'url': 'https://operators.test/question/2', 'purpose': 'support', 'source_type': 'discussion'},
            ], 'limitations': [],
        }
        self.analysis = analysis
        self.events = events
        self.last_web_events = []

    def structured(self, prompt, schema):
        self.calls.append(prompt)
        if 'candidates' in schema['properties']:
            self.last_web_events = [{'type': 'web_search', 'query': 'public research',
                                     'results': [{'url': row['url'], 'title': 'Result'} for row in self.discovery['candidates']]}] if self.events else []
            return copy.deepcopy(self.discovery)
        value = copy.deepcopy(self.analysis)
        value.setdefault('followups', [])
        value.setdefault('branch_answer', {'verdict': 'inconclusive', 'basis': 'Нет активного уточняющего исследования.', 'source_ids': [], 'evidence': []})
        return value


def proposal(first, second, third=None, buyer_two=None):
    evidence = [
        {'source_id': first, 'quote': FIRST, 'interpretation': 'Есть описанная повторяемая ручная работа.',
         'kind': 'problem', 'source_type': 'request', 'origin_group': ''},
        {'source_id': second, 'quote': SECOND, 'interpretation': 'Существующая платная альтернатива требует сравнения.',
         'kind': 'alternative', 'source_type': 'offer', 'origin_group': ''},
    ]
    if third:
        evidence.append({'source_id': third, 'quote': COUNTER, 'interpretation': 'Бесплатный заменитель может опровергать идею.',
                         'kind': 'counter', 'source_type': 'offer', 'origin_group': ''})
    if buyer_two:
        evidence.append({'source_id': buyer_two, 'quote': BUYER_TWO, 'interpretation': 'Другой покупатель описывает повторный ввод и потери времени.',
                         'kind': 'workaround', 'source_type': 'discussion', 'origin_group': ''})
    return {'existing_id': '', 'key': 'order-transfer', 'title': 'Перенос заказов из таблиц',
            'buyer': 'Небольшие интернет-магазины', 'problem': 'Ежедневное ручное копирование заказов',
            'offer': 'Интеграция таблицы и учётной системы', 'geography': 'Не установлена',
            'monetization': 'Подписка; готовность платить и цена неизвестны.', 'evidence': evidence,
            'counterarguments': ['Уже есть платный конкурент; непокрытый сегмент ещё не определён.'],
            'unknowns': ['Размер сегмента неизвестен.'],
            'test_result': {'verdict': 'not_tested', 'basis': 'Пока новая гипотеза.', 'evidence_source_ids': []},
            'rubric': {key: {'rating': 'unknown', 'reason': 'Данных недостаточно.', 'evidence_source_ids': []}
                       for key in dr.RUBRIC_KEYS},
            'next_test': {'assumption': 'Существующие интеграции не покрывают нужный формат.',
                          'query': 'перенос заказов таблица отсутствует интеграция формат',
                          'method': 'Сравнить публичные запросы и документацию двух поставщиков.',
                          'metric': 'Число независимых запросов с конкретным отсутствующим форматом.',
                          'pass_condition': 'Два независимых запроса, отсутствующий формат указан в документации.',
                          'fail_condition': 'Бесплатный продукт поддерживает тот же формат без дополнительных действий.',
                          'manual_validation': 'Владелец отдельно проверяет готовность платить; автоматическая переписка не выполняется.'},
            'recommendation': 'investigate'}


class DemandResearch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'research.sqlite3'
        self.cards = Path(self.temp.name) / 'cards'
        self.clock = STAMP
        self.clock_patch = patch.object(dr, 'now', side_effect=lambda: self.clock.isoformat(timespec='seconds'))
        self.url_patch = patch.object(dr, '_canonical', side_effect=canonical)
        self.clock_patch.start()
        self.url_patch.start()
        self.fetch_calls = []
        self.tuning = patch.multiple(dr, BATCH_FOCI=('Контрпроверка и весь малый тестовый корпус',),
                                     MIN_READ_PAGES=3, MIN_DIRECT_ORIGINS=2, BREADTH_GOAL=3,
                                     AGENT_RESEARCH_ENABLED=False)
        self.tuning.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.url_patch.stop()
        self.tuning.stop()
        self.temp.cleanup()

    def fetcher(self, url):
        self.fetch_calls.append(url)
        quote = FIRST if 'buyers.' in url else COUNTER if 'counter.' in url else BUYER_TWO if 'operators.' in url else SECOND
        return {'ok': True, 'url': url, 'canonical_url': canonical(url), 'title': 'Public source',
                'text': 'Публичное описание, которое было получено для проверки источника. ' + quote + ' Конец документа.',
                'fetched_at': self.clock.isoformat(), 'published_at': None}

    def step(self, cli=None, fetcher=None):
        return dr.run_step('test-job', 'Исследовать ПО и B2C без контактов с людьми.', self.path, self.cards,
                           cli=cli or FakeCli(), fetcher=fetcher or self.fetcher)

    def query(self, sql, args=()):
        store = dr.Research(self.path, self.cards)
        try:
            return store.rows(sql, args)
        finally:
            store.close()

    def prepared(self, cli=None):
        cli = cli or FakeCli()
        self.step(cli)
        result = self.step(cli)
        while result['stage'] == 'fetch':
            result = self.step(cli)
        sources = {row['url']: row['id'] for row in self.query('SELECT * FROM demand_sources')}
        cli.analysis = {'hypotheses': [proposal(sources['https://buyers.example/thread/1'], sources['https://vendor.test/prices'], buyer_two=sources['https://operators.test/question/2'])], 'gaps': []}
        return cli

    def test_checkpoint_bounded_stages_and_public_evidence(self):
        cli = FakeCli()
        discovery = self.step(cli)
        self.assertEqual(discovery['stage'], 'fetch')
        self.assertEqual(len(cli.calls), 1)
        fetched = self.step(cli)
        self.assertEqual(fetched['stage'], 'analyze')
        self.assertEqual(len(cli.calls), 1)
        sources = self.query('SELECT id,url FROM demand_sources')
        cli.analysis = {'hypotheses': [proposal(sources[0]['id'], sources[1]['id'], buyer_two=sources[2]['id'])], 'gaps': []}
        # Input order is the discovery order and therefore deterministic.
        result = self.step(cli)
        self.assertTrue(result['ok'])
        self.assertFalse(result['has_more'])
        self.assertTrue(result['notify'])
        self.assertEqual(result['hypotheses'][0]['state'], 'desk_supported')
        self.assertEqual(len(cli.calls), 2)
        card = next(self.cards.glob('*.md')).read_text(encoding='utf-8')
        self.assertIn('Гипотеза спроса', card)
        self.assertIn('https://buyers.example/thread/1', card)
        self.assertIn('комиссию', card)
        self.assertIn('Следующая проверка', card)

    def test_repeat_finished_hour_returns_same_notification_without_model(self):
        cli = self.prepared()
        first = self.step(cli)
        again = self.step(cli)
        self.assertTrue(again['cached'])
        self.assertEqual(again['text'], first['text'])
        self.assertEqual(again['notify'], first['notify'])
        self.assertEqual(len(cli.calls), 2)
        self.assertEqual(len(self.query('SELECT * FROM demand_hypotheses')), 1)

    def test_scheduler_cycle_anchor_preserves_final_notice_across_hour(self):
        cli = self.prepared()
        first = self.step(cli)
        self.clock += timedelta(hours=1)
        again = dr.run_step('test-job', 'Исследовать ПО и B2C без контактов с людьми.', self.path, self.cards,
                            cli=cli, fetcher=self.fetcher, cycle_id=first['cycle_id'])
        self.assertTrue(again['cached'])
        self.assertEqual(again['text'], first['text'])
        self.assertEqual(again['notify'], first['notify'])
        self.assertEqual(len(cli.calls), 2)
        self.assertEqual(len(self.query('SELECT * FROM demand_cycles')), 1)
        with self.assertRaises(ValueError):
            dr.run_step('wrong-job', 'task', self.path, self.cards, cli=cli, cycle_id=first['cycle_id'])

    def test_legacy_completed_cycle_migration_preserves_analysis_and_result(self):
        cli = self.prepared()
        first = self.step(cli)
        import sqlite3
        db = sqlite3.connect(self.path)
        try:
            db.execute('ALTER TABLE demand_candidates DROP COLUMN analysis_state')
            db.execute('ALTER TABLE demand_candidates DROP COLUMN batch_index')
            db.execute('DROP TABLE demand_batches')
            db.commit()
        finally:
            db.close()
        view = dr.execute({'action': 'status'}, self.path, self.cards)
        self.assertEqual(view['cycles'][0]['coverage']['analyzed'], 3)
        self.assertEqual(view['cycles'][0]['coverage']['analysis_pending'], 0)
        self.assertEqual(view['cycles'][0]['coverage']['batches'], [])
        again = self.step(cli)
        self.assertEqual(again['text'], first['text'])
        self.assertTrue(again['cached'])

    def test_foreign_branch_cannot_create_domestic_demand_or_relabel_existing_hypothesis(self):
        cli = self.prepared()
        store = dr.Research(self.path, self.cards)
        try:
            cycle = store.rows('SELECT * FROM demand_cycles')[0]
            plan = json.loads(cycle['plan'])
            plan['question'] = {'geography': 'international', 'region': 'США'}
            foreign = {**cycle, 'plan': json.dumps(plan)}
            sources = {row['id']: row for row in store.rows('SELECT * FROM demand_sources')}
            item = cli.analysis['hypotheses'][0]
            with store.db:
                self.assertIsNone(dr._save_hypothesis(store, foreign, item, sources))
        finally:
            store.close()
        original = self.step(cli)
        identity = original['hypotheses'][0]['id']
        store = dr.Research(self.path, self.cards)
        try:
            item['existing_id'] = identity
            item['geography'] = 'США'
            item['buyer'] = 'Американские клиенты'
            item['problem'] = 'Совсем другая зарубежная задача'
            with store.db:
                dr._save_hypothesis(store, foreign, item, sources)
            payload = json.loads(store.rows('SELECT payload FROM demand_hypotheses')[0]['payload'])
            self.assertEqual(payload['geography'], 'Не установлена')
            self.assertEqual(payload['buyer'], 'Небольшие интернет-магазины')
            self.assertEqual(payload['problem'], 'Ежедневное ручное копирование заказов')
            self.assertTrue(any(row['kind'] == 'foreign_context' for row in store.evidence(identity)))
        finally:
            store.close()

    def test_new_hour_keeps_same_hypothesis_and_evidence_without_reannouncement(self):
        cli = self.prepared()
        self.step(cli)
        # Rotation returns to software on its fourth cycle. Advance sequence to
        # the software follow-up without touching any production schedule.
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE demand_cycles SET sequence=2')
        db.close()
        self.clock += timedelta(hours=1)
        self.step(cli)
        cycle = self.query('SELECT * FROM demand_cycles ORDER BY sequence DESC')[0]
        plan = json.loads(cycle['plan'])
        self.assertIsNotNone(plan['revisit'])
        self.assertIn('Бесплатный продукт', plan['revisit']['test']['fail_condition'])
        self.step(cli)
        result = self.step(cli)
        self.assertFalse(result['notify'])
        self.assertEqual(len(self.query('SELECT * FROM demand_hypotheses')), 1)
        self.assertEqual(len(self.query('SELECT * FROM demand_evidence')), 3)

    def test_missing_or_invented_quotes_never_become_evidence(self):
        cli = self.prepared()
        cli.analysis['hypotheses'][0]['evidence'][0]['quote'] = 'Покупатель готов платить сто тысяч рублей каждый месяц.'
        cli.analysis['hypotheses'][0]['evidence'][1]['source_id'] = 'invented'
        cli.analysis['hypotheses'][0]['evidence'][2]['quote'] = 'Ещё один придуманный фрагмент, которого не было на странице.'
        result = self.step(cli)
        self.assertFalse(result['notify'])
        self.assertEqual(result['status'], 'completed_with_gaps')
        self.assertEqual(result['coverage']['rejected_unverified_hypotheses'], 1)
        self.assertEqual(self.query('SELECT * FROM demand_hypotheses'), [])

    def test_one_publisher_or_syndication_cannot_satisfy_independence_gate(self):
        cli = self.prepared()
        for item in cli.analysis['hypotheses'][0]['evidence']:
            item['origin_group'] = 'same-original'
        result = self.step(cli)
        self.assertEqual(result['hypotheses'][0]['state'], 'signal')
        self.assertEqual(result['hypotheses'][0]['independent_origins'], 1)
        self.assertFalse(result['notify'])

    def test_fetch_failures_are_coverage_gaps_not_absence_of_demand(self):
        cli = FakeCli()
        self.step(cli)
        def partial(url):
            return {'ok': False, 'error': 'http_403'} if 'vendor.' in url else self.fetcher(url)
        self.step(cli, partial)
        source = self.query('SELECT id FROM demand_sources WHERE url=?', ('https://buyers.example/thread/1',))[0]['id']
        item = proposal(source, 'not-fetched')
        item['evidence'] = item['evidence'][:1]
        cli.analysis = {'hypotheses': [item], 'gaps': ['Коммерческая альтернатива не прочитана.']}
        result = self.step(cli)
        self.assertEqual(result['status'], 'completed_with_gaps')
        self.assertEqual(result['coverage']['failed'], 1)
        self.assertFalse(result['notify'])
        self.assertEqual(result['hypotheses'][0]['state'], 'signal')

    def test_no_readable_pages_is_failure(self):
        self.step()
        result = self.step(fetcher=lambda url: {'ok': False, 'error': 'captcha'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['coverage']['read'], 0)
        self.assertIn('отсутствие спроса не установлено', result['error'])

    def test_discovery_requires_actual_native_web_call_and_stops_after_three_failures(self):
        cli = FakeCli(events=False)
        for _ in range(3):
            result = self.step(cli)
            self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'failed')
        cached = self.step(cli)
        self.assertTrue(cached['cached'])
        self.assertEqual(len(cli.calls), 3)
        self.assertEqual(self.fetch_calls, [])

    def test_duplicate_urls_and_maximum_three_fetches_per_step(self):
        cli = FakeCli()
        cli.discovery['candidates'] = [
            {'url': f'https://buyers.example/thread/{i}', 'purpose': 'support', 'source_type': 'request'}
            for i in range(1, 6)] + [{'url': 'https://buyers.example/thread/1?utm_source=copy', 'purpose': 'support', 'source_type': 'request'}]
        self.step(cli)
        first = self.step(cli)
        self.assertEqual(first['stage'], 'fetch')
        self.assertEqual(len(self.fetch_calls), 3)
        self.assertEqual(first['coverage']['planned'], 5)
        second = self.step(cli)
        self.assertEqual(second['stage'], 'analyze')
        self.assertEqual(len(self.fetch_calls), 5)

    def test_export_failure_keeps_final_notification_for_recovery(self):
        cli = self.prepared()
        with patch.object(dr.Research, 'export_cards', side_effect=OSError('disk problem')):
            first = self.step(cli)
        self.assertEqual(first['status'], 'cards_pending')
        self.assertFalse(first['ok'])
        self.clock += timedelta(hours=1)
        restored = self.step(cli)
        self.assertTrue(restored['cached'])
        self.assertTrue(restored['notify'])
        self.assertEqual(len(cli.calls), 2)
        self.assertEqual(len(self.query('SELECT * FROM demand_cycles')), 1)
        self.assertEqual(len(list(self.cards.glob('*.md'))), 1)

    def test_counterevidence_is_retained_and_weakening_is_reported(self):
        cli = self.prepared()
        first = self.step(cli)
        identity = first['hypotheses'][0]['id']
        self.clock += timedelta(hours=1)
        cli.discovery['candidates'].append({'url': 'https://counter.example/free', 'purpose': 'counter', 'source_type': 'offer'})
        self.step(cli)
        result = self.step(cli)
        while result['stage'] == 'fetch':
            result = self.step(cli)
        sources = {row['url']: row['id'] for row in self.query('SELECT * FROM demand_sources')}
        item = proposal(sources['https://buyers.example/thread/1'], sources['https://vendor.test/prices'], sources['https://counter.example/free'], buyer_two=sources['https://operators.test/question/2'])
        item['existing_id'] = identity
        item['recommendation'] = 'reject'
        item['test_result'] = {'verdict': 'contradicted', 'basis': 'Бесплатный заменитель поддерживает необходимый перенос.',
                               'evidence_source_ids': [sources['https://counter.example/free']]}
        item['counterarguments'] = ['Бесплатный заменитель поддерживает необходимый перенос.']
        cli.analysis = {'hypotheses': [item], 'gaps': []}
        result = self.step(cli)
        while result.get('has_more'):
            result = self.step(cli)
        self.assertEqual(result['hypotheses'][0]['state'], 'weakened')
        self.assertTrue(result['notify'])
        saved = json.loads(self.query('SELECT payload FROM demand_hypotheses')[0]['payload'])
        self.assertEqual(len(saved['counterarguments']), 2)
        self.assertEqual(saved['test_result']['verdict'], 'contradicted')
        self.assertIn('Бесплатный продукт', saved['previous_test']['fail_condition'])
        self.assertEqual(len(self.query('SELECT * FROM demand_evidence')), 4)

    def test_read_tool_reports_records_without_running_research(self):
        cli = self.prepared()
        self.step(cli)
        for action in ['status', 'report', 'hypotheses', 'sources']:
            result = dr.execute({'action': action}, self.path, self.cards)
            self.assertTrue(result['ok'])
        with self.assertRaises(ValueError):
            dr.execute({'action': 'send'}, self.path, self.cards)
        with self.assertRaises(ValueError):
            dr.execute({'action': 'sources', 'limit': 1000}, self.path, self.cards)
        self.assertEqual(len(cli.calls), 2)

    def test_vendor_problem_claim_is_not_direct_demand(self):
        cli = self.prepared()
        cli.analysis['hypotheses'][0]['evidence'][0]['source_type'] = 'offer'
        cli.analysis['hypotheses'][0]['evidence'][1]['source_type'] = 'statistics'
        cli.analysis['hypotheses'][0]['evidence'][2]['source_type'] = 'offer'
        result = self.step(cli)
        self.assertEqual(result['hypotheses'], [])
        self.assertEqual(result['status'], 'completed_with_gaps')
        self.assertFalse(result['notify'])

    def test_one_buyer_and_multiple_vendors_remain_a_signal(self):
        cli = self.prepared()
        item = cli.analysis['hypotheses'][0]
        item['evidence'][2]['source_type'] = 'offer'
        item['evidence'][2]['kind'] = 'alternative'
        result = self.step(cli)
        self.assertEqual(result['hypotheses'][0]['state'], 'signal')
        self.assertEqual(result['hypotheses'][0]['direct_problem_origins'], 1)
        self.assertEqual(result['hypotheses'][0]['independent_origins'], 3)
        self.assertIs(result['hypotheses'][0]['notify'], False)
        self.assertIs(result['notify'], False)

    def test_monitor_rewording_without_new_counter_does_not_downgrade(self):
        cli = self.prepared()
        original = self.step(cli)
        identity = original['hypotheses'][0]['id']
        self.clock += timedelta(hours=1)
        self.step(cli)
        self.step(cli)
        cli.analysis['hypotheses'][0]['existing_id'] = identity
        cli.analysis['hypotheses'][0]['recommendation'] = 'monitor'
        result = self.step(cli)
        self.assertEqual(result['hypotheses'][0]['state'], 'desk_supported')
        self.assertFalse(result['notify'])

    def test_model_link_without_native_result_provenance_is_rejected(self):
        class InventedCli(FakeCli):
            def structured(self, prompt, schema):
                value = super().structured(prompt, schema)
                self.last_web_events = [{'type': 'web_search', 'query': 'query', 'results': []}]
                return value
        result = self.step(InventedCli())
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_category'], 'search_provenance_missing')
        self.assertEqual(self.fetch_calls, [])

    def test_unsupported_economics_and_conversion_ratings_remain_unknown(self):
        cli = self.prepared()
        item = cli.analysis['hypotheses'][0]
        sid = item['evidence'][0]['source_id']
        item['rubric']['economics'] = {'rating': 'high', 'reason': 'Большая прибыль.', 'evidence_source_ids': [sid]}
        item['rubric']['conversion_evidence'] = {'rating': 'high', 'reason': 'Высокая конверсия.', 'evidence_source_ids': [sid]}
        self.step(cli)
        result = dr.execute({'action': 'report'}, self.path, self.cards)
        self.assertEqual(result['comparison'][0]['rubric']['economics']['rating'], 'unknown')
        self.assertEqual(result['comparison'][0]['rubric']['conversion_evidence']['rating'], 'unknown')


if __name__ == '__main__':
    unittest.main()


class DeepCli:
    """Model double exercising the real multi-batch frontier and current pages."""
    def __init__(self, counts=(12, 12, 12, 12), fail_batch=None, empty_last_analysis=False):
        self.counts = counts
        self.fail_batch = fail_batch
        self.empty_last_analysis = empty_last_analysis
        self.discovery_calls = 0
        self.analysis_sizes = []
        self.last_web_events = []
        self.urls = []
        for batch, count in enumerate(counts):
            self.urls.append([f'https://source{batch * 12 + index}.test/post/{batch * 12 + index}' for index in range(count)])

    def structured(self, prompt, schema):
        if 'candidates' in schema['properties']:
            import re
            batch = int(re.search(r'Текущая партия (\d+)/', prompt).group(1)) - 1
            self.discovery_calls += 1
            if batch == self.fail_batch:
                raise RuntimeError('test network issue')
            candidates = [{'url': url, 'purpose': 'support' if batch == 0 else 'counter' if batch == 3 else 'alternative',
                           'source_type': 'request' if batch == 0 else 'offer'} for url in self.urls[batch]]
            self.last_web_events = [{'type': 'web_search', 'query': f'query-{batch}',
                                     'results': [{'url': url, 'title': 'Source'} for url in self.urls[batch]]}]
            return {'queries': [f'query-{batch}'], 'candidates': candidates, 'limitations': []}
        pages = json.loads(prompt.split('\nПрочитанные источники: ', 1)[1])
        self.analysis_sizes.append(len(pages))
        if self.empty_last_analysis and all('/post/36' in page['url'] for page in pages):
            return {'hypotheses': [], 'gaps': [], 'followups': [], 'branch_answer': {'verdict': 'inconclusive', 'basis': 'Нет активного вопроса.', 'source_ids': [], 'evidence': []}}
        item = proposal('ignored', 'ignored')
        item['evidence'] = []
        for page in pages:
            index = int(page['url'].rsplit('/', 1)[1])
            quote = deep_quote(index)
            item['evidence'].append({'source_id': page['id'], 'quote': quote, 'interpretation': 'Свидетельство из текущего источника.',
                                     'kind': 'problem' if index < 3 else 'alternative',
                                     'source_type': ['request', 'review', 'discussion'][index] if index < 3 else 'offer', 'origin_group': ''})
        return {'hypotheses': [item], 'gaps': [], 'followups': [], 'branch_answer': {'verdict': 'inconclusive', 'basis': 'Нет активного вопроса.', 'source_ids': [], 'evidence': []}}


def deep_quote(index):
    first = ['Менеджер вручную сводит заявки из трёх площадок и регулярно пропускает новые позиции.',
             'Я теряю вечер на учёт остатков; готового соединения с нашей таблицей не нашёл.',
             'Заказ завис из-за ошибок синхронизации, приходится резервировать товары в блокноте.']
    return first[index] if index < 3 else f'Поставщик {index} предлагает интеграцию заказов, но конкретные ограничения требуют отдельной проверки.'


class DeepDemand(unittest.TestCase):
    def setUp(self):
        self.legacy_patch=patch.object(dr,'AGENT_RESEARCH_ENABLED',False)
        self.legacy_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'deep.sqlite3'
        self.cards = Path(self.temp.name) / 'cards'
        self.url_patch = patch.object(dr, '_canonical', side_effect=canonical)
        self.url_patch.start()
        self.fetch_count = 0

    def tearDown(self):
        self.legacy_patch.stop()
        self.url_patch.stop()
        self.temp.cleanup()

    def fetcher(self, url):
        self.fetch_count += 1
        index = int(url.rsplit('/', 1)[1])
        return {'ok': True, 'url': url, 'text': 'Исходное публичное описание процесса и обстоятельств покупателя. ' + deep_quote(index),
                'title': f'Source {index}', 'fetched_at': STAMP.isoformat(), 'published_at': None}

    def step(self, cli):
        before = self.fetch_count
        result = dr.run_step('deep-job', 'Исследовать публичный спрос.', self.path, self.cards, cli, self.fetcher)
        self.assertLessEqual(self.fetch_count - before, 3)
        return result

    def finish(self, cli):
        states = []
        for _ in range(100):
            result = self.step(cli)
            states.append(result)
            if not result['has_more']:
                return result, states
            self.assertIs(result['notify'], False)
        self.fail('frontier did not terminate')

    def test_four_batches_48_urls_and_analysis_three_pages_with_one_final_notice(self):
        cli = DeepCli()
        result, steps = self.finish(cli)
        self.assertTrue(result['notify'])
        self.assertEqual(result['coverage']['planned'], 48)
        self.assertEqual(result['coverage']['read'], 48)
        self.assertEqual(result['coverage']['unique_analyzed_pages'], 48)
        self.assertEqual(result['coverage']['search_batches_completed'], 4)
        self.assertEqual(cli.discovery_calls, 4)
        self.assertTrue(all(size <= 3 for size in cli.analysis_sizes))
        self.assertEqual(result['hypotheses'][0]['direct_story_origins'], 3)
        self.assertEqual(sum(bool(item['notify']) for item in steps), 1)
        cached = self.step(cli)
        self.assertTrue(cached['cached'])
        self.assertEqual(cached['text'], result['text'])

    def test_chunk_notes_do_not_become_stale_final_coverage_errors(self):
        class NotesCli(DeepCli):
            def __init__(self):
                super().__init__()
                self.progress_snapshots=[]

            def structured(self, prompt, schema):
                value=super().structured(prompt,schema)
                if 'candidates' not in schema['properties']:
                    snapshot=json.loads(prompt.split('\nФактический прогресс всего цикла перед текущим анализом: ',1)[1].split('\nСуществующие уточняющие вопросы',1)[0])
                    self.progress_snapshots.append(snapshot)
                    value['gaps']=['Прочитаны только 3 страницы из минимальных 10.',
                                   'Контрпоиск не завершён в текущем фрагменте.',
                                   'В текущем источнике не указана дата публикации.']
                return value
        cli=NotesCli()
        result,_=self.finish(cli)
        self.assertEqual(result['coverage']['read'],48)
        self.assertEqual(result['coverage']['analyzed'],48)
        self.assertTrue(result['coverage']['counter_search_completed'])
        self.assertEqual(result['coverage']['analysis_gaps'],[])
        self.assertEqual(result['status'],'completed')
        notes=result['coverage']['analysis_notes']
        self.assertTrue(notes)
        self.assertTrue(all(note['source_ids'] and note['event_id'] for note in notes))
        self.assertTrue(all(note['scope']=='historical_chunk_observation' for note in notes))
        self.assertIn('не указана дата',notes[0]['notes'][2])
        self.assertIn('могло быть уточнено',notes[0]['resolution'])
        self.assertEqual(cli.progress_snapshots[-1]['read'],48)
        self.assertEqual(cli.progress_snapshots[-1]['analyzed'],45)
        self.assertNotIn('недоступна',result['text'])

    def test_final_source_failures_remain_deterministic_gaps(self):
        original=self.fetcher
        def failing(url):
            return {'ok':False,'error_category':'http_error'} if url.endswith('/post/5') else original(url)
        self.fetcher=failing
        result,_=self.finish(DeepCli())
        self.assertEqual(result['coverage']['failed'],1)
        self.assertEqual(result['coverage']['read'],47)
        self.assertEqual(result['status'],'completed_with_gaps')
        self.assertTrue(any('Не удалось прочитать источников: 1' in gap for gap in result['coverage']['analysis_gaps']))
        self.assertEqual(result['coverage']['gaps'][0]['error'],'http_error')
        report=dr.execute({'action':'report','id':result['cycle_id']},self.path,self.cards)['cycles'][0]
        self.assertEqual(report['coverage']['failed'],1)
        self.assertTrue(any('Не удалось прочитать источников: 1' in gap for gap in report['coverage']['analysis_gaps']))
        self.assertEqual(report['state'],'completed_with_gaps')

    def test_old_report_coverage_is_normalized_without_rewriting_history_or_delivery(self):
        result,_=self.finish(DeepCli())
        stale=['Прочитаны только 3 страницы из минимальных 10.', 'Контрпоиск не завершён.']
        result['coverage'].pop('analysis_notes',None)
        result['coverage']['analysis_gaps']=stale
        result['status']='completed_with_gaps'
        result['text']='Исторический текст: часть страниц недоступна.'
        saved=dr.dumps(result)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE demand_cycles SET state='completed_with_gaps',result=? WHERE id=?",(saved,result['cycle_id']))
            event_id,detail=db.execute("SELECT id,detail FROM demand_events WHERE cycle_id=? AND event='chunk_completed' ORDER BY id LIMIT 1",(result['cycle_id'],)).fetchone()
            detail=json.loads(detail)
            detail['gaps']=stale+['У текущей страницы неизвестна дата публикации.']
            db.execute('UPDATE demand_events SET detail=? WHERE id=?',(dr.dumps(detail),event_id))
        db.close()
        report=dr.execute({'action':'report','id':result['cycle_id']},self.path,self.cards)['cycles'][0]
        status=dr.execute({'action':'status'},self.path,self.cards)['cycles'][0]
        for view in (report,status):
            self.assertEqual(view['coverage']['read'],48)
            self.assertEqual(view['coverage']['analysis_gaps'],[])
            self.assertEqual(view['state'],'completed')
            self.assertEqual(view['recorded_state'],'completed_with_gaps')
            self.assertTrue(any(note['source_ids'] and 'неизвестна дата' in ' '.join(note['notes']) for note in view['coverage']['analysis_notes']))
        self.assertEqual(report['result']['coverage']['analysis_gaps'],[])
        self.assertEqual(report['result']['recorded_status'],'completed_with_gaps')
        self.assertEqual(report['result']['text_scope'],'historical_notification_not_recomputed')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT result FROM demand_cycles WHERE id=?',(result['cycle_id'],)).fetchone()[0],saved)
        db.close()
        cached=self.step(DeepCli())
        self.assertEqual(cached['text'],result['text'])
        self.assertEqual(cached['coverage']['analysis_gaps'],stale)

    def test_tenth_page_can_qualify_earlier_evidence_without_another_hypothesis_change(self):
        cli = DeepCli(counts=(3, 3, 3, 1), empty_last_analysis=True)
        result, steps = self.finish(cli)
        self.assertEqual(result['coverage']['unique_read_pages'], 10)
        self.assertTrue(result['notify'])
        self.assertEqual(result['status'], 'completed_with_gaps')
        self.assertTrue(any(item['coverage']['read'] == 9 and not item['notify'] for item in steps))

    def test_nine_pages_or_failed_counter_batch_cannot_qualify(self):
        cli = DeepCli(counts=(3, 3, 3, 0))
        result, _ = self.finish(cli)
        self.assertEqual(result['coverage']['unique_read_pages'], 9)
        self.assertTrue(result['coverage']['counter_search_completed'])
        self.assertFalse(result['notify'])
        self.assertEqual(result['hypotheses'][0]['state'], 'signal')

    def test_failed_counter_batch_keeps_earlier_evidence_but_no_priority_notice(self):
        cli = DeepCli(fail_batch=3)
        result, _ = self.finish(cli)
        self.assertEqual(result['coverage']['read'], 36)
        self.assertFalse(result['coverage']['counter_search_completed'])
        self.assertFalse(result['notify'])
        self.assertEqual(result['hypotheses'][0]['direct_story_origins'], 3)

    def test_busy_model_yields_without_counting_failure(self):
        class Busy:
            def structured(self, *args):
                raise RuntimeError('Research authorization is busy')
        result = self.step(Busy())
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'waiting_model')
        self.assertEqual(result['retry_after_seconds'], 60)
        store = dr.Research(self.path, self.cards)
        try:
            self.assertEqual(store.rows('SELECT failures FROM demand_cycles')[0]['failures'], 0)
        finally:
            store.close()

    def test_pending_software_questions_do_not_starve_fresh_b2c_and_sheds(self):
        branch = {'id': 'fake-queue-item', 'track': 'software', 'question': 'Уточнить ПО', 'query': 'уточнение ПО',
                  'geography': 'domestic', 'region': 'Россия', 'subject': '', 'pass_condition': 'Есть данные', 'fail_condition': 'Есть опровержение'}
        store = dr.Research(self.path, self.cards)
        selected = []
        try:
            with patch.object(dr.questions, 'pick', return_value=branch), patch.object(dr.questions, 'start', return_value=branch):
                for index in range(14):
                    with patch.object(dr, 'now', return_value=(STAMP + timedelta(hours=index)).isoformat()):
                        cycle = store.cycle('fairness-job', 'task')
                        selected.append((cycle['track'], bool(json.loads(cycle['plan'])['question'])))
                        with store.db:
                            store.db.execute("UPDATE demand_cycles SET state='completed',stage='done',result='{}' WHERE id=?", (cycle['id'],))
            self.assertEqual(selected[2], ('b2c', False))
            self.assertEqual(selected[4], ('hozbloki', False))
            self.assertEqual(selected[8], ('b2c', False))
            self.assertFalse(selected[12][1])
            self.assertFalse(selected[13][1])
            self.assertTrue(selected[1][1])
        finally:
            store.close()

    def test_grounded_followup_executes_next_cycle_and_records_quote_verified_answer(self):
        cli = QuestionCli()
        with patch.object(dr, 'now', return_value=STAMP.isoformat()):
            first, _ = self.finish(cli)
        frontier = dr.execute({'action': 'status'}, self.path, self.cards)['question_frontier']
        self.assertEqual(frontier['counts'].get('pending'), 1)
        question_id = frontier['questions'][0]['id']
        self.assertIsNone(frontier['questions'][0]['active_cycle_id'])
        with patch.object(dr, 'now', return_value=(STAMP + timedelta(hours=1)).isoformat()):
            second, _ = self.finish(cli)
        self.assertNotEqual(first['cycle_id'], second['cycle_id'])
        self.assertEqual(second['question_result']['id'], question_id)
        self.assertEqual(second['question_result']['state'], 'supported')
        self.assertEqual(second['question_result']['active_cycle_id'], second['cycle_id'])
        self.assertTrue(second['question_result']['result']['source_ids'])
        self.assertIn('Проверенные выдержки', second['question_result']['result']['basis'])
        self.assertEqual(cli.discovery_calls, 8)
        report = dr.execute({'action': 'report', 'id': second['cycle_id']}, self.path, self.cards)
        self.assertEqual(report['cycles'][0]['plan']['question']['id'], question_id)
        self.assertEqual(report['cycles'][0]['track'], 'software')

    def test_branch_answer_without_actual_quote_is_inconclusive(self):
        cli = QuestionCli(invented_quote=True)
        with patch.object(dr, 'now', return_value=STAMP.isoformat()):
            self.finish(cli)
        with patch.object(dr, 'now', return_value=(STAMP + timedelta(hours=1)).isoformat()):
            second, _ = self.finish(cli)
        self.assertEqual(second['question_result']['state'], 'parked')
        self.assertEqual(second['question_result']['result']['verdict'], 'inconclusive')
        self.assertEqual(second['question_result']['result']['source_ids'], [])
        self.assertTrue(any('остался без однозначного ответа' in gap for gap in second['coverage']['analysis_gaps']))
        self.assertNotIn('недоступные или частично прочитанные',second['question_result']['result']['basis'])
        report=dr.execute({'action':'report','id':second['cycle_id']},self.path,self.cards)['cycles'][0]
        self.assertTrue(any('остался без однозначного ответа' in gap for gap in report['coverage']['analysis_gaps']))


class AgentCli(DeepCli):
    def __init__(self, child_request=True, defer=False, fail_stage=None, final_child=False):
        super().__init__()
        self.child_request=child_request
        self.defer=defer
        self.fail_stage=fail_stage
        self.final_child=final_child
        self.prompts=[]
        self.reasoning_effort='low'
        self.model='configured-model'
        self.service_tier='fast'

    def structured(self,prompt,schema):
        from tests.test_demand_agents import research_plan,child
        self.prompts.append(prompt)
        properties=schema['properties']
        if 'scope_decisions' in properties:
            return {'scope_id': properties['scope_id']['enum'][0],
                    'scope_decisions': [{'key': key, 'relationship': 'same_problem',
                                         'reason': 'Изолированная фикстура относится к тому же рабочему процессу.'}
                                        for key in properties['scope_decisions']['items']['properties']['key']['enum']]}
        stage=('agent_plan' if 'researchers' in properties else 'agent_report' if 'findings' in properties else
               'agent_synthesis' if 'decisions' in properties and 'conflicts' in properties else
               'agent_review' if 'decisions' in properties else 'discovery' if 'candidates' in properties else 'analyze')
        self.last_execution={'model':self.model,'reasoning_effort':self.reasoning_effort,'service_tier':self.service_tier}
        if self.fail_stage==stage:
            raise RuntimeError('test model timeout')
        if stage=='agent_plan':
            return research_plan()
        if stage=='discovery':
            import re
            batch=int(re.search(r'Партия источников №(\d+)',prompt).group(1))-1
            urls=[f'https://source{batch*12+i}.test/post/{batch*12+i}' for i in range(4)]
            self.last_web_events=[{'type':'web_search','query':'actual query','results':[{'url':url,'title':'Source'} for url in urls]}]
            self.discovery_calls+=1
            return {'queries':['actual query'],'candidates':[{'url':url,'purpose':'counter' if '"role": "critic"' in prompt else 'support','source_type':'request'} for url in urls],'limitations':[]}
        if stage=='agent_report':
            task=json.loads(prompt.split('Твоё поручение: ',1)[1].split('\nПроверенные свидетельства:',1)[0])
            evidence=json.loads(prompt.split('\nПроверенные свидетельства: ',1)[1].split('\nРезультаты частей',1)[0])
            requests=[]
            if self.child_request and task['role']=='buyer_problem' and task['depth']==0 and evidence:
                quote=evidence[0]['evidence'][0]
                request=child('grounded-child')
                request.update(source_ids=[quote['source_id']],trigger_quote=quote['quote'])
                requests=[request]
            return {'summary':'Собственные наблюдения исследователя требуют дальнейшей проверки.',
                    'findings':[{'hypothesis_id':item['id'],'assessment':'Интерпретация проверенных выдержек.', 'evidence_ids':[e['id'] for e in item['evidence']]} for item in evidence],
                    'conflicts':[],'limitations':[],'child_requests':requests,
                    'branch_reason':'Рассмотрены неизвестные ограничения интеграции; выбран только вопрос, способный изменить решение.'}
        if stage=='agent_review':
            context=json.JSONDecoder().raw_decode(prompt.split('\nЗадача и отчёт: ',1)[1])[0]
            return {'summary':'Запрос исследователя рассмотрен по исходной цели.',
                    'decisions':[{'key':item['key'],'decision':'defer' if self.defer else 'approve','reason':'Цитата задаёт проверяемый вопрос по ограничению интеграции.'} for item in context['report']['child_requests']]}
        if stage=='agent_synthesis':
            evidence=json.loads(prompt.split('\nСвидетельства: ',1)[1].split('\nФактический охват:',1)[0])
            requests=[]
            if self.final_child and 'проверки: 1/' in prompt and evidence:
                quote=evidence[0]['evidence'][0]
                request=child('final-conflict-child','critic')
                request.update(source_ids=[quote['source_id']],trigger_quote=quote['quote'])
                requests=[request]
            return {'summary':'Сопоставлены отдельные отчёты и проверенные цитаты.',
                    'decisions':[{'hypothesis_id':item['id'],'disposition':'investigate','basis':'Нужна следующая проверка, рынок не подтверждён.','evidence_ids':[e['id'] for e in item['evidence']]} for item in evidence],
                    'conflicts':[],'limitations':[],'child_requests':requests,
                    'branch_reason':'Сопоставлены существенные неизвестные; дополнительные вопросы предлагаются только при изменении решения.'}
        value=super().structured(prompt,schema)
        if '"role": "foreign"' in prompt:
            for hypothesis in value['hypotheses']:
                for evidence in hypothesis['evidence']:
                    evidence.update(kind='problem',source_type='complaint')
        return value


class AgentDemand(unittest.TestCase):
    # Reuse only isolated filesystem/HTTP helpers, not legacy test scenarios.
    tearDown=DeepDemand.tearDown
    fetcher=DeepDemand.fetcher
    step=DeepDemand.step

    def setUp(self):
        # Preserve the pre-coverage orchestration regression contract. The new
        # policy is exercised separately by test_demand_coverage_integration.
        coverage_patch = patch.object(dr, 'EVIDENCE_COVERAGE_ENABLED', False)
        coverage_patch.start()
        self.addCleanup(coverage_patch.stop)
        DeepDemand.setUp(self)
        self.legacy_patch.stop()
        self.legacy_patch=patch.object(dr,'AGENT_RESEARCH_ENABLED',True)
        self.legacy_patch.start()

    def finish(self,cli):
        states=[]
        for _ in range(180):
            value=self.step(cli);states.append(value)
            if not value['has_more']:return value,states
            self.assertIs(value['notify'],False)
        self.fail('agent tree did not terminate: '+str(states[-1]))

    def prepare_review(self,child_request=False):
        cli=AgentCli(child_request=child_request)
        for _ in range(30):
            result=self.step(cli)
            if result['stage']=='agent_review':return result,cli
        self.fail('review checkpoint not reached')

    def test_empty_review_still_calls_orchestrator_and_constrains_output(self):
        checkpoint,cli=self.prepare_review()
        schemas=[]
        original=cli.structured
        def observe(prompt,schema):
            schemas.append(schema)
            self.assertIn('decisions обязан быть пустым массивом []',prompt)
            self.assertIn('Единственный разрешённый список ключей decisions: []',prompt)
            return original(prompt,schema)
        cli.structured=observe
        result=self.step(cli)
        self.assertEqual(len(schemas),1)
        self.assertEqual(schemas[0]['properties']['decisions']['minItems'],0)
        self.assertEqual(schemas[0]['properties']['decisions']['maxItems'],0)
        with sqlite3.connect(self.path) as db:
            details=[json.loads(row[0]) for row in db.execute("SELECT detail FROM demand_events WHERE event='review_completed'")]
            children=db.execute('SELECT count(*) FROM demand_agent_tasks WHERE parent_task_id IS NOT NULL').fetchone()[0]
        db.close()
        self.assertEqual(details[0]['review']['decisions'],[])
        self.assertTrue(details[0]['review']['summary'])
        self.assertEqual(children,0)
        self.assertEqual(result['stage'],'discovery')

    def test_role_report_receives_complete_short_source_context_json(self):
        original=self.fetcher
        def short_page(url):
            value=original(url)
            value['text']=value['text']+'x'*(2048-len(value['text']))
            return value
        self.fetcher=short_page
        _checkpoint,cli=self.prepare_review()
        prompt=cli.prompts[-1]
        supplied=json.loads(prompt.split('\nДоступные source_ids и выдержки: ',1)[1])
        self.assertEqual(len(supplied),4)
        self.assertTrue(all(row['stored_characters']==2048 and row['provided_characters']==2048 and
                            not row['truncated_for_report'] and len(row['text'])==2048 for row in supplied))
        self.assertIn('НЕ недоступность сайта',prompt)

    def test_review_retry_has_safe_diagnostics_feedback_and_no_duplicate_side_effects(self):
        checkpoint,cli=self.prepare_review()
        class Invalid:
            def structured(self,prompt,schema):
                return {'summary':'PRIVATE_SUMMARY_932','decisions':[{'key':'PRIVATE_TOKEN_932','decision':'approve','reason':'PRIVATE_REASON_932'}]}
        rejected=self.step(Invalid())
        self.assertEqual(rejected['error_category'],'agent_review_extra_keys')
        self.assertTrue(rejected['has_more'])
        self.assertFalse(rejected['notify'])
        with sqlite3.connect(self.path) as db:
            events=[json.loads(row[0]) for row in db.execute("SELECT detail FROM demand_events WHERE event='review_contract_rejected'")]
            self.assertEqual(db.execute("SELECT count(*) FROM demand_events WHERE event='review_completed'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM demand_agent_tasks WHERE parent_task_id IS NOT NULL').fetchone()[0],0)
        db.close()
        self.assertEqual(events[0]['expected_count'],0)
        self.assertEqual(events[0]['received_count'],1)
        self.assertEqual(events[0]['extra_key_count'],1)
        self.assertNotIn('PRIVATE_',dr.dumps(events))
        accepted=self.step(cli)
        self.assertEqual(accepted['stage'],'discovery')
        self.assertIn('Предыдущий ответ не принят: agent_review_extra_keys',cli.prompts[-1])
        self.assertIn('Ни одна проверка этим ответом не разрешена',cli.prompts[-1])
        # A scheduler replay resumes the persisted next stage; it cannot repeat
        # the accepted review or grant an extra child authorization.
        self.step(cli)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM demand_events WHERE event='review_completed'").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM demand_agent_tasks WHERE parent_task_id IS NOT NULL').fetchone()[0],0)
        db.close()

    def test_nonempty_review_authoritative_keys_are_separate_and_approval_commits_once(self):
        checkpoint,cli=self.prepare_review(child_request=True)
        original=cli.structured
        def observe(prompt,schema):
            self.assertEqual(schema['properties']['decisions']['items']['properties']['key']['enum'],['grounded-child'])
            self.assertEqual(schema['properties']['decisions']['minItems'],1)
            self.assertEqual(schema['properties']['decisions']['maxItems'],1)
            self.assertIn('Единственный разрешённый список ключей decisions: ["grounded-child"]',prompt)
            return original(prompt,schema)
        cli.structured=observe
        self.step(cli)
        cli.structured=original
        self.step(cli)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM demand_events WHERE event='review_completed'").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM demand_agent_tasks WHERE parent_task_id IS NOT NULL').fetchone()[0],1)
        db.close()

    def test_adapter_schema_rejection_logs_unknown_response_counts_without_raw_error(self):
        self.prepare_review()
        class InvalidAdapter:
            def structured(self,prompt,schema):
                raise dr.StructuredOutputError('PRIVATE_PROMPT_OR_OUTPUT_891')
        result=self.step(InvalidAdapter())
        self.assertEqual(result['error_category'],'agent_review_schema_invalid')
        with sqlite3.connect(self.path) as db:
            detail=json.loads(db.execute("SELECT detail FROM demand_events WHERE event='review_contract_rejected'").fetchone()[0])
        db.close()
        self.assertFalse(detail['response_available'])
        self.assertIsNone(detail['received_count'])
        self.assertIsNone(detail['extra_key_count'])
        self.assertNotIn('PRIVATE_',dr.dumps(result)+dr.dumps(detail))

    def test_researcher_child_cannot_use_source_from_another_role(self):
        from tests.test_demand_agents import child
        cli=AgentCli(child_request=False)
        for _ in range(40):
            checkpoint=self.step(cli)
            if checkpoint['stage']=='agent_report':
                with sqlite3.connect(self.path) as db:
                    current=db.execute("SELECT id,role FROM demand_agent_tasks WHERE status='running'").fetchone()
                db.close()
                if current[1]=='alternatives':break
        else:self.fail('alternative report checkpoint not reached')
        with sqlite3.connect(self.path) as db:
            other=db.execute("SELECT c.source_id,s.text FROM demand_candidates c JOIN demand_batches b ON b.cycle_id=c.cycle_id AND b.batch_index=c.batch_index JOIN demand_agent_tasks t ON t.id=b.agent_task_id JOIN demand_sources s ON s.id=c.source_id WHERE t.role='buyer_problem' AND c.state='read' ORDER BY c.rowid LIMIT 1").fetchone()
        db.close()
        original=cli.structured
        captured=[]
        def cross_role(prompt,schema):
            value=original(prompt,schema)
            captured.append(schema)
            value['child_requests']=[{**child('cross-role-source'), 'source_ids':[other[0]],'trigger_quote':deep_quote(0)}]
            return value
        cli.structured=cross_role
        result=self.step(cli)
        self.assertEqual(result['error_category'],'schema_invalid')
        self.assertNotIn(other[0],captured[0]['properties']['child_requests']['items']['properties']['source_ids']['items']['enum'])
        with sqlite3.connect(self.path) as db:
            reports=[json.loads(row[0]) for row in db.execute("SELECT detail FROM demand_events WHERE event='role_report'")]
            self.assertEqual(db.execute('SELECT count(*) FROM demand_agent_tasks WHERE parent_task_id IS NOT NULL').fetchone()[0],0)
        db.close()
        self.assertFalse(any(report['task_id']==current[0] for report in reports))
        cli.structured=original
        accepted=self.step(cli)
        self.assertEqual(accepted['stage'],'agent_review')

    def test_real_plan_separate_roles_child_and_critic_before_final_synthesis(self):
        cli=AgentCli()
        result,states=self.finish(cli)
        self.assertTrue(result['ok'],result)
        self.assertEqual(states[0]['stage'],'discovery')
        view=dr.execute({'action':'report','id':result['cycle_id']},self.path,self.cards)['cycles'][0]
        tasks=view['orchestration']['tasks']
        self.assertEqual(len(tasks),5)
        self.assertEqual({item['role'] for item in tasks if not item['parent_task_id']},set(dr.agents.ROLES))
        self.assertTrue(all(item['status']=='done' for item in tasks))
        self.assertEqual(len({item['id'] for item in tasks}),5)
        self.assertTrue(result['coverage']['counter_search_completed'])
        self.assertTrue(result['notify'])
        self.assertEqual(len(view['orchestration']['syntheses']),1)
        self.assertEqual(cli.discovery_calls,5)
        self.assertIn('Ты оркестратор',cli.prompts[0])
        with sqlite3.connect(self.path) as db:
            assigned=[json.loads(row[0]) for row in db.execute("SELECT detail FROM demand_events WHERE event='task_assigned' ORDER BY id")]
            finalstage=db.execute("SELECT count(*) FROM demand_events WHERE event='synthesis_final'").fetchone()[0]
        db.close()
        self.assertEqual(assigned[-1]['role'],'critic')
        self.assertEqual(finalstage,1)
        cards=dr.execute({'action':'hypotheses'},self.path,self.cards)['hypotheses']
        self.assertTrue(any(e['kind']=='foreign_context' for item in cards for e in item['evidence']))
        self.assertEqual(cards[0]['evidence_summary']['direct_story_origins'],3)

    def test_synthesis_can_run_its_child_in_same_cycle_then_reconsolidate(self):
        cli=AgentCli(child_request=False,final_child=True)
        result,_=self.finish(cli)
        view=dr.execute({'action':'report','id':result['cycle_id']},self.path,self.cards)['cycles'][0]['orchestration']
        self.assertEqual(len(view['tasks']),5)
        self.assertEqual([item['status'] for item in view['syntheses']],['review','final'])
        self.assertTrue(all(item['status']=='done' for item in view['tasks']))

    def test_deferred_or_failed_synthesis_blocks_priority_and_records_real_gap(self):
        cli=AgentCli(defer=True)
        result,_=self.finish(cli)
        self.assertFalse(result['notify'])
        self.assertTrue(result['coverage']['agent_progress']['review_needed'])
        self.assertTrue(any('Дочерние проверки' in item for item in result['coverage']['analysis_gaps']))

    def test_planning_and_synthesis_failure_are_bounded(self):
        cli=AgentCli(fail_stage='agent_plan')
        result,states=self.finish(cli)
        self.assertEqual(len(states),3)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(cli.discovery_calls,0)
        with patch.object(dr,'now',return_value=(STAMP+timedelta(days=365)).isoformat()):
            result,_=self.finish(AgentCli(child_request=False,fail_stage='agent_synthesis'))
        self.assertFalse(result['notify'])
        self.assertTrue(result['coverage']['agent_progress']['synthesis_failed'])
        self.assertTrue(any('сопоставление оркестратором' in item for item in result['coverage']['analysis_gaps']))

    def test_quality_increases_only_demanding_stage_and_preserves_higher_setting(self):
        from types import SimpleNamespace
        cycle={'plan':json.dumps({'version':3}),'stage':'agent_plan'}
        client=SimpleNamespace(reasoning_effort='low')
        dr._configure_agent_quality(client,cycle)
        self.assertEqual(client.reasoning_effort,'medium')
        self.assertEqual(client.minimum_reasoning_effort,'medium')
        client.reasoning_effort='high';dr._configure_agent_quality(client,cycle)
        self.assertEqual(client.reasoning_effort,'high')
        client.reasoning_effort='low';cycle['stage']='analyze';dr._configure_agent_quality(client,cycle,{'role':'buyer_problem'})
        self.assertEqual(client.reasoning_effort,'low')
        self.assertIsNone(client.minimum_reasoning_effort)
        dr._configure_agent_quality(client,cycle,{'role':'critic'})
        self.assertEqual(client.minimum_reasoning_effort,'medium')
        client.reasoning_effort='low';cycle['stage']='agent_report';dr._configure_agent_quality(client,cycle,{'role':'buyer_problem'})
        self.assertEqual(client.minimum_reasoning_effort,'medium')
        self.assertEqual(client.reasoning_effort,'medium')

    def test_failed_critic_sources_do_not_complete_counter_check(self):
        original=self.fetcher
        def no_critic(url):
            if int(url.rsplit('/',1)[1])>=36:return {'ok':False,'error_category':'access_denied'}
            return original(url)
        self.fetcher=no_critic
        result,_=self.finish(AgentCli(child_request=False))
        self.assertFalse(result['coverage']['counter_search_completed'])
        self.assertFalse(result['notify'])
        # A completed search with no readable body receives one bounded search
        # on alternative sites; this fixture denies those four pages as well.
        self.assertEqual(result['coverage']['failed'],8)

    def test_other_role_conclusions_never_enter_researcher_context(self):
        sentinel='PRIVATE_OTHER_RESEARCHER_CONCLUSION_913'
        cli=AgentCli(child_request=False)
        for _ in range(80):
            result=self.step(cli)
            with sqlite3.connect(self.path) as db:
                rows=db.execute('SELECT id,payload FROM demand_hypotheses').fetchall()
                for identity,payload in rows:
                    value=json.loads(payload)
                    value['unknowns']=[sentinel];value['counterarguments']=[sentinel]
                    db.execute('UPDATE demand_hypotheses SET payload=? WHERE id=?',(dr.dumps(value),identity))
            db.close()
            if not result['has_more']:break
        researcher_prompts=[prompt for prompt in cli.prompts if prompt.startswith(('Ты выполняешь','Проанализируй','Ты отдельный исследователь'))]
        self.assertTrue(researcher_prompts)
        self.assertTrue(all(sentinel not in prompt for prompt in researcher_prompts))
        self.assertTrue(any(sentinel in prompt for prompt in cli.prompts if prompt.startswith('Ты оркестратор, завершающий')))

    def test_foreign_parent_marks_other_role_child_as_international(self):
        class ForeignChild(AgentCli):
            def structured(self,prompt,schema):
                from tests.test_demand_agents import child
                value=super().structured(prompt,schema)
                if 'findings' in schema['properties']:
                    task=json.loads(prompt.split('Твоё поручение: ',1)[1].split('\nПроверенные свидетельства:',1)[0])
                    evidence=json.loads(prompt.split('\nПроверенные свидетельства: ',1)[1].split('\nРезультаты частей',1)[0])
                    if task['role']=='foreign' and evidence:
                        quote=evidence[0]['evidence'][0]
                        value['child_requests']=[{**child('foreign-child','alternatives'), 'source_ids':[quote['source_id']],'trigger_quote':quote['quote']}]
                if 'hypotheses' in schema['properties'] and 'foreign-child' in prompt:
                    for hypothesis in value['hypotheses']:
                        for item in hypothesis['evidence']:item.update(kind='problem',source_type='complaint')
                return value
        result,_=self.finish(ForeignChild(child_request=False))
        with sqlite3.connect(self.path) as db:
            rows=db.execute("SELECT c.geography FROM demand_candidates c JOIN demand_batches b ON b.cycle_id=c.cycle_id AND b.batch_index=c.batch_index JOIN demand_agent_tasks t ON t.id=b.agent_task_id WHERE t.parent_task_id IS NOT NULL").fetchall()
            bad=db.execute("SELECT count(*) FROM demand_evidence e JOIN demand_candidates c ON c.source_id=e.source_id WHERE c.geography='international' AND e.kind IN ('problem','demand','workaround')").fetchone()[0]
        db.close()
        self.assertTrue(rows)
        self.assertEqual({row[0] for row in rows},{'international'})
        self.assertEqual(bad,0)

    def test_synthesis_foreign_source_child_of_critic_keeps_international_scope(self):
        class ForeignSynthesisChild(AgentCli):
            def structured(self,prompt,schema):
                from tests.test_demand_agents import child
                value=super().structured(prompt,schema)
                if 'decisions' in schema['properties'] and 'conflicts' in schema['properties'] and 'проверки: 1/' in prompt:
                    evidence=json.loads(prompt.split('\nСвидетельства: ',1)[1].split('\nФактический охват:',1)[0])
                    quote=next(item for group in evidence for item in group['evidence'] if item['kind']=='foreign_context')
                    value['child_requests']=[{**child('foreign-synthesis-child','alternatives'),
                        'source_ids':[quote['source_id']], 'trigger_quote':quote['quote']}]
                if 'hypotheses' in schema['properties'] and 'foreign-synthesis-child' in prompt:
                    for hypothesis in value['hypotheses']:
                        for item in hypothesis['evidence']:item.update(kind='problem',source_type='complaint')
                return value
        result,_=self.finish(ForeignSynthesisChild(child_request=False))
        with sqlite3.connect(self.path) as db:
            rows=db.execute("SELECT t.role,p.role,c.geography FROM demand_agent_tasks t JOIN demand_agent_tasks p ON p.id=t.parent_task_id JOIN demand_batches b ON b.agent_task_id=t.id JOIN demand_candidates c ON c.cycle_id=b.cycle_id AND c.batch_index=b.batch_index").fetchall()
            bad=db.execute("SELECT count(*) FROM demand_evidence e JOIN demand_candidates c ON c.source_id=e.source_id WHERE c.geography='international' AND e.kind IN ('problem','demand','workaround')").fetchone()[0]
        db.close()
        self.assertTrue(rows)
        self.assertEqual(set(rows),{('alternatives','critic','international')})
        self.assertEqual(bad,0)
        self.assertTrue(result['coverage']['counter_search_completed'])

    def test_international_grounding_requires_read_source_in_the_same_cycle(self):
        result,_=self.finish(AgentCli(child_request=False))
        store=dr.Research(self.path,self.cards)
        try:
            source=store.rows("SELECT source_id FROM demand_candidates WHERE cycle_id=? AND geography='international' AND state='read' LIMIT 1",(result['cycle_id'],))[0]['source_id']
            task={'role':'alternatives','parent_task_id':None,'cycle_id':result['cycle_id'],'payload':{'source_ids':[source]}}
            self.assertTrue(dr._agent_international(store,task))
            self.assertFalse(dr._agent_international(store,{**task,'cycle_id':'unrelated-cycle'}))
            self.assertFalse(dr._agent_international(store,{**task,'payload':{'source_ids':['not-a-read-source']}}))
            with store.db:
                store.db.execute("UPDATE demand_candidates SET state='pending' WHERE cycle_id=? AND source_id=?",(result['cycle_id'],source))
            self.assertFalse(dr._agent_international(store,task))
        finally:store.close()

    def test_foreign_observations_survive_without_attaching_to_a_hypothesis(self):
        class UnattachedForeign(AgentCli):
            def structured(self,prompt,schema):
                value=super().structured(prompt,schema)
                if 'hypotheses' in schema['properties'] and '"role": "foreign"' in prompt:
                    for item in value['hypotheses']:
                        item.update(existing_id='',key='foreign-standalone-application',buyer='Жители другого государства',
                                    problem='Иная полностью незнакомая покупателю задача иностранного рынка',
                                    offer='Предварительное предложение на другом рынке')
                return value
        result,_=self.finish(UnattachedForeign(child_request=False))
        view=dr.execute({'action':'report','id':result['cycle_id']},self.path,self.cards)['cycles'][0]
        observations=view['orchestration']['verified_observations']
        foreign=[item for item in observations if item['geography']=='international']
        self.assertTrue(foreign)
        with sqlite3.connect(self.path) as db:
            foreign_ids={item['source_id'] for item in foreign}
            attached={row[0] for row in db.execute('SELECT source_id FROM demand_evidence')}
        db.close()
        self.assertFalse(foreign_ids&attached)
        report=(self.cards/('research-cycle-'+result['cycle_id']+'.md')).read_text(encoding='utf-8')
        self.assertIn(foreign[0]['url'],report)
        self.assertIn(foreign[0]['quote'],report)
        self.assertIn('автоматически не увеличивают',report)

    def test_failed_planner_parks_an_already_selected_question(self):
        with patch.object(dr,'AGENT_RESEARCH_ENABLED',False),patch.object(dr,'now',return_value=STAMP.isoformat()):
            DeepDemand.finish(self,QuestionCli())
        with patch.object(dr,'now',return_value=(STAMP+timedelta(hours=1)).isoformat()):
            result,_=self.finish(AgentCli(fail_stage='agent_plan'))
        self.assertEqual(result['status'],'failed')
        with sqlite3.connect(self.path) as db:
            state=db.execute('SELECT state,result FROM demand_questions').fetchone()
        db.close()
        self.assertEqual(state[0],'parked')
        self.assertEqual(json.loads(state[1])['verdict'],'inconclusive')


class AgentReviewContract(unittest.TestCase):
    def test_report_keeps_entire_2048_character_source_with_explicit_metadata(self):
        body='A'*764+'Ориентир выплаты за одну закрытую сделку зависит от формата.'
        body=body+'B'*(2048-len(body))
        original={'s1':{'id':'s1','url':'https://example.test/referral','title':'Public terms','text':body}}
        context=dr._report_source_context(original)
        self.assertEqual(context[0]['text'],body)
        self.assertEqual(context[0]['stored_characters'],2048)
        self.assertEqual(context[0]['provided_characters'],2048)
        self.assertFalse(context[0]['truncated_for_report'])
        self.assertEqual(json.loads(dr.dumps(context)),context)
        self.assertEqual(original['s1']['text'],body)

    def test_report_excerpt_budget_preserves_complete_json_and_all_source_metadata(self):
        sources={f's{index:02}':{'id':f's{index:02}','url':f'https://example.test/{index}',
                 'title':'Long public document','text':'"line\\next\n'*1500} for index in range(15)}
        context=json.loads(dr.dumps(dr._report_source_context(sources)))
        self.assertEqual(len(context),15)
        self.assertEqual({row['id'] for row in context},set(sources))
        self.assertEqual(sum(row['provided_characters'] for row in context),24000)
        self.assertTrue(all(row['provided_characters']==1600 for row in context))
        for row in context:
            self.assertEqual(row['stored_characters'],len(sources[row['id']]['text']))
            self.assertEqual(row['provided_characters'],len(row['text']))
            self.assertLessEqual(row['provided_characters'],4000)
            self.assertTrue(row['truncated_for_report'])
        mixed={f's{index}':{'id':f's{index}','url':f'https://example.test/{index}',
               'title':'Public source','text':'x'*(100 if index<10 else 9000)} for index in range(14)}
        allocation=dr._report_source_context(mixed)
        self.assertEqual(sum(row['provided_characters'] for row in allocation),17000)
        self.assertEqual(dr._report_source_context({}),[])

    def test_child_schema_uses_real_source_ids_not_evidence_or_observation_ids(self):
        import jsonschema
        from tests.test_demand_agents import child
        role=dr._child_result_schema(dr.ROLE_REPORT,{'s1'})
        value={'summary':'Проверка роли выполнена.', 'findings':[], 'conflicts':[], 'limitations':[],
               'child_requests':[child()], 'branch_reason':'Существенная неизвестная характеристика способна изменить вывод и требует отдельной проверки.'}
        jsonschema.validate(value,role)
        for wrong in ('obs_1234','evidence_1234','s2'):
            with self.subTest(source_id=wrong):
                invalid=copy.deepcopy(value);invalid['child_requests'][0]['source_ids']=[wrong]
                with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(invalid,role)
        synthesis=dr._child_result_schema(dr.SYNTHESIS,{'s1','s2'})
        total={key:item for key,item in value.items() if key!='findings'}
        total['decisions']=[];total['child_requests'][0]['source_ids']=['s2']
        jsonschema.validate(total,synthesis)
        empty=dr._child_result_schema(dr.ROLE_REPORT,set())
        self.assertEqual(empty['properties']['child_requests']['maxItems'],0)
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,empty)

    def test_dynamic_schema_and_runtime_reject_extra_missing_and_duplicate_keys(self):
        import jsonschema
        keys=['child-alpha','child-beta']
        schema=dr._review_schema(keys)
        def response(values):
            return {'summary':'Отчёт оценён отдельно от решений о дочерних запросах.',
                    'decisions':[{'key':value,'decision':'reject','reason':'Нужны более убедительные основания.'} for value in values]}
        valid=response(keys)
        self.assertEqual(dr._validate_review(valid,schema,keys),valid)
        for values,category in [(['child-alpha','invented'],'agent_review_extra_keys'),
                                (['child-alpha'],'agent_review_missing_keys'),
                                (['child-alpha','child-alpha'],'agent_review_duplicate_keys')]:
            with self.subTest(values=values):
                with self.assertRaises(dr.ReviewContractError) as error:
                    dr._validate_review(response(values),schema,keys)
                self.assertEqual(error.exception.category,category)
                self.assertEqual(error.exception.metadata['expected_count'],2)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(response(['child-alpha','invented']),schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(response(['child-alpha']),schema)
        empty=dr._review_schema([])
        self.assertEqual(dr._validate_review(response([]),empty,[]),response([]))
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(response(['invented']),empty)


class QuestionCli(DeepCli):
    def __init__(self, invented_quote=False):
        super().__init__(counts=(3, 3, 3, 3))
        self.emitted = False
        self.invented_quote = invented_quote

    def structured(self, prompt, schema):
        value = super().structured(prompt, schema)
        if 'candidates' in schema['properties']:
            return value
        pages = json.loads(prompt.split('\nПрочитанные источники: ', 1)[1])
        plan = json.loads(prompt.split('План до сбора: ', 1)[1].split('\nСтарые гипотезы: ', 1)[0])
        if plan.get('question'):
            vendor_pages = [page for page in pages if int(page['url'].rsplit('/', 1)[1]) >= 3]
            if vendor_pages:
                page = vendor_pages[0]
                quote = 'Совершенно выдуманная цитата о доказанных массовых продажах.' if self.invented_quote else deep_quote(int(page['url'].rsplit('/', 1)[1]))
                value['branch_answer'] = {'verdict': 'supported', 'basis': 'В предложении поставщика явно названа интеграция заказов; это не доказательство внедрения или спроса.',
                                          'source_ids': [page['id']], 'evidence': [{'source_id': page['id'], 'quote': quote}]}
        elif not self.emitted:
            self.emitted = True
            value['followups'] = [{'hypothesis_id': '', 'question': {
                'question_key': 'order-integration-offers', 'question': 'Предлагают ли существующие поставщики интеграцию заказов?',
                'query': 'программа интеграция заказов документация отзывы', 'reason': 'Нужно проверить существующие решения до разработки своего.',
                'priority': 'critical', 'kind': 'alternatives', 'geography': 'domestic', 'region': 'Россия', 'subject': '',
                'pass_condition': 'В публичном предложении поставщика прямо указана интеграция заказов.',
                'fail_condition': 'Документация прямо исключает нужный вид интеграции.', 'source_ids': [pages[0]['id']]}}]
        return value
