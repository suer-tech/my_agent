"""Real specialist + live EIS, isolated local research state, no Telegram messages."""
import json
import os
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env
from iva_integration.tender_agent import run
os.environ.update(env())

with tempfile.TemporaryDirectory(prefix='tender-check-') as tmp:
    result=run('Проверка поиска: сделай один search active по фразе «программного обеспечения», страница 1. '
               'По реальной выдаче покажи один пример с номером, ссылкой, сроком и оговоркой о соответствии '
               'моему профилю. Не выдавай поставку лицензий за разработку. Если подходящего нет в этой '
               'выборке, скажи это. Не делай дополнительных поисков, не меняй профиль.',
               'diagnostic:tender-search-test',db_path=Path(tmp)/'test.sqlite3')
    report={'ok':result.get('ok'),'model':result.get('model'),'reasoning_effort':result.get('reasoning_effort'),
            'search_executed':any(s['tool']=='search' and s['ok'] for s in result.get('steps',[])),
            'reply':result.get('text')}
    (ROOT/'data/tender-search-verification.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
    if not report['ok'] or not report['search_executed']:raise RuntimeError('Real tender search check failed')
    print('tender_search_complete')
