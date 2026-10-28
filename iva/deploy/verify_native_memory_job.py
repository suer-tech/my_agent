"""Run the deployed night processor on a disposable vault, leaving owner memory intact."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.memory_scheduler import run_native,APP
with tempfile.TemporaryDirectory(prefix='native-memory-check-',dir=ROOT/'data') as tmp:
    base=Path(tmp)
    vault=base/'vault'
    shutil.copytree(APP/'vault',vault,ignore=shutil.ignore_patterns('.git'))
    day='2026-01-03'
    (vault/'daily'/f'{day}.md').write_text(
        '# Проверка памяти\n\n## 12:00 [owner telegram]\n'
        'Тестовый проект Янтарный Маяк: цвет кнопок оранжевый, номер макета 853. '
        'Это обычное сведение о проекте, не пароль.\n')
    result=run_native(vault=vault,data=base/'state',day=day)
    summary=vault/'summaries/daily'/f'{day}.md'
    text=summary.read_text() if summary.exists() else ''
    result={'exit_code':result['exit_code'],'summary_exists':summary.exists(),
            'fact_preserved':'853' in text,'summary':text,'owner_vault_modified':False}
    out=ROOT/'data/native-memory-job-verification.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2));out.chmod(0o600)
    print('native_memory_job',result['exit_code'],result['fact_preserved'])
