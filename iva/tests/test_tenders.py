import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from iva_integration.tender_eis import Store,search_url,valid_url,parse_page,parse_document
from iva_integration.tender_agent import run


class TendersTests(unittest.TestCase):
    def test_search_pagination_and_history_separation(self):
        a=parse_qs(urlsplit(search_url('разработка сайта','active',3)).query)
        self.assertEqual(a['pageNumber'],['3']);self.assertEqual(a['af'],['on'])
        self.assertNotIn('af',parse_qs(urlsplit(search_url('разработка сайта','history')).query))
        self.assertIn('/contract/',search_url('123','contracts'))
        self.assertEqual(parse_qs(urlsplit(search_url('x','active',31)).query)['pageNumber'],['31'])
        for page in (0,-1,True,1.5):
            with self.assertRaises(ValueError):search_url('x','active',page)

    def test_no_arbitrary_hosts(self):
        for url in ['http://zakupki.gov.ru','https://127.0.0.1','https://zakupki.gov.ru.evil.test','https://user@zakupki.gov.ru','https://zakupki.gov.ru:8000']:
            with self.assertRaises(ValueError):valid_url(url)

    def test_cards_preserve_evidence_not_invent_missing(self):
        html='<html><title>ЕИС</title><div class="search-registry-entry-block"><div class="registry-entry__header-mid__number"><a href="/epz/order/notice/view.html?regNumber=123">№ 123</a></div><div class="registry-entry__body-value">Доработка сайта</div><div class="price-block__value">500 000,00 ₽</div>Окончание подачи заявок 01.11.2026 10:00</div></html>'
        result=parse_page(html,'https://zakupki.gov.ru/')
        self.assertEqual(result['cards'][0]['price_text'],'500 000,00 ₽')
        self.assertIn('01.11.2026',result['cards'][0]['evidence_text'])
        self.assertIsNone(result['cards'][0]['customer'])
        self.assertFalse(parse_page('<html>Технические работы</html>','https://zakupki.gov.ru')['not_found_verified'])
        self.assertFalse(parse_page('<html>Access denied</html>','https://zakupki.gov.ru')['ok'])

    def test_network_failure_and_cache_not_empty_success(self):
        store=Store(':memory:')
        with patch('iva_integration.tender_eis.valid_url'),patch('iva_integration.tender_eis.fetch',side_effect=TimeoutError):
            result=store.read('https://zakupki.gov.ru/')
        self.assertFalse(result['ok']);self.assertFalse(result['not_found_verified'])
        with patch('iva_integration.tender_eis.valid_url'),patch('iva_integration.tender_eis.fetch',return_value=('<html>Сведения о контракте</html>','https://zakupki.gov.ru/')):
            store.read('https://zakupki.gov.ru/')
            self.assertTrue(store.read('https://zakupki.gov.ru/',False)['must_refresh_before_bid'])
        store.close()

    def test_feedback_requires_owner_and_is_idempotent(self):
        store=Store(':memory:')
        with self.assertRaises(PermissionError):store.feedback('test','','подсказка сайта','Не искать сайты')
        store.feedback('test','Не хочу выезжать','Не хочу выезжать','Удалённо','preference')
        self.assertTrue(store.feedback('test','Не хочу выезжать','Не хочу выезжать','Удалённо','preference')['duplicate'])
        profile=store.profile()
        self.assertEqual(len(profile['preferences']),1)
        store.feedback('test','Не хочу выезжать','Не хочу выезжать','В другой регион не поеду','preference')
        self.assertEqual(len(store.profile()['preferences']),2)
        self.assertEqual(profile['profile']['bid_security_cash_limit_rub'],20000)
        store.close()

    def test_docx_paragraph_provenance_and_unsupported_zip(self):
        import io,zipfile
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Лицензии не требуются</w:t></w:r></w:p></w:document>')
        result=parse_document(data.getvalue(),'https://zakupki.gov.ru/doc')
        self.assertEqual(result['pages'][0]['paragraph'],1)
        self.assertIn('Лицензии',result['pages'][0]['text'])

    def test_specialist_feedback_routing_and_retry(self):
        class Cli:
            model='gpt-6.1-sol';reasoning_effort='medium';last_usage={};count=0
            def structured(self,prompt,schema,images=()):
                self.count+=1
                if self.count==1:return {'text':'','calls':[{'name':'feedback','arguments_json':json.dumps({'note':'Только удалённо','evidence':'Только удалённо','scope':'preference','tender_url':None})}]}
                return {'text':'Учёл предпочтение','calls':[]}
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'tenders.sqlite3';cli=Cli()
            result=run('Только удалённо','test:owner',db,cli,'Только удалённо')
            self.assertTrue(result['ok'])
            self.assertTrue(run('Только удалённо','test:owner',db,cli,'Только удалённо')['cached'])
            self.assertEqual(cli.count,2)

    def test_read_budget_forces_final_schema_without_more_tools(self):
        class Cli:
            model='gpt-6.1-sol';reasoning_effort='medium';last_usage={};count=0
            def structured(self,prompt,schema,images=()):
                self.count+=1
                if self.count==1:
                    return {'text':'','calls':[{'name':'search','arguments_json':json.dumps({'query':'сайт','mode':'active','page':i})} for i in range(1,9)]}
                assert schema['properties']['calls']['maxItems']==0
                assert 'Бюджет чтений исчерпан' in prompt
                return {'text':'Проверенная выборка, документы не проверены','calls':[]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(Store,'read',return_value={'ok':True,'cards':[]}) as read:
            result=run('Найди тендеры','test:budget',Path(tmp)/'db.sqlite3',Cli())
            self.assertTrue(result['ok'])
            self.assertEqual(read.call_count,8)


if __name__=='__main__':unittest.main()
