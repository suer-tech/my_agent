import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from iva_integration import threads_development as development, threads_hourly as worker


class AllAges(unittest.TestCase):
    def test_activity_has_bounded_deadline_and_idle_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'ROOT', Path(directory)), patch.object(worker.time, 'time', return_value=1000):
            worker.activity('ai', 'development')
            self.assertEqual(worker.read('activity.json'), {'campaign': 'development', 'stage': 'ai', 'state': 'running', 'updated_at': 1000, 'deadline_at': 1120})
            self.assertLessEqual(max(worker.ACTIVITY_BUDGETS.values()), 180)
            worker.activity('idle', 'development')
            self.assertEqual(worker.read('activity.json')['state'], 'idle')
            self.assertEqual(worker.read('activity.json')['deadline_at'], 1000)

    def test_old_dates_allowed_but_invalid_and_future_dates_rejected(self):
        now = 1800000000
        for published in (now, now - 3601, now - 86400 * 365 * 5):
            self.assertTrue(worker.fresh(published, now))
        for published in (None, '2026-10-25', True, float('nan'), float('inf'), now + 1):
            self.assertFalse(worker.fresh(published, now))
        self.assertFalse(worker.fresh(now - 3601, now, max_age=3600))

    def test_both_campaigns_discover_all_queries_in_ranked_and_recent_search(self):
        for config in (worker.default_config(), development.config()):
            with self.subTest(campaign=config['name']):
                self.assertIsNone(config['max_age'])
                modes = {'ranked': set(), 'recent': set()}
                for slot in range(len(config['queries'])):
                    cycle = {'started': slot * config['interval']}
                    self.assertEqual(worker.search_source(config, cycle, 0), 'https://www.threads.com/')
                    for index in range(1, config['source_count']):
                        query = parse_qs(urlsplit(worker.search_source(config, cycle, index)).query)
                        mode = 'recent' if query.get('filter') == ['recent'] else 'ranked'
                        modes[mode].add(query['q'][0])
                self.assertEqual(modes['ranked'], set(config['queries']))
                self.assertEqual(modes['recent'], set(config['queries']))

    def test_queue_migration_only_restores_never_attempted_age_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {**worker.default_config(), 'root': Path(directory)}
            database = worker.db_open(config['root'])
            rows = [
                ('old', 'old', 'Older than 2h', None),
                ('during', 'old', 'Aged during inspection', None),
                ('root_age', 'skipped', 'Not a fresh root thread or timestamp unavailable', None),
                ('new', 'new', None, None),
                ('preflight', 'preflight_failed', None, None),
                ('other_old', 'old', 'Timestamp invalid', None),
                ('other_skip', 'skipped', 'Own reply already present', None),
                ('no_fit', 'no_fit', 'Not relevant', None),
                ('sent', 'sent', None, 10),
                ('sending', 'sending', None, 10),
                ('uncertain', 'uncertain', None, 10),
                ('attempted_old', 'old', 'Older than 2h', 10),
                ('attempted_preflight', 'preflight_failed', None, 10),
            ]
            with database:
                for name, state, reason, attempt in rows:
                    database.execute('INSERT INTO posts(url,cycle_id,published,state,reason,attempt_at) VALUES (?,1,10,?,?,?)',
                                     (name, state, reason, attempt))
            worker.restore_unsubmitted(database, 2, config, now=10000000)
            restored = {row['url'] for row in database.execute('SELECT * FROM posts WHERE cycle_id=2')}
            self.assertEqual(restored, {'old', 'during', 'root_age', 'new', 'preflight'})
            for name, state, reason, attempt in rows:
                row = database.execute('SELECT * FROM posts WHERE url=?', (name,)).fetchone()
                self.assertEqual(row['attempt_at'], attempt)
                if name not in restored:
                    self.assertEqual(row['state'], state)
            worker.status(database, config)
            self.assertIsNone(worker.read('hourly-status.json', root=config['root'])['limits']['max_age_minutes'])
            database.close()

    def test_old_root_reaches_ai_and_preview_without_submission(self):
        for base in (worker.default_config(), development.config()):
            with self.subTest(campaign=base['name']), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                url = 'https://www.threads.com/@example/post/old_job_request'
                published = time.time() - 86400 * 365
                comment = ('Я делаю расширение для ручных откликов. ' + worker.STORE if base['name'] == 'extension'
                           else 'Я разработчик, могу обсудить вашу задачу. ' + development.PORTFOLIO)
                decide = Mock(return_value={'fit': True, 'reason': 'Open relevant request', 'comment': comment})
                config = {**base, 'root': root, 'decide': decide}
                worker.write('hourly-launch.json', {'enabled': True, 'pending': True, 'publish': False}, root=root)
                database = worker.db_open(root)
                with database:
                    database.execute("INSERT INTO cycles(id,started,state,source_index) VALUES (1,?,'active',?)",
                                     (time.time(), config['source_count']))
                    database.execute("INSERT INTO posts(url,cycle_id,published,state) VALUES (?,1,?,'new')", (url, published))
                database.close()

                page = SimpleNamespace(url=url, goto=Mock(), wait_for_timeout=Mock(), wait_for_function=Mock())
                with patch('iva_integration.threads_page.working_page', return_value=page), \
                     patch.object(worker, 'require_session'), patch.object(worker, 'signed_in', return_value=True), \
                     patch.object(worker, 'candidate_cards', return_value=[{'url': url, 'published': published}]), \
                     patch.object(worker, 'known_reply', return_value=False), \
                     patch('iva_integration.threads_browser_rpc.snapshot', return_value={'links': []}), \
                     patch.object(worker, 'publish') as publish, patch.object(worker, 'finish') as finish:
                    worker.step(object(), config=config)
                decide.assert_called_once()
                publish.assert_not_called()
                self.assertEqual(finish.call_args.args[2], 'preview')
                database = worker.db_open(root)
                row = database.execute('SELECT * FROM posts').fetchone()
                self.assertEqual(row['state'], 'drafted')
                self.assertIsNone(row['attempt_at'])
                database.close()

    def test_ranked_search_collects_old_candidate_and_releases_activity_marker(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'ROOT', Path(directory)):
            root = Path(directory)
            config = worker.default_config()
            worker.write('hourly-launch.json', {'enabled': True, 'pending': True, 'publish': False})
            database = worker.db_open()
            with database:
                database.execute("INSERT INTO cycles(id,started,state,source_index) VALUES (1,?,'active',2)", (time.time(),))
            database.close()
            url = 'https://www.threads.com/@example/post/year_old'
            card = {'url': url, 'published': time.time() - 86400 * 365,
                    'body': 'Подскажите инструмент: вручную пишу сопроводительные письма на hh.ru'}
            # No get_by_role: ranked search must not force the Recent tab.
            page = SimpleNamespace(goto=Mock(), wait_for_timeout=Mock(), wait_for_function=Mock(), mouse=SimpleNamespace(wheel=Mock()))
            with patch('iva_integration.threads_page.working_page', return_value=page), \
                 patch.object(worker, 'require_session'), patch.object(worker, 'candidate_cards', return_value=[card]), \
                 patch('iva_integration.runtime_diagnostics.event'):
                worker.step(object(), config=config)
            self.assertNotIn('filter=recent', page.goto.call_args.args[0])
            database = worker.db_open()
            self.assertEqual(database.execute('SELECT count(*) FROM posts').fetchone()[0], 1)
            self.assertEqual(database.execute('SELECT state FROM posts').fetchone()[0], 'new')
            database.close()
            self.assertEqual(worker.read('activity.json')['state'], 'idle')


if __name__ == '__main__':
    unittest.main()
