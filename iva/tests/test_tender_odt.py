import io
import unittest
import zipfile
import json
import tempfile
from pathlib import Path
from iva_integration.tender_research import Research
from iva_integration.tender_research import extract_binary
from iva_integration.tender_odt import body_text

PREFIX='''<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">'''
def xml(body):return (PREFIX+'<office:automatic-styles><text:p>STYLE_NOISE</text:p></office:automatic-styles><office:body><office:text>'+body+'</office:text></office:body></office:document-content>').encode()
def package(body):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('mimetype','application/vnd.oasis.opendocument.text')
        z.writestr('content.xml',xml(body));z.writestr('styles.xml','<styles><!--'+'STYLE_NOISE'*20000+'--></styles>')
        z.writestr('META-INF/manifest.xml','MANIFEST_NOISE')
    return out.getvalue()

class OdtTests(unittest.TestCase):
    def test_package_extracts_body_not_xml_or_styles(self):
        r=extract_binary(package('<text:h>Техническое задание</text:h><text:p>1.2 Цена 123 456,78 ₽, срок 15 дней.</text:p>'),'download.html')
        text=''.join(x['text'] for x in r['chunks'])
        self.assertIn('1.2 Цена 123 456,78 ₽, срок 15 дней.',text)
        self.assertIn('Техническое задание',text)
        self.assertNotIn('STYLE_NOISE',text);self.assertNotIn('MANIFEST_NOISE',text);self.assertNotIn('<office:',text)
        self.assertEqual(len(r['chunks']),1)

    def test_tables_lists_inline_spaces_and_notes_survive(self):
        value,media=body_text(xml('''<text:list><text:list-item text:start-value="7"><text:p>Пункт <text:span>7.1</text:span></text:p></text:list-item></text:list>
        <table:table><table:table-row><table:table-cell table:number-columns-spanned="2"><text:p>Цена<text:tab/>100<text:s text:c="2"/>₽<text:line-break/>без НДС</text:p></table:table-cell></table:table-row></table:table>'''))
        for expected in ['Пункт 7.1','start-value: 7','ODT ячейка','number-columns-spanned','Цена\t100  ₽\nбез НДС']:self.assertIn(expected,value)
        self.assertFalse(media)

    def test_media_is_an_explicit_coverage_gap(self):
        r=extract_binary(package('<text:p>ТЗ</text:p><draw:image/>'),'doc.odt')
        self.assertIn('визуальной',r['gap'])

    def test_automatic_numbering_and_continuation(self):
        styles=('''<styles xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"><text:list-style style:name="L"><text:list-level-style-number text:level="1" style:num-format="1" style:num-suffix="."/></text:list-style></styles>''').encode()
        text,_=body_text(xml('''<text:list text:style-name="L"><text:list-item text:start-value="7"><text:p>Обязанности</text:p></text:list-item></text:list><text:list text:style-name="L" text:continue-numbering="true"><text:list-item><text:p>Оплата</text:p></text:list-item></text:list>'''),styles)
        self.assertIn('автоматический номер: 7.',text)
        self.assertIn('автоматический номер: 8.',text)

    def test_entities_and_non_text_opendocument_rejected(self):
        with self.assertRaises(ValueError):body_text(b'<!DOCTYPE x>'+xml('<text:p>Data</text:p>'))
        with self.assertRaises(ValueError):body_text((PREFIX+'<office:body><office:spreadsheet/></office:body></office:document-content>').encode())
        with self.assertRaises(ValueError):body_text(xml('<text:p><text:s text:c="999999999"/></text:p>'))

    def test_embedded_odt_in_zip_still_works(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('ТЗ.odt',package('<text:p>Условия оплаты: 30 дней</text:p>'))
        text=''.join(x['text'] for x in extract_binary(out.getvalue(),'docs.zip')['chunks'])
        self.assertIn('Условия оплаты: 30 дней',text);self.assertNotIn('STYLE_NOISE',text)


class ExtractionReuseTests(unittest.TestCase):
    def check_reuse(self,kind='document',previous_job='j',previous_text='same text',old_url_in_result=False):
        with tempfile.TemporaryDirectory() as directory:
            research=Research(Path(directory)/'db.sqlite3',Path(directory)/'artifacts')
            result={'summary':'Original extracted facts','findings':[],'document_role':'contract'}
            if old_url_in_result:result['findings']=['https://zakupki.gov.ru/old.pdf']
            try:
                with research.db:
                    for job,number,url,analysis,text in [(previous_job,'one','https://zakupki.gov.ru/old.pdf',json.dumps(result),previous_text),('j','two','https://zakupki.gov.ru/new.pdf',None,'same text')]:
                        research.db.execute("INSERT INTO research_resources(job,number,url,kind,state,sha256) VALUES(?,?,?,?,?,?)",(job,number,url,kind,'read','content-hash'))
                        research.db.execute('INSERT INTO research_chunks VALUES(?,?,?,?,?,?,?)',(job,number,url,0,'ODT text',text,analysis))
                class Model:
                    calls=0
                    def structured(self,*args):
                        self.calls+=1
                        return {'summary':'Fresh analysis','findings':[],'document_role':'contract'}
                model=Model()
                research.review_step({'id':'j','task':'test','profile':'{}'},{'number':'two'},model)
                return model.calls
            finally:research.close()

    def test_identical_attachment_in_same_job_reuses_extraction(self):self.assertEqual(self.check_reuse(),0)
    def test_notice_cards_are_not_cross_notice_cached(self):self.assertEqual(self.check_reuse(kind='card'),1)
    def test_other_jobs_cannot_supply_stale_extraction(self):self.assertEqual(self.check_reuse(previous_job='older-job'),1)
    def test_different_text_or_source_bound_citations_are_not_reused(self):
        self.assertEqual(self.check_reuse(previous_text='different text'),1)
        self.assertEqual(self.check_reuse(old_url_in_result=True),1)
