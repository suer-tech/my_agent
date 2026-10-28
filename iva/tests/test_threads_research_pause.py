import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deploy import threads_hourly_tick as tick
from iva_integration import threads_hourly as worker


class ResearchPauseBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = self.root / 'research-pauses.json'
        self.root_patch = patch.object(worker, 'ROOT', self.root)
        self.ledger_patch = patch.object(worker, 'RESEARCH_PAUSES', self.ledger)
        self.root_patch.start()
        self.ledger_patch.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.ledger_patch.stop)

    def pauses(self, intervals=(), active_since=None):
        self.ledger.write_text(json.dumps({'intervals': list(intervals),
                                          'active_since': active_since}), encoding='utf-8')

    def seed_cycle(self, started=10000, post_state='new'):
        worker.write('hourly-launch.json', {'enabled': True, 'pending': True, 'publish': True})
        db = worker.db_open()
        try:
            with db:
                db.execute("INSERT INTO cycles(id,started,state) VALUES (1,?,'active')", (started,))
                db.execute("INSERT INTO posts(url,cycle_id,published,body,state,comment,reply_url) VALUES (?,1,?,?,?,?,?)",
                           ('https://www.threads.com/@a/post/b', started - 10, 'Ищу работу',
                            post_state, 'saved draft', 'https://www.threads.com/@suer_stuff/post/ack'))
        finally:
            db.close()

    def run_search_step(self, now):
        page = Mock()
        with patch.object(worker.time, 'time', return_value=now), \
             patch('iva_integration.threads_page.working_page', return_value=page), \
             patch.object(worker, 'require_session'), \
             patch.object(worker, 'candidate_cards', return_value=[]), \
             patch.object(worker, 'any_pending', return_value=True), \
             patch('iva_integration.runtime_diagnostics.event'):
            worker.step(object())
        return page

    def test_long_research_pause_preserves_cycle_and_candidates_after_resume(self):
        self.seed_cycle()
        self.pauses([[10100, 16900]])
        page = self.run_search_step(17000)
        db = worker.db_open()
        try:
            cycle = dict(db.execute('SELECT * FROM cycles').fetchone())
            post = dict(db.execute('SELECT * FROM posts').fetchone())
        finally:
            db.close()
        self.assertEqual((cycle['state'], cycle['started'], cycle['source_index']), ('active', 10000, 1))
        self.assertEqual((post['cycle_id'], post['state'], post['comment']), (1, 'new', 'saved draft'))
        self.assertIsNone(post['attempt_at'])
        self.assertTrue(worker.read('hourly-launch.json')['pending'])
        page.goto.assert_called_once()
        self.assertEqual(worker.read('hourly-status.json')['cycle_timing'],
                         {'elapsed_seconds': 7000, 'research_paused_seconds': 6800,
                          'budget_elapsed_seconds': 200})
        # No in-memory offsets: reopening/re-reading cannot subtract the same pause twice.
        self.assertEqual(worker.cycle_timing(cycle, 17100)['budget_elapsed_seconds'], 300)

    def test_real_active_budget_still_expires_without_modifying_saved_post(self):
        self.seed_cycle(post_state='uncertain')
        self.pauses([[10100, 16000]])
        page = self.run_search_step(20000)
        db = worker.db_open()
        try:
            cycle = dict(db.execute('SELECT * FROM cycles').fetchone())
            post = dict(db.execute('SELECT * FROM posts').fetchone())
        finally:
            db.close()
        self.assertEqual((cycle['state'], cycle['started']), ('expired', 10000))
        self.assertIn('40 active minutes', cycle['error'])
        self.assertEqual((post['state'], post['reply_url']),
                         ('uncertain', 'https://www.threads.com/@suer_stuff/post/ack'))
        self.assertFalse(worker.read('hourly-launch.json')['pending'])
        page.goto.assert_not_called()

    def test_interrupted_send_remains_uncertain_even_after_long_pause(self):
        self.seed_cycle(post_state='sending')
        self.pauses([[10100, 16900]])
        with patch.object(worker.time, 'time', return_value=17000), \
             patch.object(worker, 'any_pending', return_value=True), \
             patch('iva_integration.threads_page.working_page') as browser:
            worker.step(object())
        db = worker.db_open()
        try:
            self.assertEqual(db.execute('SELECT state FROM posts').fetchone()[0], 'uncertain')
            self.assertEqual(db.execute('SELECT state FROM cycles').fetchone()[0], 'error')
        finally:
            db.close()
        browser.assert_not_called()

    def test_union_clips_overlaps_and_open_pause_to_cycle_lifetime(self):
        self.pauses([[0, 150], [125, 200], [150, 200], [300, 400]], active_since=190)
        self.assertEqual(worker.research_paused_seconds(100, 250), 150)
        self.pauses([[0, 200], [190, 250]])  # Same pause after coordinator restart/release.
        self.assertEqual(worker.research_paused_seconds(100, 250), 150)
        self.assertEqual(worker.research_paused_seconds(300, 250), 0)

    def test_absent_or_malformed_ledger_never_invents_a_pause(self):
        self.assertEqual(worker.research_paused_seconds(100, 200), 0)
        self.ledger.write_text('{broken', encoding='utf-8')
        self.assertEqual(worker.research_paused_seconds(100, 200), 0)
        self.pauses([[True, 200], ['100', 200], [100, float('inf')], [200, 100], [100]])
        self.assertEqual(worker.research_paused_seconds(100, 200), 0)

    def test_tick_only_enqueues_and_never_switches_interactive_max(self):
        worker.write('hourly-launch.json', {'enabled': True, 'pending': False, 'publish': True})
        request = Mock(return_value={'current': 'max-manual'})
        tick.queue_campaign('extension', request=request)
        request.assert_called_once_with({'action': 'enqueue', 'task': 'threads'})
        self.assertTrue(worker.read('hourly-launch.json')['pending'])
        self.assertTrue(worker.read('hourly-launch.json')['publish'])

    def test_tick_does_not_reenable_paused_campaign(self):
        original = {'enabled': False, 'pending': False}
        worker.write('hourly-launch.json', original)
        request = Mock()
        tick.queue_campaign('extension', request=request)
        request.assert_not_called()
        self.assertEqual(worker.read('hourly-launch.json'), original)


if __name__ == '__main__':
    unittest.main()
