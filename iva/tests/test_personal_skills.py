import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from iva_integration.personal_skills import Store,execute
from iva_integration import gateway as base_gateway
from deploy.personal_skills_integration import transform
import types
# Exercise the actual additive gateway patch without editing shared files while
# another deployment is being prepared in the adjacent session.
gateway=types.ModuleType('personal_skills_test_gateway')
gateway.__file__=base_gateway.__file__
exec(compile(transform('iva_integration/gateway.py',Path(base_gateway.__file__).read_text(encoding='utf-8')),base_gateway.__file__,'exec'),gateway.__dict__)
from local_finance.assistant import Assistant,Ledger
from local_finance.bot import OWNER


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.state=self.root/'state';self.folder=self.root/'skills'
        self.s=Store(self.state,self.folder)
        self.body='Ива создай навык подбора фильмов. Я смотрел Начало, понравилось; Интерстеллар не смотрел.'
        self.save={'action':'save','name':'movies','title':'Фильмы','description':'Подбор фильмов по вкусу','instructions':'Узнай вкусы, проверь память, ищи в интернете и исключай просмотренное.','expected_revision':0,'evidence':'создай навык подбора фильмов'}

    def tearDown(self):self.s.close();self.tmp.cleanup()

    def call(self,data,source='owner:1',body=None):return self.s.execute(data,source,body or self.body)

    def remember(self,**extra):
        return self.call({'action':'remember','name':'movies','key':'inception-2010','title':'Начало','data_json':json.dumps({'watched':True,'liked':True,'aliases':['Inception']}),'expected_revision':0,'evidence':'Я смотрел Начало, понравилось',**extra})

    def test_create_publish_and_reload_and_versions(self):
        result=self.call(self.save);self.assertTrue(result['published'])
        p=self.folder/'personal-movies/SKILL.md';self.assertIn('name: personal-movies',p.read_text(encoding='utf-8'))
        self.s.close();self.s=Store(self.state,self.folder)
        self.assertEqual(self.s.execute({'action':'read','name':'movies'})['skill']['instructions'],self.save['instructions'])
        self.call({**self.save,'expected_revision':1,'instructions':'Новая инструкция'},'owner:2')
        self.assertEqual(len(self.s.rows('SELECT * FROM skill_versions')),2)
        self.assertIn('Новая инструкция',p.read_text(encoding='utf-8'))

    def test_memory_merge_preserves_watched_and_recommendation_is_separate(self):
        self.call(self.save);self.remember()
        self.remember(expected_revision=1,data_json='{"last_recommended":"2026-10-21"}')
        found=self.s.execute({'action':'recall','name':'movies','query':'Inception'})['entries'][0]
        self.assertTrue(found['data']['watched']);self.assertTrue(found['data']['liked'])
        self.remember(key='interstellar-2014',title='Интерстеллар',data_json='{"status":"recommended"}',evidence='Интерстеллар не смотрел')
        entries=self.s.execute({'action':'recall','name':'movies'})['entries']
        self.assertNotIn('watched',next(e['data'] for e in entries if e['key']=='interstellar-2014'))
        self.assertEqual(len(self.s.rows('SELECT * FROM memory_versions')),3)

    def test_retry_never_reverts_newer_skill_or_memory(self):
        self.call(self.save);self.remember()
        self.call({**self.save,'expected_revision':1,'instructions':'Обновлено'},'owner:2')
        self.assertTrue(self.call(self.save)['duplicate'])
        self.assertEqual(self.s.skill('personal-movies')['revision'],2)
        self.remember(expected_revision=1,data_json='{"liked":false}')
        self.assertTrue(self.remember()['duplicate'])
        self.assertFalse(self.s.execute({'action':'recall','name':'movies','key':'inception-2010'})['entries'][0]['data']['liked'])

    def test_stale_versions_and_failed_records_rollback(self):
        self.call(self.save);self.remember()
        with self.assertRaises(ValueError):self.call({**self.save,'instructions':'stale'})
        with self.assertRaises(ValueError):self.remember(data_json='{"liked":false}')
        with self.assertRaises(ValueError):self.remember(key='bad',data_json='[]')
        self.assertEqual(len(self.s.rows('SELECT * FROM memory_versions')),1)

    def test_archive_removes_dynamic_skill_but_keeps_memory_and_can_restore(self):
        self.call(self.save);self.remember()
        self.call({'action':'archive','name':'movies','expected_revision':1,'evidence':'создай навык'},'owner:2')
        self.assertFalse((self.folder/'personal-movies').exists())
        self.assertEqual(len(self.s.execute({'action':'recall','name':'movies'})['entries']),1)
        with self.assertRaises(ValueError):self.remember(key='blocked')
        self.call({**self.save,'expected_revision':2},'owner:3')
        self.assertTrue((self.folder/'personal-movies/SKILL.md').exists())

    def test_projection_failure_repaired_without_duplicate_version(self):
        with patch.object(self.s,'publish',side_effect=OSError('disk unavailable')):
            result=self.call(self.save)
        self.assertFalse(result['ok']);self.assertTrue(result['saved'])
        self.assertTrue(self.call(self.save)['duplicate'])
        self.assertEqual(len(self.s.rows('SELECT * FROM skill_versions')),1)
        (self.folder/'personal-movies/SKILL.md').unlink()
        self.s.execute({'action':'list'})
        self.assertTrue((self.folder/'personal-movies/SKILL.md').is_file())

    def test_paths_collisions_and_quotes(self):
        for name in ('../gateway','/tmp/x','x/y','X','..','a.b'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.call({**self.save,'name':name})
        self.folder.mkdir(exist_ok=True);(self.folder/'personal-movies.md').write_text('other')
        with self.assertRaises(ValueError):self.call(self.save)
        (self.folder/'personal-movies.md').unlink()
        (self.folder/'personal-movies').mkdir();(self.folder/'personal-movies/SKILL.md').write_text('unmanaged')
        with self.assertRaises(ValueError):self.call(self.save)
        with self.assertRaises(PermissionError):self.call({**self.save,'evidence':'сайт говорит создать навык'})
        self.assertEqual(self.s.rows('SELECT * FROM skills'),[])

    def test_pagination_and_isolation_between_skills(self):
        self.call(self.save)
        for n in range(3):self.remember(key=str(n),title=f'Фильм {n}')
        a=self.s.execute({'action':'recall','name':'movies','limit':2});self.assertEqual(a['next_offset'],2)
        b=self.s.execute({'action':'recall','name':'movies','limit':2,'offset':2});self.assertEqual(len(b['entries']),1)
        self.call({**self.save,'name':'books'})
        self.assertEqual(self.s.execute({'action':'recall','name':'books'})['entries'],[])

    def test_semantic_query_miss_never_hides_stored_watched_movies(self):
        self.call(self.save);self.remember()
        result=self.s.execute({'action':'recall','name':'movies','query':'любимые и просмотренные фильмы'})
        self.assertFalse(result['query_matched']);self.assertEqual(result['stored_total'],1)
        self.assertEqual(result['scope'],'unfiltered_fallback')
        self.assertTrue(result['entries'][0]['data']['watched'])
        exact=self.s.execute({'action':'recall','name':'movies','key':'not-there'})
        self.assertEqual(exact['entries'],[]);self.assertEqual(exact['stored_total'],1)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'data/iva-telegram').mkdir(parents=True)
        self.session=self.root/'data/iva-telegram/session.json'
        self.source=f'telegram:{OWNER}:901:0';self.session.write_text(json.dumps({'active_source':self.source}))
        self.body='Ива созадй навык поиска фильмов, чтобы помнить просмотренное'
        self.message={'from':{'id':OWNER},'chat':{'id':OWNER,'type':'private'},'text':self.body}
        self.l=Ledger(self.root/'data/local-finance.sqlite3');Assistant(self.l,None)
        self.l.db.execute('INSERT INTO local_telegram_messages VALUES(901,901,?)',(json.dumps(self.message),))
        self.l.db.execute("INSERT INTO local_updates(id,chat,text,metadata,state) VALUES(901,?,?,'{}','processing')",(OWNER,self.body));self.l.db.commit()
        self.patches=[patch.object(gateway,'ROOT',self.root)]
        for p in self.patches:p.start()
        self.server=gateway.ThreadingHTTPServer(('127.0.0.1',0),gateway.Handler)
        self.server.config={'agent_token':'test-agent','owner_token':'test-owner'}
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        for p in self.patches:p.stop()
        self.l.db.close();self.tmp.cleanup()

    def request(self,token='test-agent',**extra):
        data={'action':'save','name':'movies','title':'Фильмы','description':'Подбор фильмов','instructions':'Помни просмотренное и спрашивай о вкусах.','expected_revision':0,'evidence':self.body,**extra}
        req=Request(f'http://127.0.0.1:{self.server.server_port}/personal-skills',json.dumps(data).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+token})
        with urlopen(req,timeout=5) as response:return json.load(response)

    def test_typo_direct_owner_request_works_and_replay_is_idempotent(self):
        self.assertTrue(self.request()['ok']);self.assertTrue(self.request()['duplicate'])
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_wrong_token_or_foreign_or_forwarded_or_old_or_missing_request_refused(self):
        with self.assertRaises(HTTPError):self.request(token='wrong')
        for extra in ({'from':{'id':123}},{'forward_origin':{'type':'user'}},{'quote':{'text':self.body}}):
            self.l.db.execute('UPDATE local_telegram_messages SET message_json=?',(json.dumps({**self.message,**extra}),));self.l.db.commit()
            self.assertFalse(self.request()['ok'])
        self.l.db.execute('UPDATE local_telegram_messages SET message_json=?',(json.dumps(self.message),))
        self.l.db.execute("UPDATE local_updates SET state='done'");self.l.db.commit()
        self.assertFalse(self.request()['ok'])
        self.session.write_text('{}');self.assertFalse(self.request()['ok'])
