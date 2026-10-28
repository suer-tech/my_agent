import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
import zipfile

from iva_integration.tender_research import (Research, TRIAGE, EXTRACT, REVIEW,
    document_links,read_resource,split_text,notice_number)
from iva_integration.tender_eis import parse_page
from bs4 import BeautifulSoup


class FakeModel:
    def structured(self,prompt,schema):
        if schema==TRIAGE:
            values=json.loads(prompt.split('\n')[-1])
            return {'decisions':[{'number':v['number'],'relevant':True,'reason':'Разработка'} for v in values]}
        if schema==EXTRACT:
            role='contract' if 'contract-doc' in prompt else 'specification'
            return {'summary':'Проверенный фрагмент','findings':['Пункт 1: требуется разработка'],'document_role':role}
        if schema==REVIEW:
            return {'decision':'priority','score':90,'reason':'Подходит','admission_status':'eligible',
                    'execution_status':'feasible','platform_fee_verified':True,'blockers':[],
                    'facts':['Разработка приложения'],'next_action':'Сверить актуальный статус'}
        raise AssertionError(schema)


def card(number='12345678901'):
    return {'number':'№ '+number,'url':'https://zakupki.gov.ru/epz/order/notice/view.html?regNumber='+number,
            'title':'Разработка приложения','price_text':'100 000 RUB','deadline_text':'01.11.2026'}


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.r=Research(self.root/'test.sqlite3',self.root/'artifacts')
    def tearDown(self):self.r.close();self.tmp.cleanup()
    def start(self,queries=None,source='owner:1'):
        return self.r.start('Сайты, ИИ, чат-боты: все страницы и документы',source,queries or ['разработка'],False)['job_id']
    def drain(self,reader,limit=100):
        for _ in range(limit):
            if not self.r.step(FakeModel(),reader):return
        self.fail('Research did not finish')

    def test_all_31_pages_restart_dedup_and_exact_provenance(self):
        job=self.start(['сайт','ИИ'])
        visited=[]
        def reader(url):
            q=parse_qs(urlsplit(url).query);page=int(q['pageNumber'][0]);query=q['searchString'][0]
            visited.append((query,page))
            if page==32:return {'ok':True,'cards':[],'not_found_verified':True}
            return {'ok':True,'cards':[card(str(12345678900+page))], 'pagination':{'end_verified':False,'next_page':page+10}}
        for _ in range(12):self.r.step(FakeModel(),reader)
        self.r.close();self.r=Research(self.root/'test.sqlite3',self.root/'artifacts')
        for _ in range(52):self.r.step(FakeModel(),reader)
        self.assertEqual(len(visited),64)
        self.assertEqual([p for q,p in visited if q=='сайт'],list(range(1,33)))
        status=self.r.status(job)
        self.assertEqual(status['queries'],{'done':2});self.assertEqual(status['pages'],64)
        self.assertEqual(status['notices'],{'triage':31})
        origins=json.loads(self.r.db.execute('SELECT provenance FROM research_notices LIMIT 1').fetchone()[0])
        self.assertEqual(len(origins),2)
        self.assertEqual(self.r.start('Перефразированное поручение','owner:1',['другое'],False)['job_id'],job)

    def test_repeat_and_unknown_empty_are_gaps_not_completion(self):
        job=self.start(['повтор','сбой'])
        def reader(url):
            query=parse_qs(urlsplit(url).query)['searchString'][0]
            if query=='сбой':return {'ok':True,'cards':[]}
            return {'ok':True,'cards':[card()]}
        for _ in range(3):self.r.step(FakeModel(),reader)
        status=self.r.status(job)
        self.assertEqual(status['queries'],{'gap':2});self.assertEqual(len(status['coverage_gaps']),2)

    def test_full_pipeline_docs_last_chunk_gate_and_outbox(self):
        job=self.r.start('Все документы','owner:notify',['ИИ'],True)['job_id']
        def reader(url):
            if 'results.html' in url:return {'ok':True,'cards':[card()],'pagination':{'end_verified':True}}
            if 'contract-doc' in url:return {'ok':True,'chunks':[{'text':'ТЗ и контракт','location':'стр. 1'}]}
            return {'ok':True,'chunks':[{'text':'ТЗ','location':'HTML'}],
                    'documents':[{'url':'https://zakupki.gov.ru/contract-doc','title':'Контракт','kind':'document'}]}
        self.drain(reader)
        status=self.r.status(job)
        self.assertEqual(status['state'],'completed')
        self.assertEqual(status['chunks_total'],status['chunks_analyzed'])
        self.assertEqual(status['notices'],{'reviewed':1})
        manifest=json.loads((self.root/'artifacts'/job/'manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(manifest['audit']['ok']);self.assertEqual(manifest['audit']['exit_code'],0)
        report=self.r.control('report',job)['report']
        self.assertIn('priority',report);self.assertIn('12345678901',report)
        class Telegram:
            calls=[]
            def send(self,*args):self.calls.append('summary');return {'message_id':1}
            def send_document(self,*args):self.calls.append('document');return {'message_id':2}
        tg=Telegram()
        self.assertTrue(self.r.deliver_one(tg,1));self.assertTrue(self.r.deliver_one(tg,1))
        self.assertFalse(self.r.deliver_one(tg,1));self.assertEqual(tg.calls,['summary','document'])

    def test_unread_document_prevents_priority_and_resume_preserves_progress(self):
        job=self.start()
        def reader(url):
            if 'results.html' in url:return {'ok':True,'cards':[card()],'pagination':{'end_verified':True}}
            if 'unread' in url:return {'ok':True,'chunks':[],'gap':'Скан: нужен OCR'}
            return {'ok':True,'chunks':[{'text':'ТЗ','location':'HTML'}],
                    'documents':[{'url':'https://zakupki.gov.ru/unread','title':'Приложение','kind':'document'}]}
        self.drain(reader)
        self.assertEqual(self.r.status(job)['state'],'completed_with_gaps')
        review=json.loads(self.r.db.execute('SELECT review FROM research_notices').fetchone()[0])
        self.assertEqual(review['decision'],'conditional');self.assertEqual(review['document_review'],'partial')
        self.r.control('resume',job)
        self.assertEqual(self.r.status(job)['pages'],1)
        self.assertEqual(self.r.status(job)['documents']['pending'],2)

    def test_amended_resource_replaces_old_text_and_analysis(self):
        job=self.start()
        def reader(url):
            if 'results.html' in url:return {'ok':True,'cards':[card()],'pagination':{'end_verified':True}}
            return {'ok':True,'sha256':'old','chunks':[{'text':'Старые условия','location':'ТЗ'}]}
        self.drain(reader)
        self.assertEqual(self.r.status(job)['state'],'completed_with_gaps')
        self.r.control('resume',job)
        self.r.step(FakeModel(),reader) # completed discovery advances to review
        self.r.step(FakeModel(),lambda u:{'ok':True,'sha256':'new','chunks':[{'text':'Новая редакция','location':'ТЗ'}]})
        chunk=self.r.db.execute('SELECT text,analysis FROM research_chunks').fetchone()
        self.assertEqual(chunk['text'],'Новая редакция');self.assertIsNone(chunk['analysis'])

    def test_captcha_pause_retry_resume_cancel(self):
        job=self.start()
        self.r.step(FakeModel(),lambda u:{'ok':False,'access_challenge':True})
        self.assertEqual(self.r.status(job)['state'],'paused')
        self.assertEqual(self.r.status(job)['pages'],0)
        self.r.control('resume',job)
        self.assertEqual(self.r.status(job)['state'],'queued')
        self.r.control('cancel',job)
        self.assertFalse(self.r.step(FakeModel(),lambda u:self.fail('cancelled work executed')))

    def test_model_failure_does_not_lose_unread_chunk(self):
        job=self.start()
        reader=lambda u:({'ok':True,'cards':[card()],'pagination':{'end_verified':True}} if 'results.html' in u else {'ok':True,'chunks':[{'text':'ТЗ','location':'стр1'}]})
        for _ in range(4):self.r.step(FakeModel(),reader)
        class Broken:
            def structured(self,*args):raise TimeoutError('private token must not be logged')
        self.r.step(Broken(),reader)
        j=self.r.job(job)
        self.assertEqual(j['failures'],1);self.assertEqual(j['error'],'TimeoutError')
        self.assertGreater(j['retry_at'],0)
        self.assertEqual(self.r.status(job)['chunks_analyzed'],0)

    def test_uncertain_delivery_is_not_repeated(self):
        job=self.start()
        with self.r.db:
            self.r.db.execute("UPDATE research_jobs SET state='completed' WHERE id=?",(job,))
            self.r.db.execute("INSERT INTO research_outbox(job,kind) VALUES(?,'summary:1')",(job,))
        class Broken:
            def send(self,*args):raise TimeoutError()
        self.assertTrue(self.r.deliver_one(Broken(),1));self.assertFalse(self.r.deliver_one(Broken(),1))
        self.assertEqual(self.r.status(job)['delivery'],{'uncertain':1})

    def test_incomplete_triage_never_silently_omits_notice(self):
        job=self.start()
        self.r.step(reader=lambda u:{'ok':True,'cards':[card()],'pagination':{'end_verified':True}})
        self.r.step()
        class Missing:
            def structured(self,*args):return {'decisions':[]}
        self.r.step(Missing())
        self.assertEqual(self.r.status(job)['notices'],{'triage':1})
        self.assertEqual(self.r.job(job)['failures'],1)

    def test_document_inventory_no_navigation_and_full_long_docx(self):
        html='''<a href='/epz/main/public/document/view.html?sectionId=1'>Закон</a>
        <a href='/epz/order/notice/documents.html?regNumber=12345678901'>Документы</a>
        <a href='/223/download/file.html?id=1'>ТЗ</a><a href='https://evil.test/secret'>Нет</a>'''
        links=document_links(BeautifulSoup(html,'html.parser'),'https://zakupki.gov.ru/card')
        self.assertEqual(len(links),2)
        content=('Начало ТЗ '+('требование '*20000)+' ПОСЛЕДНИЙ ПУНКТ').encode()
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('word/document.xml',b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>'+content+b'</w:t></w:r></w:p></w:document>')
        with patch('iva_integration.tender_research.valid_url'),patch('iva_integration.tender_research.fetch',return_value=(data.getvalue(),'https://zakupki.gov.ru/doc')):
            result=read_resource('https://zakupki.gov.ru/doc',self.root/'originals')
        self.assertNotIn('gap',result)
        self.assertIn('ПОСЛЕДНИЙ ПУНКТ',result['chunks'][-1]['text'])
        self.assertEqual(gzip.open(result['original_path'],'rb').read(),data.getvalue())

    def test_more_than_twenty_exposed_cards_not_silently_cut(self):
        entry=lambda i:f'<div class="search-registry-entry-block"><a href="/view?regNumber={12345678900+i}">{12345678900+i}</a></div>'
        result=parse_page('<div class="paginator"></div>'+''.join(entry(i) for i in range(50)),'https://zakupki.gov.ru/?pageNumber=11')
        self.assertEqual(len(result['cards']),50)
        self.assertFalse(result['pagination']['end_verified'])
        self.assertEqual(result['pagination']['page'],11)

    def test_global_spreadsheet_and_other_notices_are_not_tender_attachments(self):
        url='https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=12345678901'
        html='''<a href='/rpt/cat02/zakupki-traffic.xlsx'>Отчет о посещаемости</a>
        <a href='/epz/order/notice/ea20/view/documents.html?regNumber=99999999999'>Чужая закупка</a>
        <a href='/epz/order/notice/ea20/view/documents.html?regNumber=12345678901#'>Документы</a>
        <a href='/epz/order/notice/ea20/view/documents.html?regNumber=12345678901'>Документы</a>
        <a href='/44fz/filestore/public/1.0/download/priz/file.html?uid=ONE'>ТЗ</a>'''
        links=document_links(BeautifulSoup(html,'html.parser'),url)
        self.assertEqual(len(links),2)
        self.assertTrue(all('/rpt/' not in d['url'] and '99999999999' not in d['url'] for d in links))

    def test_identical_version_bytes_reuse_extract_within_same_notice(self):
        job=self.start();number='12345678901'
        self.r.db.execute("INSERT INTO research_notices(job,number,card,provenance,state) VALUES(?,?,?,'[]','review')",(job,number,json.dumps(card())))
        analysis=json.dumps({'summary':'Существенные условия','findings':['Пункт 1'],'document_role':'contract'})
        for url in ['https://zakupki.gov.ru/old','https://zakupki.gov.ru/new']:
            self.r.db.execute("INSERT INTO research_resources(job,number,url,kind,state,sha256) VALUES(?,?,?,'document','read','same-hash')",(job,number,url))
            self.r.db.execute('INSERT INTO research_chunks VALUES(?,?,?,?,?,?,?)',(job,number,url,0,'абзац 1','Одинаковый текст',analysis if url.endswith('old') else None))
        self.r.db.commit()
        class NoModel:
            def structured(self,*args):raise AssertionError('Identical source must not be analyzed twice')
        notice=dict(self.r.db.execute('SELECT * FROM research_notices WHERE job=?',(job,)).fetchone())
        self.r.review_step(self.r.job(job),notice,NoModel())
        self.assertEqual(self.r.db.execute('SELECT count(analysis) FROM research_chunks').fetchone()[0],2)

    def test_invalidated_223_edition_does_not_supply_current_requirements(self):
        html='''<div class="attachment d-none no-valid">Редакция Недействующая
        <a href="/223/filestore/public/1.0/download/fz223/file.html?uid=OLD">Извещение с ФСБ</a></div>
        <div class="attachment">Редакция Действующая
        <a href="/223/filestore/public/1.0/download/fz223/file.html?uid=NEW">Извещение</a></div>'''
        links=document_links(BeautifulSoup(html,'html.parser'),'https://zakupki.gov.ru/epz/order/notice/notice223/documents.html?purchaseNoticeNumber=32616484294')
        self.assertEqual([d['url'].rsplit('=',1)[-1] for d in links],['NEW'])

    def test_total_count_does_not_mistake_whitespace_for_zero(self):
        html='<span class="search-results__total">Всего записей: 1 240</span><div class="paginator"></div>'
        result=parse_page(html,'https://zakupki.gov.ru/?pageNumber=11')
        self.assertEqual(result['pagination']['total_results'],1240)
        self.assertFalse(result['pagination']['end_verified'])
        empty=parse_page('<span class="search-results__total">Всего записей: 0</span>','https://zakupki.gov.ru/')
        self.assertTrue(empty['not_found_verified'])

    def test_generic_zip_reads_every_member_and_retains_unsupported_gap(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('one.txt','Полное ТЗ')
            archive.writestr('../two.txt','Условия договора')
            archive.writestr('unsupported.rar',b'Rar!')
        from iva_integration.tender_research import extract_binary
        result=extract_binary(data.getvalue(),'docs.zip')
        self.assertIn('unsupported.rar',result['gap'])
        self.assertEqual(len(result['chunks']),2)
        self.assertIn('../two.txt',result['chunks'][1]['location'])
        self.assertFalse((self.root/'two.txt').exists())

    def test_direct_status_and_cancel_do_not_call_model(self):
        from local_finance.iva_telegram import IvaAssistant
        from local_finance.bot import OWNER
        assistant=object.__new__(IvaAssistant)
        with patch('iva_integration.tender_agent.dispatch',return_value={'text':'Сохранённый прогресс'}) as dispatch:
            self.assertEqual(assistant.handle(OWNER,'/tenders','owner:control'),'Сохранённый прогресс')
            dispatch.assert_called_once_with('/tenders','owner:control','status',None)
        with patch('iva_integration.tender_agent.dispatch',return_value={'text':'Остановлено'}) as dispatch:
            self.assertEqual(assistant.handle(OWNER,'/tenders_cancel abcdef123456','owner:control'),'Остановлено')
            dispatch.assert_called_once_with('/tenders_cancel abcdef123456','owner:control','cancel','abcdef123456')

    def test_pdf_beyond_old_80_page_cap_is_read_to_the_end(self):
        import pymupdf
        from iva_integration.tender_research import extract_binary
        with pymupdf.open() as pdf:
            for i in range(90):
                pdf.new_page().insert_text((30,40),f'Page {i+1}: a complete readable contractual requirement for testing.')
            body=pdf.tobytes()
        result=extract_binary(body,'large.pdf')
        self.assertNotIn('gap',result)
        self.assertIn('Page 90:',''.join(c['text'] for c in result['chunks']))

    def test_missing_discovered_notice_fails_coverage_audit(self):
        job=self.start()
        self.r.step(reader=lambda u:{'ok':True,'cards':[card()],'pagination':{'end_verified':True}})
        with self.r.db:self.r.db.execute('DELETE FROM research_notices WHERE job=?',(job,))
        self.r.step();self.r.step()
        self.assertEqual(self.r.status(job)['state'],'completed_with_gaps')
        manifest=json.loads((self.root/'artifacts'/job/'manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(manifest['audit']['ok']);self.assertEqual(manifest['audit']['exit_code'],1)
        self.assertTrue(any('missing from ledger' in x for x in manifest['audit']['errors']))

    def test_corrupt_pdf_is_a_gap_and_zip_keeps_other_members(self):
        with patch('iva_integration.tender_research.valid_url'),patch('iva_integration.tender_research.fetch',return_value=(b'%PDF-corrupt','https://zakupki.gov.ru/bad.pdf')):
            result=read_resource('https://zakupki.gov.ru/bad.pdf',self.root/'originals')
        self.assertTrue(result['ok']);self.assertIn('gap',result)
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('broken.pdf',b'%PDF-corrupt')
            archive.writestr('intact.txt','Полное читаемое приложение')
        from iva_integration.tender_research import extract_binary
        result=extract_binary(data.getvalue(),'docs.zip')
        self.assertIn('broken.pdf',result['gap'])
        self.assertIn('читаемое приложение',result['chunks'][0]['text'])


if __name__=='__main__':unittest.main()
