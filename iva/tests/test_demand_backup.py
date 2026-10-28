import contextlib
import io
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from deploy import backup

class ResearchBackup(unittest.TestCase):
    def test_live_wal_research_and_cards_restore_without_source_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';data.mkdir()
            with contextlib.closing(sqlite3.connect(data/'local-finance.sqlite3')) as c:
                c.execute('CREATE TABLE fixture(id INTEGER)');c.execute('INSERT INTO fixture VALUES(7)');c.commit()
            research=sqlite3.connect(data/'demand-research.sqlite3')
            try:
                research.execute('PRAGMA journal_mode=WAL')
                research.execute('CREATE TABLE evidence(id INTEGER,text TEXT)')
                research.execute("INSERT INTO evidence VALUES(1,'Source evidence')");research.commit()
                cards=data/'demand-research/cards';cards.mkdir(parents=True)
                (cards/'one.md').write_text('Hypothesis; not commercial validation',encoding='utf-8')
                with patch.object(backup,'ROOT',root),contextlib.redirect_stdout(io.StringIO()):backup.main()
                archive=next((data/'server-backups').glob('iva-*.tar.gz'))
                with tarfile.open(archive) as tar:
                    self.assertIn('data/demand-research/cards/one.md',tar.getnames())
                    restored=root/'restored.sqlite3'
                    restored.write_bytes(tar.extractfile('data/demand-research.sqlite3').read())
                with contextlib.closing(sqlite3.connect(restored)) as c:
                    self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                    self.assertEqual(c.execute('SELECT * FROM evidence').fetchall(),[(1,'Source evidence')])
                self.assertEqual(research.execute('SELECT count(*) FROM evidence').fetchone()[0],1)
            finally:research.close()

if __name__=='__main__':unittest.main()
