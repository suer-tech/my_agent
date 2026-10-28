import json
from pathlib import Path
from iva_integration.web_browser import browser_request
r=browser_request({'action':'open','url':'https://www.python.org'})
result={'ok':r.get('ok'),'title':r.get('title'),'status':r.get('http_status'),'error':r.get('error')}
Path('data/server-browser-verification.json').write_text(json.dumps(result));print(json.dumps(result),flush=True)
browser_request({'action':'close'})
