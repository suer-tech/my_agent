import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_finance.bot import Bot,OWNER
from local_finance.assistant import Assistant,Ledger
from local_finance.attachments import merge_overlap


class API:
    def __init__(self):self.sent=[]
    def send(self,chat,text):self.sent.append((chat,text))


class CLI:
    def plan(self,prompt):return 'Текст','none',{}


class Reader:
    def __init__(self):self.calls=[]
    def handle(self,update,metadata):self.calls.append((update,metadata));return 'Альбом прочитан'


class AlbumTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(':memory:');self.api=API();self.bot=Bot(self.l,Assistant(self.l,CLI()),self.api);self.reader=Reader();self.bot.attachments=self.reader
    def tearDown(self):self.l.db.close()
    def photo(self,uid,caption=''):
        return {'update_id':uid,'message':{'message_id':uid,'media_group_id':'one-album','chat':{'id':OWNER,'type':'private'},'from':{'id':OWNER},'caption':caption,
                'photo':[{'file_id':'small','width':90,'height':100},{'file_id':'full-'+str(uid),'width':1000,'height':2000},{'file_id':'medium','width':320,'height':640}]}}

    def test_three_photos_one_caption_one_context_best_resolution_no_audio(self):
        with patch('local_finance.bot.time.time',return_value=100):self.bot.ingest([self.photo(1,'Это один кредит из списка'),self.photo(2),self.photo(3)])
        with patch('local_finance.bot.time.time',return_value=103),patch.object(self.bot.voice,'transcribe',side_effect=AssertionError('Images must not enter STT')) if self.bot.voice else patch('builtins.id',wraps=id):
            self.assertTrue(self.bot.process_one())
        self.assertEqual(len(self.reader.calls),1)
        metadata=self.reader.calls[0][1]
        self.assertEqual(len(metadata['album_members']),3)
        self.assertEqual(metadata['album_members'][0]['caption'],'Это один кредит из списка')
        self.assertEqual([m['file_id'] for m in metadata['album_members']],['full-1','full-2','full-3'])
        self.assertEqual([r['state'] for r in self.l.rows('SELECT state FROM local_updates ORDER BY id')],['done','done_group','done_group'])
        self.assertFalse(self.bot.process_one());self.assertEqual(len(self.api.sent),1)

    def test_album_waits_for_upload_without_losing_metadata(self):
        with patch('local_finance.bot.time.time',return_value=100):self.bot.ingest([self.photo(1,'Подпись')]);self.assertFalse(self.bot.process_one())
        self.assertEqual(self.reader.calls,[])
        self.assertEqual(len(self.l.rows('SELECT * FROM local_telegram_messages')),1)

    def test_overlapping_row_numbers_merge_and_keep_source_refs(self):
        a={'payment_no':5,'date':'2026-10-20','payment':None,'principal':None,'interest':None,'fees':None,'balance':None,'page':1,'row_ref':'p1:r5'}
        b={**a,'payment':'100.21','page':2,'row_ref':'p2:r5'}
        data=merge_overlap({'rows':[a,b],'questions':[]})
        self.assertEqual(len(data['rows']),1);self.assertEqual(data['rows'][0]['payment'],'100.21');self.assertIn('p2:r5',data['rows'][0]['row_ref'])

    def test_conflicting_overlap_is_question_not_guessed_amount(self):
        a={'payment_no':5,'date':'2026-10-20','payment':'100.21','page':1,'row_ref':'p1'}
        b={**a,'payment':'100.22','page':2,'row_ref':'p2'}
        self.assertTrue(merge_overlap({'rows':[a,b],'questions':[]})['questions'])

    def test_same_amount_different_payment_numbers_not_dropped(self):
        a={'payment_no':5,'date':'2026-10-20','payment':'100.21','page':1,'row_ref':'p1'}
        b={**a,'payment_no':6,'row_ref':'p2'}
        self.assertEqual(len(merge_overlap({'rows':[a,b],'questions':[]})['rows']),2)

    def test_image_document_pdf_text_and_voice_have_distinct_routes(self):
        updates=[]
        for index,(filename,mime) in enumerate([('x.png','image/png'),('x.pdf','application/pdf'),('x.txt','text/plain')],1):
            updates.append({'update_id':index,'message':{'chat':{'id':OWNER,'type':'private'},'from':{'id':OWNER},'document':{'file_id':'x','file_name':filename,'mime_type':mime},'caption':'Подпись'}})
        updates.append({'update_id':4,'message':{'chat':{'id':OWNER,'type':'private'},'from':{'id':OWNER},'voice':{'file_id':'voice','duration':1}}})
        self.bot.ingest(updates)
        self.assertEqual([r['text'] for r in self.l.rows('SELECT text FROM local_updates ORDER BY id')],['/incoming_image','/incoming_pdf','/incoming_text','/incoming_voice'])


if __name__=='__main__':unittest.main()
