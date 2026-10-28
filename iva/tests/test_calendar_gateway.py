import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from iva_integration import gateway
from local_finance.assistant import Assistant, Ledger
from local_finance.bot import OWNER
from local_finance.personal_calendar import Calendar


class CalendarGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'data/iva-telegram').mkdir(parents=True)
        self.path = self.root / 'data/local-finance.sqlite3'
        self.source = f'telegram:{OWNER}:900:0'
        self.session = self.root / 'data/iva-telegram/session.json'
        self.session.write_text(json.dumps({'active_source': self.source}))
        self.body = 'термоголовку еще не купил, добавь в напорминание к термоголовке купить еще и фильтры для воды'
        self.message = {'from': {'id': OWNER}, 'chat': {'id': OWNER, 'type': 'private'}, 'text': self.body}
        ledger = Ledger(self.path)
        Assistant(ledger, None)
        ledger.db.execute('INSERT INTO local_telegram_messages VALUES(900,900,?)', (json.dumps(self.message),))
        ledger.db.execute("INSERT INTO local_updates(id,chat,text,metadata,state) VALUES(900,?,?,'{}','processing')", (OWNER, self.body))
        ledger.db.commit()
        ledger.db.close()
        with self.calendar() as store:
            self.event = store.execute({'action': 'add', 'title': 'Купить термоголовку', 'at': '2026-10-20T11:04:36+05:00', 'repeat': 'daily'}, 'fixture')['event']
        self.root_patch = patch.object(gateway, 'ROOT', self.root)
        self.root_patch.start()
        self.server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        self.server.config = {'agent_token': 'test-agent', 'owner_token': 'test-owner'}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.root_patch.stop()
        self.tmp.cleanup()

    def calendar(self):
        from contextlib import closing
        return closing(Calendar(self.path))

    def request(self, token='test-agent', **data):
        payload = {'action': 'update', 'id': self.event['id'], 'title': 'Купить термоголовку и фильтры для воды', **data}
        request = Request(f'http://127.0.0.1:{self.server.server_port}/calendar', json.dumps(payload).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def save_message(self, **extra):
        ledger = Ledger(self.path)
        ledger.db.execute('UPDATE local_telegram_messages SET message_json=? WHERE id=900', (json.dumps({**self.message, **extra}),))
        ledger.db.commit()
        ledger.db.close()

    def assert_rejected(self):
        self.assertFalse(self.request()['ok'])
        with self.calendar() as store:
            self.assertEqual(store.rows('SELECT title FROM personal_events')[0]['title'], self.event['title'])
            self.assertEqual(store.rows('SELECT * FROM personal_mutations WHERE source=?', (self.source,)), [])

    def test_exact_typo_request_updates_same_event_and_preserves_schedule(self):
        result = self.request()
        self.assertTrue(result['ok'])
        event = result['event']
        self.assertEqual(event['title'], 'Купить термоголовку и фильтры для воды')
        for key in ('id', 'at', 'repeat', 'kind', 'leads', 'ack', 'created'):
            self.assertEqual(event[key], self.event[key])
        self.assertTrue(self.request()['duplicate'])
        with self.calendar() as store:
            self.assertEqual(len(store.rows('SELECT * FROM personal_events')), 1)

    def test_request_without_calendar_keywords_is_accepted(self):
        self.save_message(text='Добавь ещё фильтры для воды')
        self.assertTrue(self.request()['ok'])

    def test_completed_message_is_rejected(self):
        ledger = Ledger(self.path)
        ledger.db.execute("UPDATE local_updates SET state='done' WHERE id=900")
        ledger.db.commit()
        ledger.db.close()
        self.assert_rejected()

    def test_missing_active_source_is_rejected(self):
        self.session.write_text(json.dumps({'active_source': None}))
        self.assert_rejected()

    def test_foreign_sender_is_rejected(self):
        self.save_message(**{'from': {'id': 123}})
        self.assert_rejected()

    def test_forwarded_message_is_rejected(self):
        self.save_message(forward_origin={'type': 'user'})
        self.assert_rejected()

    def test_document_is_rejected(self):
        self.save_message(document={'file_id': 'fixture'})
        self.assert_rejected()

    def test_invalid_gateway_token_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request(token='wrong')
        self.assertEqual(error.exception.code, 401)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
