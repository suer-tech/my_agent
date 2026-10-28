import json
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hr_campaign as hr
from worker.engine import Engine,fingerprint
from worker.store import Store
from worker.tests.test_research import FakeRuntime, FakeCrm, REVIEW


class HrTests(unittest.TestCase):
    def test_render_preserves_owner_offer_without_ocr_or_psychological_claims(self):
        p={'subject':'ИИ для анализа резюме и интервью','observation':'На вашем сайте описана работа с требованиями вакансии.',
           'pilot_focus':'Предлагаю попробовать сервис на одной вакансии и обезличенных материалах.',
           'question':'Кому можно показать пример отчёта?'}
        text=hr.render_hr({'segment':'recruitment_agency'},p)['body']
        for phrase in ['искусственного интеллекта','30 минут до часа','10–15 минут','«Рекомендовать»','«Не рекомендовать»','«Недостаточно данных»','AI Studio']:
            self.assertIn(phrase,text)
        for phrase in ['95%','судебных','эмоциональ','разрабатываем']:
            self.assertNotIn(phrase,text)
        for phrase in ['эмоциональный портрет','Telegram','психологический профиль','точность 95%']:
            with self.subTest(phrase=phrase),self.assertRaises(ValueError):
                hr.render_hr({'segment':'recruitment_agency'},p|{'observation':phrase})

    def test_staggered_schedule_does_not_repeat_completed_slots(self):
        c={'enabled':True};now=datetime(2026,10,27,13,30,tzinfo=timezone.utc)
        self.assertEqual(hr.hr_slot(now),'2026-10-27T12:00')
        self.assertEqual(hr.admission(c,now,[]),(True,'new_interval_cycle'))
        self.assertEqual(hr.admission(c,now,[{'slot':hr.hr_slot(now),'state':'completed'}]),(False,'interval_already_completed'))
        self.assertEqual(hr.admission(c,now,[{'slot':'older','state':'waiting_resources'}]),(True,'resume_checkpoint'))
        self.assertEqual(hr.admission(c,now.replace(hour=22),[]),(False,'outside_window'))
        self.assertEqual(hr.admission({'enabled':False},now,[]),(False,'disabled'))

    def test_hr_seed_pipeline_persists_and_replays_without_more_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);profile=root/'product-brief.json';profile.write_text('{}')
            company={'company_name':'Тестовый Рекрутинг','company_domain':'recruit.example','segment':'recruitment_agency',
                     'source_urls':['https://recruit.example/about','https://recruit.example/process','https://recruit.example/contact']}
            (root/'seed-companies.json').write_text(json.dumps([company]))
            texts=['Тестовый Рекрутинг — кадровое агентство.',
                   'Проводим интервью и сопоставляем резюме с профессиональными требованиями вакансии.',
                   'Для деловых вопросов: office@recruit.example']
            class Runtime(FakeRuntime):
                def fetch(self,url):
                    return {'ok':True,'url':url,'text':texts[company['source_urls'].index(url)],'fetched_at':'2026-10-27T08:00:00+00:00'}
                def compose(self,*args,**kwargs):
                    self.calls.append('compose')
                    return {'subject':'ИИ для резюме и интервью','observation':'На сайте описано сопоставление резюме с требованиями вакансии.',
                            'question':'Кому можно показать пример готового отчёта?',
                            'pilot_focus':'Предлагаю попробовать на одной вакансии и обезличенных материалах.','offer_kind':'direct_pilot'}
            runtime=Runtime();runtime.companies=[]
            runtime.assessment={'company_name':company['company_name'],'identity_url':company['source_urls'][0],'identity_quote':texts[0],
                'signal_url':company['source_urls'][1],'signal_quote':texts[1],'signal_kind':'profile_process','signal_date':'','date_quote':'',
                'current_unmet_need':False,'use_case':'Сводка резюме и интервью по требованиям вакансии','why_fit':'Подбор персонала',
                'existing_solution':'Неизвестно','remaining_manual_step':'','budget_evidence':'Неизвестно',
                'contact_email':'office@recruit.example','contact_url':company['source_urls'][2],'contact_quote':texts[2],'unknowns':['Спрос неизвестен']}
            runtime.review=deepcopy(REVIEW)|{'need_is_unmet':False,'need_quote_supports_claim':False}
            config={'campaign':'hr-test','database':str(root/'state.sqlite3'),'queries':['поиск HR компаний для резюме'],
                    'max_candidates':1,'max_pages':6,'max_model_calls':12,'max_signal_age_days':180,
                    'timezone':'UTC','enabled':True,'mode':'preview','discovery_outreach':True}
            store=Store(config['database'])
            try:
                with patch.object(hr,'PROFILE',profile):
                    engine=hr.HrEngine(config,store,runtime,FakeCrm(),clock=lambda:datetime(2026,10,27,13,tzinfo=timezone.utc))
                    result=engine.run('2026-10-27T12:00')
                    self.assertTrue(result['ok'],result)
                    row=store.rows('SELECT result,stage FROM candidates')[0]
                    payload=json.loads(row['result'])
                    self.assertEqual(payload['lead']['qualification_status'],'contact_ready')
                    self.assertEqual(payload['lead']['intent_score'],0)
                    self.assertIn('10–15 минут',payload['draft']['body'])
                    before=list(runtime.calls);engine.run('2026-10-27T12:00')
                    self.assertEqual(runtime.calls,before)
            finally:store.close()

    def test_ocr_defaults_unchanged(self):
        self.assertEqual(Engine.allowed_segments,('bankruptcy_legal','crm_integrator'))
        self.assertNotIn('recruitment_agency',Engine.allowed_segments)
        self.assertIn('recruitment_agency',hr.HrEngine.allowed_segments)
