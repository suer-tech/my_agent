"""Isolated live read-only Iva turns; never sends a Telegram message."""
import json
import sys
from pathlib import Path
sys.path.insert(0,'/opt/iva')
from local_finance.iva_telegram import EveBridge

root=Path('/opt/iva/data/repair-20261024/replies')
root.mkdir(parents=True,exist_ok=True)
bridge=EveBridge(state_dir=root,skip_daily=True)
results=[]
for number,prompt in enumerate([
 'Это техническая проверка обычного ответа. Не вызывай инструменты записи и не отправляй уведомления. Назови столицу Франции одним словом.',
 'Это продолжение технической проверки. Какую столицу ты только что назвала? Ответь одним словом; ничего не записывай и не отправляй.',
 'Используй только чтение памяти: найди название фильма, который ты раньше советовала владельцу. Если точного сведения нет, так и скажи. Ничего не сохраняй и не отправляй.',
 'Только чтение: проверь status почты и список расписаний. Одним коротким предложением скажи, включена ли проверка новых писем. Ничего не создавай, не изменяй и не отправляй.',
 'Проверка после обновления поколения движка. Назови столицу Франции одним словом, никаких действий.'
]):
 try:results.append({'step':number,'ok':True,'answer':bridge.send(prompt,'diagnostic:reply-repair-20261024:'+str(number))})
 except Exception as exc:results.append({'step':number,'ok':False,'error_type':type(exc).__name__})
 (root/'verification.json').write_text(json.dumps(results,ensure_ascii=False))
