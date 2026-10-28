"""Linux server acceptance: real guarded filesystem removal, isolated owner DB."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

if os.name == 'posix':
    from deploy import disk_storage_root as helper
    from iva_integration.disk_storage import execute, permitted, present
    from local_finance.assistant import Ledger, Assistant
    from local_finance.bot import OWNER


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root helper filesystem checks run separately as root on Linux')
class DiskStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='iva-disk-test-')
        self.root = Path(self.tmp.name); self.cache = self.root / 'cache'
        self.cache.mkdir(); (self.cache / 'package.bin').write_bytes(b'x' * 16384)
        self.protected = self.root / 'finance.sqlite3'; self.protected.write_bytes(b'important')
        self.patches = [patch.object(helper, 'CACHES', {'iva_npm': (str(self.cache), 'Кэш npm', 'npm')}),
                        patch.object(helper, 'BACKUPS', self.root / 'backups')]
        for p in self.patches: p.start()
        self.ledger = Ledger(':memory:'); Assistant(self.ledger, None); self.uid = 0

    def tearDown(self):
        self.ledger.db.close()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def incoming(self, text, **extra):
        self.uid += 1
        msg = {'from': {'id': OWNER}, 'chat': {'id': OWNER, 'type': 'private'}, 'text': text, **extra}
        with self.ledger.db:
            self.ledger.db.execute('INSERT INTO local_telegram_messages VALUES(?,?,?)', (self.uid, self.uid, json.dumps(msg)))
            self.ledger.db.execute("INSERT INTO local_updates(id,chat,text,metadata,state) VALUES(?,?,?,'{}','processing')", (self.uid, OWNER, text))
        return f'telegram:{OWNER}:{self.uid}:0'

    def runner(self, data):
        if data['action'] == 'analyze': return {'ok': True, 'candidates': [helper.candidate(helper.policy()['iva_npm'])]}
        if data['action'] == 'clean': return helper.clean(data['items'])
        return {'ok': True, 'disk': helper.usage()}

    def call(self, data, source=None):
        return execute(data, self.ledger, OWNER, source, state=self.root / 'state', runner=self.runner)

    def analyze(self): return self.call({'action': 'analyze'})

    def cleanup(self, text='Очисти кэш npm', source=None, **data):
        source = source or self.incoming(text)
        return self.call({'action': 'clean', 'candidate_ids': ['iva_npm'], 'instruction_quote': text, **data}, source)

    def test_clear_command_removes_cache_and_preserves_finance_and_audits_retry(self):
        self.analyze(); source = self.incoming('Очисти кэш npm')
        first = self.cleanup(source=source); second = self.cleanup(source=source)
        self.assertTrue(first['ok']); self.assertTrue(first['results'][0]['deleted'])
        self.assertGreater(first['removed_allocated_bytes'], 0); self.assertTrue(second['cached'])
        self.assertFalse(self.cache.exists()); self.assertEqual(self.protected.read_bytes(), b'important')
        self.assertFalse(self.ledger.rows('SELECT * FROM operations'))

    def test_read_and_feature_requests_cannot_delete(self):
        self.analyze()
        for text in ['Что занимает место?', 'Можно удалить кэш npm?', 'Не удаляй кэш npm',
                     'Добавь функционал, чтобы умела удалить мусор', 'Если мало места, удали кэш',
                     'Например удали кэш npm', 'Ива говорит «удали кэш npm»']:
            with self.subTest(text=text), self.assertRaises(PermissionError): self.cleanup(text)
        self.assertTrue(self.cache.exists())

    def test_no_owner_or_wrong_quote_or_forward_or_old_update(self):
        self.analyze()
        with self.assertRaises(PermissionError): self.call({'action': 'clean', 'candidate_ids': ['iva_npm'], 'instruction_quote': 'Очисти кэш npm'})
        source = self.incoming('Очисти кэш npm', forward_origin={'type': 'user'})
        with self.assertRaises(PermissionError): self.cleanup(source=source)
        source = self.incoming('Посмотри диск')
        with self.assertRaises(PermissionError): self.cleanup(source=source)
        source = self.incoming('Очисти кэш npm')
        self.ledger.db.execute("UPDATE local_updates SET state='done'")
        with self.assertRaises(PermissionError): self.cleanup(source=source)

    def test_group_scope_cannot_expand(self):
        npm = {'id': 'iva_npm', 'group': 'npm'}; pip = {'id': 'iva_pip', 'group': 'pip'}
        self.assertTrue(permitted('Очисти кэш npm', [npm]))
        self.assertFalse(permitted('Очисти кэш npm', [npm, pip]))
        self.assertTrue(permitted('Удали найденный безопасный мусор', [npm, pip]))
        self.assertFalse(permitted('Удали найденный мусор', [{'id': 'old_backup', 'group': 'backups'}]))
        self.assertFalse(permitted('Очисти кэш кроме pip', [pip]))
        self.assertFalse(permitted('Очисти root npm', [npm]))
        self.assertFalse(permitted('Очисти кэш пользователя Ива', [{'id': 'root_npm', 'group': 'npm'}]))

    def test_unknown_ids_and_arbitrary_paths_cannot_delete(self):
        self.analyze()
        with self.assertRaises(ValueError): self.cleanup(candidate_ids=[str(self.protected)])
        with self.assertRaises(ValueError): helper.clean([{'id': str(self.protected), 'fingerprint': 'x'}])
        self.assertEqual(self.protected.read_bytes(), b'important')

    def test_changed_files_need_new_analysis(self):
        self.analyze(); (self.cache / 'package.bin').write_bytes(b'new contents')
        result = self.cleanup()
        self.assertFalse(result['ok']); self.assertFalse(result['results'][0]['deleted'])
        self.assertIn('изменилось', result['results'][0]['reason']); self.assertTrue(self.cache.exists())

    def test_symlink_target_and_nested_symlink_cannot_escape(self):
        (self.cache / 'linked').symlink_to(self.protected)
        with self.assertRaises(ValueError): helper.candidate(helper.policy()['iva_npm'])
        (self.cache / 'linked').unlink(); self.cache.rename(self.root / 'old-cache')
        self.cache.symlink_to(self.root / 'old-cache', target_is_directory=True)
        with self.assertRaises((ValueError, OSError)): helper.candidate(helper.policy()['iva_npm'])
        self.assertEqual(self.protected.read_bytes(), b'important')

    def test_symlink_ancestor_and_hardlink_rejected(self):
        alias = self.root / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError): helper.parent_fd(str(alias / 'cache'))
        os.link(self.protected, self.cache / 'hardlink')
        with self.assertRaises(ValueError): helper.candidate(helper.policy()['iva_npm'])

    def test_busy_files_and_unverifiable_processes_are_blocked(self):
        row = helper.candidate(helper.policy()['iva_npm'])
        # Another process holds a real cache descriptor; no mocked file usage.
        import subprocess
        child = subprocess.Popen(['python3', '-c', 'import sys,time; f=open(sys.argv[1]); print("ready",flush=True); time.sleep(30)', str(self.cache / 'package.bin')], stdout=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().strip(), b'ready')
            result = helper.clean([{'id': 'iva_npm', 'fingerprint': row['fingerprint']}])
            self.assertFalse(result['ok']); self.assertIn('используется', result['results'][0]['reason'])
        finally: child.terminate(); child.wait(); child.stdout.close()

    def test_expired_plan_and_uncertain_result_never_replays(self):
        self.analyze(); plan_path = self.root / 'state/plan.json'
        plan = json.loads(plan_path.read_text()); plan['created'] = time.time() - 3600; plan_path.write_text(json.dumps(plan))
        with self.assertRaises(ValueError): self.cleanup()
        self.analyze(); source = self.incoming('Очисти кэш npm')
        with patch.object(helper, 'clean', side_effect=RuntimeError('lost response')): first = self.cleanup(source=source)
        self.assertTrue(first['needs_analysis']); second = self.cleanup(source=source)
        self.assertTrue(second['needs_analysis']); self.assertTrue(self.cache.exists())

    def test_backups_keep_latest_two_and_never_fresh_archives(self):
        folder = self.root / 'backups'; folder.mkdir()
        for day in (1, 2, 3, 4):
            p = folder / f'iva-2026090{day}-030000.tar.gz'; p.write_bytes(b'archive')
            os.utime(p, (time.time() - 20 * 86400,) * 2)
        fresh = folder / 'iva-20260900-030000.tar.gz'; fresh.write_bytes(b'fresh mtime')
        (folder / 'other.tar.gz').write_bytes(b'other')
        eligible = [r['path'] for r in helper.policy().values() if r['group'] == 'backups']
        self.assertEqual(len(eligible), 2)
        self.assertTrue(all('0901-' in p or '0902-' in p for p in eligible))
        self.assertNotIn(str(fresh), eligible)

    def test_replacement_after_rename_restores_without_deleting(self):
        row = helper.candidate(helper.policy()['iva_npm']); original = helper.os.rename
        def replace(source, target, **kwargs):
            original(source, target, **kwargs)
            if target.startswith('.iva-clean-'):
                (self.cache.parent / target / 'new-data').write_bytes(b'new')
        with patch.object(helper.os, 'rename', side_effect=replace):
            result = helper.clean([{'id': 'iva_npm', 'fingerprint': row['fingerprint']}])
        self.assertFalse(result['ok']); self.assertTrue((self.cache / 'new-data').exists())

    def test_partial_removal_is_reported_and_remaining_cache_restored(self):
        row = helper.candidate(helper.policy()['iva_npm'])
        def partial(name, *, dir_fd):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
            try: os.unlink('package.bin', dir_fd=child)
            finally: os.close(child)
            raise OSError('simulated partial failure')
        with patch.object(helper.shutil, 'rmtree', side_effect=partial) as remove:
            remove.avoids_symlink_attacks = True
            result = helper.clean([{'id': 'iva_npm', 'fingerprint': row['fingerprint']}])
        self.assertFalse(result['ok']); self.assertTrue(result['results'][0]['partial_or_unknown'])
        self.assertTrue(self.cache.exists()); self.assertGreater(result['removed_allocated_bytes'], 0)

    def test_scope_of_retry_cannot_expand(self):
        self.analyze(); source = self.incoming('Очисти кэш npm'); self.cleanup(source=source)
        with self.assertRaises(PermissionError): self.cleanup(source=source, candidate_ids=['iva_npm', 'iva_pip'])

    def test_disk_command_bypasses_model(self):
        from local_finance.iva_telegram import IvaAssistant
        class Bridge:
            def send(self, *args): raise AssertionError('Disk command must not call a model')
        with patch('iva_integration.disk_storage.execute', return_value=present({'ok': True, 'disk': helper.usage()})):
            answer = IvaAssistant(self.ledger, Bridge(), cli=object()).handle(OWNER, '/disk', 'diagnostic:read')
        self.assertIn('свободно', answer)

    def test_display_uses_consistent_binary_units(self):
        value = present({'ok': True, 'disk': {'total_bytes': 10*1024**3, 'used_bytes': 8*1024**3,
                         'free_bytes': 2*1024**3, 'used_percent': 80}, 'recoverable_bytes': 256*1024**2})
        self.assertIn('всего 10,00 ГиБ', value['disk']['display'])
        self.assertIn('свободно 2,00 ГиБ', value['disk']['display'])
        self.assertEqual(value['recoverable_display'], '256,00 МиБ')

    def test_mount_and_unreadable_process_checks_fail_closed(self):
        original_text = Path.read_text
        def text(path, *args, **kwargs):
            if str(path) == '/proc/self/mountinfo': return f'1 2 0:1 / {self.cache} rw - tmpfs none rw\n'
            return original_text(path, *args, **kwargs)
        with patch.object(Path, 'read_text', text), self.assertRaises(ValueError): helper.parent_fd(str(self.cache))
        original_bytes = Path.read_bytes
        def read(path):
            if str(path).startswith('/proc/') and path.name == 'cmdline': raise PermissionError('fixture')
            return original_bytes(path)
        with patch.object(Path, 'read_bytes', read):
            row = helper.candidate(helper.policy()['iva_npm'])
        self.assertFalse(row['cleanable']); self.assertIn('проверить', row['blocked_reason'])


if __name__ == '__main__': unittest.main()
