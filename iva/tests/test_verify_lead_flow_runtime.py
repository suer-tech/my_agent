from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy import verify_lead_flow_runtime as audit


class RuntimeAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'iva_integration/reviewed.py'
        self.source.parent.mkdir()
        self.source.write_text('reviewed source')
        self.expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.manifest = self.root/'manifest.json'
        self.manifest.write_text(json.dumps([{'target':str(self.source),'sha256':self.expected}]))
        self.root_patch = patch.object(audit,'ROOT',self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_reviewed_hashes_detect_changed_source_without_updating_expectations(self):
        report = {'checks':[]}
        audit.verify_sources(report,[self.manifest])
        self.assertTrue(report['checks'][0]['passed'])
        self.source.write_text('unreviewed change')
        report = {'checks':[]}
        audit.verify_sources(report,[self.manifest])
        self.assertFalse(report['checks'][0]['passed'])
        self.assertEqual(report['checks'][0]['expected'],self.expected)
        self.assertEqual(json.loads(self.manifest.read_text())[0]['sha256'],self.expected)

    def test_secret_files_and_traversal_are_not_hashed(self):
        for name in ('auth.json','.env','../../private.py'):
            self.manifest.write_text(json.dumps([{'target':str(self.source.parent/name),'sha256':self.expected}]))
            with self.subTest(name=name), self.assertRaises(ValueError):
                audit.expected_hashes([self.manifest])

    def test_conflicting_manifests_fail_instead_of_silently_trusting_new_hash(self):
        another = self.root/'another.json'
        another.write_text(json.dumps({str(self.source):'f'*64}))
        with self.assertRaisesRegex(ValueError,'conflicting'):
            audit.expected_hashes([self.manifest,another])

    def test_only_exact_research_reader_helper_is_allowed(self):
        self.assertTrue(audit.source_allowed(Path('/usr/local/lib/iva-research-fetch/server.py')))
        for name in ('server.py.bak','auth.json','private.env','other.py'):
            with self.subTest(name=name):
                self.assertFalse(audit.source_allowed(Path('/usr/local/lib/iva-research-fetch')/name))
        self.assertFalse(audit.source_allowed(Path('/usr/local/lib/other/server.py')))
        self.assertTrue(audit.source_allowed(Path('/usr/local/lib/lead-flow-crm/request.py')))
        self.assertFalse(audit.source_allowed(Path('/usr/local/lib/lead-flow-crm/token')))
        self.assertFalse(audit.source_allowed(Path('/etc/lead-flow/crm-agent.token')))

    def test_general_schedule_read_does_not_migrate_or_modify_database(self):
        database = self.root/'data/local-finance.sqlite3'
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as db:
            db.execute('CREATE TABLE scheduled_jobs(id,state)')
            db.execute('INSERT INTO scheduled_jobs VALUES(?,?)',(audit.GENERAL_JOB,'paused'))
            db.commit()
        before = database.read_bytes()
        self.assertEqual(audit.general_schedule(),{'id':audit.GENERAL_JOB,'state':'paused'})
        self.assertEqual(database.read_bytes(),before)

    def test_missing_financial_database_is_not_created(self):
        (self.root/'data').mkdir()
        with self.assertRaises(sqlite3.OperationalError):
            audit.general_schedule()
        self.assertFalse((self.root/'data/local-finance.sqlite3').exists())

    def test_report_remains_failed_when_runtime_is_healthy_but_source_changed(self):
        self.source.write_text('changed')
        with patch.object(audit,'_runtime',side_effect=lambda report,*args:audit.check(report,'runtime_stub',True)):
            report = audit.collect([self.manifest])
        self.assertFalse(report['ok'])
        self.assertEqual(report['model_calls'],0)
        self.assertEqual(report['emails_sent'],0)

    def test_service_audit_uses_show_and_never_control_commands(self):
        response = SimpleNamespace(stdout='Id=iva@gateway.service\nActiveState=active\n\nId=lead-flow-research.timer\nActiveState=inactive\n')
        with patch.object(audit.subprocess,'run',return_value=response) as command:
            states = audit.service_states()
        self.assertEqual(command.call_args.args[0][1],'show')
        self.assertEqual(states['lead-flow-research.timer']['ActiveState'],'inactive')

    def test_report_destination_cannot_escape_private_release(self):
        with patch.object(audit,'RELEASE',self.root):
            with self.assertRaises(ValueError):
                audit.save_private({},'../outside.json')
        self.assertFalse((self.root.parent/'outside.json').exists())


if __name__ == '__main__':
    unittest.main()
