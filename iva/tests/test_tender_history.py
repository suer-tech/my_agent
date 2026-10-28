import json
from datetime import date,timedelta
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

from iva_integration.tender_research import Research,EXTRACT,TRIAGE,OCR,read_resource,document_links
from iva_integration.tender_history import BASELINE,ANALOG,GATES,MEASURES,verify,discount,query_window,summary
from bs4 import BeautifulSoup

TARGET='https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0161600001326000063'
CARD='https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0126300035826000204'
ROOT='https://zakupki.gov.ru/'
TEXT=('Специальные требования не установлены. НМЦК 1 600 000,00 рублей. '
      'Цена победителя 880 000,00 рублей. Цена договора 890 000,00 рублей. '
      'Подано 24 заявки. Первые части 20. Торговалось 9. Вторые части 1. '
      'Аукцион завершён. Цена за весь договор. Исполнение продолжается.')

def source(field,url=ROOT+'protocol',quote=TEXT):
    return {'field':field,'url':url,'quote':quote,'location':'пункт 1'}

def target():
    return {'number':'0161600001326000063','title':'Новые интерактивные приложения',
            'product_type':'new_app','scope':'UI, CMS, multimedia','technologies':['JavaScript'],
            'nmc_rub':1600000,'expected_contract_date':None,
            'access':{k:'not_required' for k in GATES},'financial_terms':'После приёмки',
            'execution_terms':'Удалённо','queries':['разработка приложения'],
            'sources':[source(k,ROOT+'target-admission') for k in (*GATES,'nmc_rub')],'unknowns':[]}

def analog():
    values=dict(zip(MEASURES,[1600000,880000,890000,24,20,9,1]))
    today=date.today();award=(today-timedelta(days=20)).isoformat()
    return {'group':'strict','similarity':88,'reason':'Близкая разработка','product_type':'new_app',
            'technologies':['JavaScript'],'differences':[],'access':{k:'not_required' for k in GATES},
            'procedure':'auction','procedure_completed':True,'award_date':award,
            'contract_execution':'in_progress','price_is_total':True,'winner_name':None,
            'contract_number':None,'related_notice_number':None,'participants':[],**values,
            'sources':[source(k) for k in (*MEASURES,*GATES,'procedure','procedure_completed','price_is_total','contract_execution')]
                      +[source('award_date',quote='Дата итогов '+award)],'facts':['Новая разработка'],
            'unknowns':[]}

class Model:
    def structured(self,prompt,schema,images=()):
        if schema==TRIAGE:
            cards=json.loads(prompt.split('\n')[-1])
            return {'decisions':[{'number':c['number'],'relevant':True,'reason':'Программная разработка'} for c in cards]}
        if schema==EXTRACT:
            role=next((v for v in ('specification','contract','admission','protocol') if '/'+v in prompt),'card')
            return {'summary':'Условия документа','findings':[TEXT],'document_role':role}
        if schema==BASELINE:return target()
        if schema==ANALOG:return analog()
        if schema==OCR:return {'text':TEXT,'unreadable':False}
        raise AssertionError(schema)

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.r=Research(self.path/'test.sqlite3',self.path/'artifacts')
    def tearDown(self):self.r.close();self.tmp.cleanup()
    def start(self):return self.r.start('История выбранной новой разработки','owner:test',notify=False,workflow='history',target_url=TARGET)['job_id']

    def test_two_workflows_and_idempotency_are_separate(self):
        search=self.r.start('Ищи тендеры','owner:test',['разработка'],False)
        history=self.start();self.assertNotEqual(search['job_id'],history)
        self.assertEqual(self.start(),history)
        self.assertEqual(self.r.job(history)['stage'],'baseline')
        self.assertEqual(self.r.status(history)['workflow'],'history')
        with self.assertRaises(ValueError):self.r.start('История','owner:invalid',notify=False,workflow='history',target_url='https://example.com/card')

    def test_selected_history_is_not_starved_by_bulk_search(self):
        search=self.r.start('Широкий поиск','owner:search',['разработка'],False)['job_id']
        history=self.start()
        def reader(url):
            if 'results.html' in url:return {'ok':True,'cards':[{'number':'0126300035826000204','url':CARD,'title':'Разработка'}],'pagination':{'end_verified':False}}
            return {'ok':True,'sha256':'target','chunks':[{'text':TEXT,'location':'карточка'}]}
        self.r.step(Model(),reader);self.r.step(Model(),reader)
        self.assertEqual(self.r.status(search)['pages'],1)
        self.assertEqual(self.r.status(history)['documents'],{'read':1})

    def test_full_history_pipeline_uses_closed_search_expands_and_survives_restart(self):
        job=self.start();seen=[]
        def reader(url):
            if 'results.html' in url:
                q=parse_qs(urlsplit(url).query);seen.append(q)
                return {'ok':True,'cards':[{'number':'0126300035826000204','url':CARD,'title':'Новое приложение'}],'pagination':{'end_verified':True}}
            if url in (TARGET,CARD):
                prefix='target-' if url==TARGET else ''
                return {'ok':True,'sha256':url,'chunks':[{'text':TEXT,'location':'карточка'}],
                        'documents':[{'url':ROOT+prefix+name,'title':name,'kind':'document'} for name in ('specification','contract','admission','protocol')]}
            return {'ok':True,'sha256':url,'chunks':[{'text':TEXT+' Дата итогов '+analog()['award_date'],'location':'пункт 1'}]}
        for _ in range(9):self.r.step(Model(),reader)
        self.r.close();self.r=Research(self.path/'test.sqlite3',self.path/'artifacts')
        for _ in range(120):
            self.r.step(Model(),reader)
            if self.r.status(job)['state'] not in ('queued','running'):break
        state=self.r.status(job)
        self.assertIn(state['state'],('completed','completed_with_gaps'),self.r.job(job))
        self.assertEqual(state['notices'],{'reviewed':1})
        self.assertTrue(all(q.get('pc')==['on'] and 'af' not in q for q in seen))
        self.assertTrue(all('publishDateFrom' in q and 'priceFromGeneral' in q for q in seen))
        self.assertEqual(json.loads(self.r.job(job)['history_plan'])['horizon'],365)
        history=json.loads(self.r.db.execute('SELECT review FROM research_notices WHERE job=?',(job,)).fetchone()[0])['history']
        self.assertEqual(history['submitted'],24);self.assertEqual(history['first_admitted'],20)
        self.assertEqual(history['price_participants'],9);self.assertEqual(history['final_admitted'],1)
        self.assertEqual(history['winning_discount_percent'],45);self.assertEqual(history['contract_discount_percent'],44.38)
        self.assertTrue((self.path/'artifacts'/job/'pricing.json').is_file())
        self.assertEqual(self.r.status(job)['delivery'],{})
        self.assertEqual(summary(self.r,self.r.job(job))['cohorts'],{})

    def test_fabricated_numbers_and_unknown_gates_never_become_strict(self):
        j={'created':date.today().isoformat()};t=target();t['reference_date']=date.today().isoformat()
        resources=[{'url':ROOT+'protocol','state':'read'}];texts=[(ROOT+'protocol',TEXT+' Дата итогов '+analog()['award_date'])]
        v=analog();v['winner_price_rub']=1;v['sources']=[s for s in v['sources'] if s['field']!='staff_minimum']
        v['_extracts']=[{'document_role':r} for r in ('specification','contract','admission','protocol')]
        result=verify(v,j,t,resources,texts)
        self.assertIsNone(result['winner_price_rub']);self.assertIsNone(result['winning_discount_percent'])
        self.assertEqual(result['access']['staff_minimum'],'unknown');self.assertEqual(result['group'],'conditional')
        self.assertEqual(result['contract_discount_percent'],44.38)
        self.assertEqual(discount(100,120),-20)

    def test_later_than_reference_date_cannot_enter_historical_cohort(self):
        j={'created':date.today().isoformat()};t=target();t['reference_date']=(date.today()-timedelta(days=40)).isoformat()
        v=analog();r=verify(v,j,t,[{'url':ROOT+'protocol','state':'read'}],[(ROOT+'protocol',TEXT+' Дата итогов '+v['award_date'])])
        self.assertIsNone(r['award_date']);self.assertNotEqual(r['group'],'strict')
        self.assertEqual(query_window(j,t,90)[0][2]['published_to'],date.fromisoformat(t['reference_date']).strftime('%d.%m.%Y'))

    def test_scanned_pdf_pages_are_persisted_and_processed_one_at_a_time(self):
        import pymupdf
        pdf=pymupdf.open();pdf.new_page();pdf.new_page();body=pdf.tobytes();pdf.close()
        with patch('iva_integration.tender_research.valid_url'),patch('iva_integration.tender_research.fetch',return_value=(body,ROOT+'protocol')):
            parsed=read_resource(ROOT+'protocol',self.path/'images',include_history=True)
        self.assertEqual([x['page'] for x in parsed['images']],[1,2])
        job=self.start()
        self.r.step(Model(),lambda url:parsed)
        self.r.step(Model(),lambda url:self.fail('OCR must use the saved image'))
        self.assertEqual(self.r.db.execute("SELECT count(*) FROM research_images WHERE state='read'").fetchone()[0],1)
        self.r.close();self.r=Research(self.path/'test.sqlite3',self.path/'artifacts')
        self.r.step(Model(),lambda url:self.fail('OCR must resume with the second saved image'))
        self.assertEqual(self.r.db.execute("SELECT count(*) FROM research_images WHERE state='read'").fetchone()[0],2)
        self.assertEqual(self.r.status(job)['documents'],{'read':1})
        with self.r.db:self.r.db.execute("UPDATE research_resources SET state='pending' WHERE job=?",(job,))
        self.r.step(Model(),lambda url:parsed)
        self.assertEqual(self.r.status(job)['documents'],{'read':1})

    def test_history_navigation_stays_on_notice_and_follows_contract_registry(self):
        html='''<a href="/epz/order/notice/ea20/view/supplier-results.html?regNumber=0161600001326000063">Итоги</a>
        <a href="/epz/order/notice/ea20/view/supplier-results.html?regNumber=9999999999999999999">Чужая закупка</a>
        <a href="/epz/order/notice/ea20/view/protocol/protocol-bid-list.html?regNumber=0161600001326000063">Заявки</a>
        <a href="/epz/contract/contractCard/common-info.html?reestrNumber=1000000000000000000">Контракт</a>
        <a href="/epz/contractfz223/search/results.html">Глобальный реестр</a>'''
        links=document_links(BeautifulSoup(html,'html.parser'),TARGET,include_history=True)
        self.assertEqual(len(links),3)
        self.assertFalse(any('9999999999' in x['url'] for x in links))

    def test_dispatch_history_uses_existing_specialist_and_owner_authorization(self):
        from iva_integration.tender_agent import dispatch
        from unittest.mock import MagicMock
        store=MagicMock();job=MagicMock();job.start.return_value={'job_id':'123456abcdef','workflow':'history'}
        with patch('local_finance.assistant.Ledger',return_value=store),patch('local_finance.received_income.raw_owner_input',return_value={'body':'Изучи историю выбранного тендера'}),patch('iva_integration.tender_research.Research',return_value=job):
            result=dispatch('История','telegram:test','history',target_url=TARGET)
        self.assertEqual(result['workflow'],'history')
        job.start.assert_called_once_with('История','telegram:test',workflow='history',target_url=TARGET)
        with patch('local_finance.assistant.Ledger',return_value=store),patch('iva_integration.tender_research.Research') as research:
            with self.assertRaises(PermissionError):dispatch('История','diagnostic:test','history',target_url=TARGET)
            research.assert_not_called()

    def test_single_supplier_weighted_and_terminated_never_feed_price_statistics(self):
        target_value=target();target_value['reference_date']=date.today().isoformat()
        for procedure,execution in [('single_supplier','in_progress'),('weighted','in_progress'),('auction','terminated')]:
            with self.subTest(procedure=procedure,execution=execution):
                v=analog();v['procedure']=procedure;v['contract_execution']=execution
                v['_extracts']=[{'document_role':r} for r in ('specification','contract','admission','protocol')]
                text=TEXT+' Дата итогов '+v['award_date']
                result=verify(v,{'created':date.today().isoformat()},target_value,[{'url':ROOT+'protocol','state':'read'}],[(ROOT+'protocol',text)])
                self.assertNotEqual(result['group'],'strict')
                self.assertEqual(result['submitted'],24)
                self.assertEqual(result['contract_discount_percent'],44.38)

if __name__=='__main__':unittest.main()
