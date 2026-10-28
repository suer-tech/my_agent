import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from iva_integration import threads_hourly as w
from iva_integration.threads_page import working_page


class Repair(unittest.TestCase):
    def test_empty_search_is_not_proof_editor_or_ai_recovered(self):
        from iva_integration.threads_alerts import recovery_proven
        self.assertFalse(recovery_proven('interface',{'state':'done'},{},100))
        self.assertTrue(recovery_proven('interface',{'state':'done'},{'verified_at':101},100))
        self.assertFalse(recovery_proven('ai',{'state':'done','inspected':0},{},100))
        self.assertTrue(recovery_proven('ai',{'state':'done','inspected':1},{},100))
    def test_lexical_filter_keeps_requests_and_discards_unrelated_feed(self):
        self.assertTrue(w.likely_relevant('Ищу разработчика Python для AI агента','development'))
        self.assertFalse(w.likely_relevant('Доброе утро! Молитва и хорошее настроение','development'))
        self.assertTrue(w.likely_relevant('На hh нет ответов на отклики, что посоветуете?','extension'))
        self.assertFalse(w.likely_relevant('Новый ковёр и кот','extension'))
    def test_explicit_advice_request_precedes_plain_job_ad(self):
        self.assertGreater(w.extension_priority('Подскажите, как искать работу на hh, устала писать сопроводительные'),w.extension_priority('Ищу работу SMM срочно'))
    def test_restored_extra_tabs_do_not_break_worker(self):
        class Page:
            url='https://www.threads.com/'
            def is_closed(self):return False
        class Context:pages=[Page(),Page()]
        ctx=Context();self.assertIs(working_page(ctx),ctx.pages[-1]);self.assertIs(working_page(ctx),ctx.pages[-1])
    def test_preflight_failure_is_distinct_from_lost_send_ack(self):
        for submitted,expected in [(False,'preflight_failed'),(True,'uncertain')]:
            with self.subTest(submitted=submitted),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);url='https://www.threads.com/@owner/post/example'
                w.write('hourly-launch.json',{'enabled':True,'pending':True,'publish':True},root=root)
                db=w.db_open(root)
                with db:
                    db.execute("INSERT INTO cycles(started,state,source_index) VALUES (?,'active',5)",(time.time(),))
                    db.execute("INSERT INTO posts(url,cycle_id,published,state) VALUES (?,1,?,'new')",(url,time.time()))
                db.close()
                class Page:
                    url='https://www.threads.com/@owner/post/example'
                    def goto(self,*a,**kw):pass
                    def wait_for_timeout(self,*a):pass
                    def wait_for_function(self,*a,**kw):pass
                def publish(*args,**kwargs):
                    if submitted:kwargs['before_submit']()
                    raise RuntimeError('Lost acknowledgement' if submitted else 'No editor')
                config={**w.default_config(),'root':root,'decide':lambda s:{'fit':True,'reason':'match','comment':'Я делаю расширение для откликов. '+w.STORE}}
                with patch('iva_integration.threads_page.working_page',return_value=Page()),patch.object(w,'signed_in',return_value=True),patch.object(w,'require_session'),patch.object(w,'canonical',return_value=url),patch.object(w,'candidate_cards',return_value=[{'url':url,'published':time.time()}]),patch.object(w,'known_reply',return_value=False),patch('iva_integration.threads_browser_rpc.snapshot',return_value={'links':[]}),patch.object(w,'publish',side_effect=publish),patch.object(w,'finish'),patch('iva_integration.runtime_diagnostics.event'):
                    w.step(object(),config=config)
                db=w.db_open(root);row=db.execute('SELECT state,attempt_at FROM posts').fetchone()
                self.assertEqual(row['state'],expected);self.assertEqual(row['attempt_at'] is not None,submitted);db.close()


if __name__=='__main__':unittest.main()
