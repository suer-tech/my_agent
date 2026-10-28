import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from local_finance.assistant import Ledger,Assistant
from local_finance import financial_screens as screens
from local_finance.document_vision import originals
from iva_integration.finance_agent import run


def fixture():
    months=['20 октября','20 ноября','20 декабря','20 января','20 февраля','20 марта','20 апреля','20 мая','20 июня','20 июля','20 августа','20 сентября','20 октября','20 ноября','20 декабря','20 января','20 февраля']
    principal=['100.10','101.22','109.10','110.83','115.79','129.28','126.95','134.90','138.73','147.20','151.75','158.57','167.30','173.45','182.30','189.57','170.12']
    interest=['105.90','104.78','96.90','95.17','90.21','76.72','79.05','71.10','67.27','58.80','54.25','47.43','38.70','32.55','23.70','16.43','7.75']
    all_rows=[{'date':None,'date_display':months[n],'date_evidence':None,'payment':'206.00' if n<16 else '177.87','payment_display':'206' if n<16 else '177,87','principal':principal[n],'interest':interest[n],'fees':None,'balance':None,'page':0,'row_ref':str(n),'payment_no':None} for n in range(17)]
    pages=[]
    for page,selection in enumerate((range(11,17),range(7,13),range(3,9),range(0,5)),1):
        rows=[{**all_rows[n],'page':page,'row_ref':f'p{page}:r{n}'} for n in selection]
        if page==4:rows[-1].update(payment=None,payment_display=None,interest=None)
        facts=[]
        if page==4:
            for kind,label,value in [('principal_total','Основной долг','2407.16'),('interest_total','Проценты за весь период','1066.71')]:
                facts.append({'kind':kind,'label':label,'raw':value,'value':value,'unit':'RUB','scope':'unknown','row_ref':'overview:'+kind})
        pages.append({'page':page,'kind':'loan_schedule','visible_row_count':len(rows),'rows':rows,'summary_fields':facts,'questions':[]})
    return {'kind':'loan_schedule','bank':None,'loan_name':None,'currency':'RUB','rows':[],'receipt':None,'questions':[],'pages_covered':[1,2,3,4],'page_extractions':pages}


class ScreenTests(unittest.TestCase):
    def test_all_observations_and_overview_survive_overlaps_in_reverse_page_order(self):
        data=fixture();result=screens.finalize(data)
        self.assertEqual(len(result['rows']),17);self.assertEqual(result['original_observation_count'],23)
        self.assertEqual(result['page_extractions'],data['page_extractions'])
        self.assertTrue(result['overlap_chain_connected'])
        self.assertTrue(all(c['matches'] for c in result['arithmetic_checks']))
        self.assertEqual([r['principal'] for r in result['rows'][:3]],['100.10','101.22','109.10'])
        self.assertTrue(all(r['date'] is None for r in result['rows']))
        self.assertTrue(any('годы' in q for q in result['questions']))
        self.assertEqual(len([r for r in result['rows'] if r['date_display']=='20 октября']),2)
        self.assertEqual(result['rows'][4]['interest'],'90.21')
        self.assertEqual(len(result['rows'][4]['observations']),2)

    def test_missing_three_rows_detected_even_when_model_claims_page_count_complete(self):
        data=fixture();page=data['page_extractions'][3];page['rows']=page['rows'][3:];page['visible_row_count']=len(page['rows'])
        result=screens.finalize(data)
        self.assertEqual(len(result['rows']),14)
        self.assertTrue(all(c['matches'] is False for c in result['arithmetic_checks']))
        self.assertTrue(any('не совпал' in q for q in result['questions']))

    def test_invented_year_removed_but_visible_year_kept(self):
        data=fixture();row=data['page_extractions'][0]['rows'][0];row['date']='2026-09-20'
        self.assertIsNone(next(r for r in screens.finalize(data)['rows'] if r['principal']=='158.57')['date'])
        row['date_display']='20 сентября 2026';row['date_evidence']='20 сентября 2026'
        self.assertEqual(next(r for r in screens.finalize(data)['rows'] if r['principal']=='158.57')['date'],'2026-09-20')

    def test_amount_only_and_within_page_duplicates_are_not_silently_removed(self):
        data=fixture()
        for p in data['page_extractions']:
            for r in p['rows']:r['principal']=None;r['interest']=None
        self.assertEqual(len(screens.finalize(data)['rows']),23)
        data=fixture();p=data['page_extractions'][0];p['rows'].append(copy.deepcopy(p['rows'][0]));p['visible_row_count']+=1
        self.assertGreaterEqual(len(screens.finalize(data)['rows']),18)

    def test_page_coverage_and_visible_block_count(self):
        data=fixture();data['pages_covered']=[1,2,3]
        with self.assertRaises(ValueError):screens.finalize(data)
        data=fixture();data['page_extractions'][0]['visible_row_count']=7
        self.assertTrue(any('число видимых' in q for q in screens.finalize(data)['questions']))

    def test_fully_cut_off_edge_row_can_be_completed_from_a_two_row_overlap(self):
        data=fixture();p=data['page_extractions'][3];r=p['rows'][-1]
        r.update(principal=None,interest=None,payment=None,payment_display=None)
        p['issues']=[{'kind':'partial_row','row_ref':r['row_ref'],'question':'Нижняя строка обрезана'}]
        p['questions']=['Нижняя строка обрезана']
        result=screens.finalize(data)
        self.assertEqual(len(result['rows']),17)
        self.assertTrue(all(c['matches'] for c in result['arithmetic_checks']))
        self.assertNotIn('Нижняя строка обрезана',result['questions'])
        self.assertEqual(len(result['resolved_issues']),1)

    def test_receipts_unchanged(self):
        data={'kind':'receipt','receipt':{'total':'100.00'},'page_extractions':[{'page':1,'kind':'receipt','rows':[]}]}
        self.assertEqual(screens.finalize(data),data)

    def test_old_extraction_version_retained_and_imported_schedule_never_overwritten(self):
        l=Ledger(':memory:')
        try:
            old={'kind':'loan_schedule','rows':[]};screens.persist(l,'id','file','loan_schedule',4,old)
            screens.persist(l,'id','file','loan_schedule',4,screens.finalize(fixture()))
            self.assertEqual(json.loads(l.rows('SELECT payload FROM source_document_revisions')[0]['payload']),old)
            with l.transaction():l.db.execute("UPDATE source_documents SET status='imported'")
            with self.assertRaises(ValueError):screens.persist(l,'id','file','loan_schedule',4,old)
            self.assertEqual(l.rows('SELECT * FROM operations'),[])
        finally:l.db.close()


class OriginalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.l=Ledger(self.root/'isolated.sqlite3');Assistant(self.l,None)
        self.folder=self.root/'data/documents';self.folder.mkdir(parents=True)
        self.path=self.folder/'original.bin';Image.new('RGB',(30,30),'blue').save(self.path,format='PNG')
        self.id=hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.l.transaction():self.l.db.execute('INSERT INTO source_documents VALUES(?,?,?,?,?,?)',(self.id,str(self.path),'loan_schedule',1,'{}','extracted'))

    def tearDown(self):self.l.db.close();self.tmp.cleanup()

    def test_real_images_are_sanitized_attached_and_cleaned_without_writes(self):
        with originals(self.l,self.id,storage_root=self.root) as (info,images):
            self.assertTrue(info['hashes_verified']);self.assertTrue(info['all_pages_attached'])
            self.assertTrue(Path(images[0]).read_bytes().startswith(b'\x89PNG'))
            path=Path(images[0])
        self.assertFalse(path.exists());self.assertEqual(self.l.rows('SELECT * FROM operations'),[])

    def test_tampered_file_wrong_page_and_external_path_refused(self):
        for pages in ([0],[2],[1,1]):
            with self.assertRaises(ValueError),originals(self.l,self.id,pages,storage_root=self.root):pass
        self.path.write_bytes(b'tampered')
        with self.assertRaises(ValueError),originals(self.l,self.id,storage_root=self.root):pass
        outside=self.root/'outside.bin';outside.write_bytes(b'not allowed')
        with self.l.transaction():self.l.db.execute('UPDATE source_documents SET path=?',(str(outside),))
        with self.assertRaises(PermissionError),originals(self.l,self.id,storage_root=self.root):pass

    def test_specialist_receives_real_image_on_next_model_step_and_keeps_read_only(self):
        captured=[];docid=self.id
        class CLI:
            model='gpt-6.1-sol';reasoning_effort='medium';last_usage={}
            def structured(s,prompt,schema,images=()):
                captured.append(list(images))
                if len(captured)==1:return {'text':'','calls':[{'name':'finance_review_document','arguments_json':json.dumps({'document_id':docid})}]}
                self.assertEqual(len(images),1);self.assertTrue(Path(images[0]).is_file())
                return {'text':'Оригинал просмотрен','calls':[]}
        original=originals
        def review(ledger,docid,pages=None,storage_root=None):return original(ledger,docid,pages,storage_root=self.root)
        with patch('local_finance.document_vision.originals',side_effect=review):
            result=run('Перепроверь оригинал','verification:images',self.root/'isolated.sqlite3',CLI(),body_override='Перепроверь оригинал')
        self.assertTrue(result['steps'][0]['ok']);self.assertEqual(captured[0],[])
        self.assertFalse(Path(captured[1][0]).exists())
        self.assertEqual(self.l.rows('SELECT * FROM operations'),[])
        self.assertEqual(self.l.rows('SELECT * FROM reported_loans'),[])


if __name__=='__main__':unittest.main()
