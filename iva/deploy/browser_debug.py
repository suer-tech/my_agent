import faulthandler,json
faulthandler.dump_traceback_later(20)
from playwright.sync_api import sync_playwright
print('driver start',flush=True)
with sync_playwright() as p:
 print('launch',flush=True)
 b=p.chromium.launch(headless=True,timeout=25000)
 print('page',flush=True)
 page=b.new_page()
 page.goto('https://example.com',timeout=15000)
 print(json.dumps({'title':page.title()}),flush=True)
 b.close()
