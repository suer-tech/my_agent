"""Real extraction and Sol original review on a private SQLite copy only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
if ROOT.name=='source':ROOT=Path('/opt/iva')
sys.path.insert(0,str(ROOT))
from local_finance.assistant import Ledger,Assistant
from local_finance.codex_cli import Codex
from iva_integration.run_pilot import env

DOCUMENT='af596bbf80ce8f8720606b554a55c0d8ff212f2e659a974d4ed0485368878fb7'


def stage_modules(source):
    import local_finance,iva_integration
    for name in ('local_finance.financial_screens','local_finance.document_vision','local_finance.attachments','iva_integration.finance_agent'):
        path=source/Path(name.replace('.','/')+'.py')
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module;setattr(sys.modules[name.rsplit('.',1)[0]],name.rsplit('.',1)[1],module);spec.loader.exec_module(module)
        if name.endswith('finance_agent'):module.ROOT=ROOT


def fingerprint(l):return hashlib.sha256(json.dumps(l.rows('SELECT * FROM operations ORDER BY id'),sort_keys=True).encode()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--release',required=True,type=Path);p.add_argument('--source',type=Path);p.add_argument('--skip-extract',action='store_true');args=p.parse_args()
    os.environ.update(env())
    if args.source:stage_modules(args.source)
    from local_finance.attachments import SCREEN_PROMPT,SCREEN_SCHEMA,normalize_extraction
    from local_finance import financial_screens
    from local_finance.document_vision import originals
    from iva_integration.finance_agent import run
    release=args.release;report={}
    with tempfile.TemporaryDirectory(prefix='screen-verification-') as temp:
        path=Path(temp)/'copy.sqlite3'
        c=sqlite3.connect(ROOT/'data/local-finance.sqlite3');copy=sqlite3.connect(path);c.backup(copy);copy.close();c.close()
        l=Ledger(path);Assistant(l,None);before=fingerprint(l)
        if args.skip_extract:
            extraction=json.loads((release/'verified-extraction.json').read_text())
        else:
            cli=Codex(timeout=240)
            with originals(l,DOCUMENT,storage_root=ROOT) as (info,images):
                prompt=SCREEN_PROMPT+'\n'+json.dumps({'original_pages':info['attached_pages'],'context':'Владелец сообщил, что это новый, ранее не учтённый кредит Ozon. Годы из слов владельца неизвестны.'},ensure_ascii=False)
                raw=cli.structured(prompt,SCREEN_SCHEMA,images=images)
            (release/'raw-page-extraction.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding='utf-8')
            extraction=financial_screens.finalize(normalize_extraction(raw))
            (release/'verified-extraction.json').write_text(json.dumps(extraction,ensure_ascii=False,indent=2),encoding='utf-8')
            report['extraction_model']={'model':cli.model,'reasoning':cli.reasoning_effort,'images':cli.last_execution.get('images')}
        rows=extraction['rows'];facts=extraction['summary_fields']
        assert len(rows)==17,('Payment rows',len(rows))
        assert len(extraction['page_extractions'])==4
        assert all(r['date'] is None for r in rows),'Undocumented years must not be invented'
        assert sum(r['payment']=='206.00' for r in rows)==16 and sum(r['payment']=='177.87' for r in rows)==1
        assert any(f['kind']=='principal_total' and f['value']=='2407.16' for f in facts)
        assert any(f['kind']=='interest_total' and f['value']=='1066.71' for f in facts)
        assert all(c['matches'] is True for c in extraction['arithmetic_checks'])
        old=l.rows('SELECT * FROM source_documents WHERE id=?',(DOCUMENT,))[0]
        extraction['original_members']=json.loads(old['extraction']).get('original_members',[])
        financial_screens.persist(l,DOCUMENT,old['path'],'loan_schedule',4,extraction)
        assert fingerprint(l)==before
        l.db.close()
        report['extraction']={'payments':len(rows),'pages':4,'years_unknown':True,'sums_match':True,'principal':'2407.16','interest_total':'1066.71'}
        # Explicitly isolated owner-authorized write: prove original review is
        # usable by the real specialist and readable amounts are not discarded.
        body='Это новый кредит Ozon. Сохрани ясные сведения о нём по присланным скриншотам, перепроверив оригиналы. Не выдумывай годы графика. Доходов, расходов и отметок оплаты не создавай. Если для календаря неизвестен год — сохрани известные условия, а уточни только год.'
        task=body+' Документ '+DOCUMENT+'. Используй finance_review_document, чтобы посмотреть сами картинки. Это изолированная проверка на копии реестра.'
        result=run(task,'verification:screens:ozon',db_path=path,body_override=body)
        l=Ledger(path)
        steps=[]
        for step in result.get('steps',[]):
            call=json.loads(step['signature']);params=json.loads(call['arguments_json'])
            steps.append({'tool':step['tool'],'ok':step['ok'],'action':params.get('action')})
        loans=l.rows("SELECT * FROM reported_loans WHERE lower(name) LIKE '%ozon%'")
        report['specialist']={'ok':result.get('ok'),'steps':steps,'text':result.get('text'),'loans':loans,'operations_unchanged':fingerprint(l)==before,'bank_schedule_count':len(l.rows('SELECT * FROM bank_schedules WHERE document_id=?',(DOCUMENT,)))}
        report['passed']=bool(result.get('ok') and any(s['tool']=='finance_review_document' and s['ok'] for s in steps) and loans and loans[0]['monthly_payment']==20600 and report['specialist']['operations_unchanged'] and report['specialist']['bank_schedule_count']==0)
        report['cached_repeat']=run(task,'verification:screens:ozon',db_path=path,body_override=body).get('cached',False)
        l.db.close()
    (release/'model-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='specialist'},ensure_ascii=False),flush=True)
    print(json.dumps({k:v for k,v in report['specialist'].items() if k!='loans'},ensure_ascii=False),flush=True)
    if not report['passed'] or not report['cached_repeat']:raise RuntimeError('Real screen verification failed')


if __name__=='__main__':main()
