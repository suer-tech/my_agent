"""Read-only live connectivity plus isolated real-model research acceptance."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
os.environ.update(env())
from iva_integration.tender_research import Research,read_resource
from iva_integration.tender_eis import fetch,search_url,parse_page
from local_finance.codex_cli import Codex


def operations():
    with sqlite3.connect(f'file:{ROOT}/data/local-finance.sqlite3?mode=ro',uri=True) as c:
        return hashlib.sha256(json.dumps(c.execute('SELECT * FROM operations ORDER BY 1').fetchall()).encode()).hexdigest()


def main():
    before=operations();result={'started':time.time()}
    html,url=fetch(search_url('программное обеспечение',page=11))
    page=parse_page(html,url)
    result['live_page_11']={k:page[k] for k in ('ok','pagination','not_found_verified')}
    result['live_page_11']['cards']=len(page['cards'])
    assert page['ok'] and (page['cards'] or page['not_found_verified'] or page['pagination']['end_verified']), 'EIS did not return a verified page'
    with tempfile.TemporaryDirectory(prefix='research-acceptance-',dir=ROOT/'data') as folder:
        folder=Path(folder)
        url='https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=745AC6B19A874DE0A55BC0D3D2D9F3EB'
        doc=read_resource(url,folder/'originals')
        result['live_contract']={'ok':doc.get('ok'),'chunks':len(doc.get('chunks',[])),'gap':doc.get('gap'),'sha256':doc.get('sha256')}
        assert doc.get('chunks'), 'Contract did not yield any readable text'
        research=Research(folder/'test.sqlite3',folder/'artifacts')
        job=research.start('ИЗОЛИРОВАННЫЙ ПРИЁМОЧНЫЙ ТЕСТ. Проверь вымышленный пример разработки сайта, условия в документах. Не сохраняй предпочтения.',
                           'diagnostic:research-acceptance',['isolated synthetic query'],False)['job_id']
        def reader(url):
            if 'results.html' in url:
                return {'ok':True,'cards':[{'number':'12345678901','url':'https://zakupki.gov.ru/example?regNumber=12345678901',
                        'title':'Разработка нового веб-сайта','price_text':'500 000 RUB','deadline_text':'01.12.2026',
                        'evidence_text':'Вымышленная закупка для приёмочного теста: разработка сайта, подача заявок открыта.'}],
                        'pagination':{'end_verified':True}}
            if 'specification' in url:
                return {'ok':True,'chunks':[{'location':'ТЗ, пункт 1','text':'Техническое задание тестового примера: создать новый сайт на Python и PostgreSQL. Работа полностью дистанционная; выезды не требуются. Срок 60 дней. Заказчик предоставляет сервер и исходные материалы. Система не обрабатывает персональные данные.'}]}
            if 'contract' in url:
                return {'ok':True,'chunks':[{'location':'Проект контракта, пункты 1–4','text':'Проект контракта тестового примера. Оплата 100% после приёмки за 7 рабочих дней. Исключительные права на новый код передаются заказчику. Требований к предыдущим контрактам и лицензиям нет. Обеспечение заявки не требуется. Тариф площадки не указан и не проверен. Другие расходы неизвестны.'}]}
            return {'ok':True,'chunks':[{'location':'Карточка тестового примера','text':'Разработка сайта 500000 рублей. Подача заявок открыта до 1 декабря 2026 года 12:00 по Москве. Требований к опыту нет. Тариф площадки не подтверждён.'}],
                    'documents':[{'url':'https://zakupki.gov.ru/specification','title':'ТЗ','kind':'document'},
                                 {'url':'https://zakupki.gov.ru/contract','title':'Контракт','kind':'document'}]}
        cli=Codex(timeout=150,role='tenders')
        for _ in range(20):
            research.step(cli,reader)
            status=research.status(job)
            if status['state'] not in ('queued','running'):break
            if research.job(job)['error']:raise RuntimeError('Isolated model step failed: '+research.job(job)['error'])
        status=research.status(job)
        assert status['state'] in ('completed','completed_with_gaps'),status
        assert status['chunks_total']==status['chunks_analyzed']==3,status
        review=json.loads(research.db.execute('SELECT review FROM research_notices').fetchone()[0])
        assert review['decision']!='priority','Unverified platform fee must block priority'
        assert status['delivery']=={},'Synthetic result must not be delivered to Telegram'
        result['isolated_model']={'model':cli.model,'status':status,'review':review}
        research.close()
    result['operations_unchanged']=before==operations()
    assert result['operations_unchanged']
    result['finished']=time.time()
    output=ROOT/'data/research-release-20261021/live-verification.json'
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2));output.chmod(0o600)
    print(json.dumps({'verified':True,'page11':result['live_page_11'],'live_contract':result['live_contract'],
                      'isolated_model':result['isolated_model']['model'],'operations_unchanged':True},ensure_ascii=False))


if __name__=='__main__':main()
