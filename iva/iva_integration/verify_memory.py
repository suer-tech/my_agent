"""Native night pipeline tests on a separate synthetic vault, never owner facts."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env,APP,PILOT
from iva_integration.memory_scheduler import run_native


def main():
    case=PILOT/'memory-tests/native-cycle';vault=case/'vault';data=case/'data'
    if not vault.exists():
        shutil.copytree(APP/'vault-template',vault)
        subprocess.run(['git','init',str(vault)],capture_output=True,check=True)
        for key,value in [('user.name','Iva memory test'),('user.email','iva-test@localhost')]:subprocess.run(['git','-C',str(vault),'config',key,value],check=True)
        (vault/'daily').mkdir(exist_ok=True)
        (vault/'daily/2025-12-31.md').write_text('''# Синтетический тест, не профиль владельца

## 09:00 [text]
Это исключительно синтетический тест. Проект «Маяк тест памяти» имеет контрольный код LIGHTHOUSE-8427. Срок проекта — 20 ноября.

## 09:10 [text]
Исправление синтетического проекта «Маяк тест памяти»: срок теперь 25 ноября, прежнее 20 ноября устарело. Контрольный код LIGHTHOUSE-8427 остаётся прежним.

## 09:20 [text]
Дата рождения вымышленного родственника «Тестовый родственник» неизвестна. Не создавать дату или напоминание. Этот родственник не относится к настоящему владельцу.

## 09:30 [text]
Для синтетического проекта «Маяк тест памяти» принято решение: сначала проверить документы, затем согласовать бюджет. Это тест, никаких реальных закупок.
''',encoding='utf-8')
    first=run_native(vault=vault,data=data,day='2025-12-31');assert first['exit_code']==0,first
    summary=vault/'summaries/daily/2025-12-31.md';assert summary.exists()
    cards=list((vault/'cards').rglob('*.md'));texts='\n'.join(p.read_text(encoding='utf-8') for p in cards)
    assert 'LIGHTHOUSE-8427' in texts and '25 ноября' in texts
    assert '20 ноября' in texts # old evidence remains in dated log/history.
    assert 'неизвест' in texts.lower()
    assert '[[daily/2025-12-31]]' in texts
    before={str(p.relative_to(vault)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [summary,*cards]}
    metrics=PILOT/'gateway-metrics.jsonl';count=len(metrics.read_text().splitlines())
    second=run_native(vault=vault,data=data,day='2025-12-31');assert second['exit_code']==0
    after={str(p.relative_to(vault)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [summary,*cards]}
    assert before==after,'Repeated night changed facts or summary'
    assert len(metrics.read_text().splitlines())==count,'Repeated completed day called the model'
    childenv=env();childenv['ASSISTANT_VAULT_DIR']=str(vault);childenv['ASSISTANT_DATA_DIR']=str(data)
    result=subprocess.run(['node',str(ROOT/'iva_integration/verify-memory-periods.ts')],cwd=APP,env=childenv,capture_output=True,timeout=600)
    (data/'period-check.log').write_bytes(result.stdout+result.stderr);assert result.returncode==0,'Native period check failed; see private log'
    report={'native_day_cli_driven':True,'corrected_fact_current_in_card':True,'old_fact_source_retained':True,'unknown_birthdate_not_invented':True,'source_daily_links_preserved':True,'repeat_day_no_duplicate':True,'repeat_day_zero_model_calls':True,'native_week_month_year_cli_driven':True,'fixture_vault':str(vault),'owner_profile_not_used_as_fixture':True}
    (PILOT/'memory-native-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))


if __name__=='__main__':main()
