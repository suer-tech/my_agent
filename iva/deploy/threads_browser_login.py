"""Persistent, private Threads browser; authentication is performed by the owner."""
import fcntl
import json
import os
from pathlib import Path
import time
import signal
import browser_turn as iva_turn
import sys
sys.path.insert(0,'/opt/iva')
from iva_integration.threads_browser_rpc import process_pending
from iva_integration.threads_hourly import read as hourly_read, campaign_roots
from iva_integration.threads_development import step as campaign_step

from playwright.sync_api import sync_playwright

ROOT = Path('/opt/iva/data/threads-browser')
ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
iva_turn.admit('threads')
stopped = False
def stop(*_):
    global stopped
    stopped = True
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
lock = (ROOT / 'browser.lock').open('a')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(75)
available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
if available < 640 * 1024:
    print('Threads browser deferred: insufficient available memory', flush=True)
    raise SystemExit(75)


def save_status(**values):
    tmp = ROOT / 'status.tmp'
    tmp.write_text(json.dumps({'updated_at': int(time.time()), **values}), encoding='utf-8')
    tmp.replace(ROOT / 'status.json')


with sync_playwright() as p:
    save_status(state='starting', authenticated=False, publishing_enabled=False)
    browser = p.chromium.launch_persistent_context(
        str(ROOT / 'profile'), headless=False,
        executable_path='/home/iva/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',
        viewport={'width': 1280, 'height': 900},
        args=['--disable-gpu', '--disable-quic', '--disable-http2', '--window-size=1280,900'],
        timeout=45000,
        proxy={'server': os.environ['THREADS_BROWSER_PROXY']} if os.getenv('THREADS_BROWSER_PROXY') else None,
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    save_status(state='navigating', authenticated=False, publishing_enabled=False)
    try:
        page.goto(iva_turn.restore_url('https://www.threads.com/'), wait_until='domcontentloaded', timeout=45000)
    except Exception as error:
        print('Threads initial navigation:', type(error).__name__, str(error)[:350], flush=True)
        save_status(state='navigation_failed', authenticated=False, publishing_enabled=False)
    try:
        while not stopped:
            iva_turn.checkpoint(stage='idle')
            process_pending(browser)
            campaign_step(browser)
            pages = [x for x in browser.pages if not x.is_closed()]
            # A public profile link alone also exists on the signed-out profile page.
            # Require the signed-in creation UI and absence of the visible login gate.
            def visible_text(tab, text):
                return any(item.is_visible() for item in tab.get_by_text(text, exact=True).all())
            def signed_in(tab):
                self_link = tab.locator('a[href="/@suer_stuff"], a[href="https://www.threads.com/@suer_stuff"]')
                gate = any(visible_text(tab, text) for text in (
                    'Say more with Threads', 'Log in or sign up for Threads',
                    'Continue with Instagram', 'Войти через Instagram'))
                creation = any(visible_text(tab, text) for text in (
                    "What's new?", 'New thread', 'Что нового?', 'Новая ветка'))
                return not gate and creation and any(item.is_visible() for item in self_link.all())
            authenticated = any(
                signed_in(x)
                for x in pages if x.url.startswith('https://www.threads.com/')
            )
            publishing=any((lambda launch:launch.get('publish') and launch.get('enabled'))(hourly_read('hourly-launch.json',{},root=root)) for root in campaign_roots())
            save_status(state='authenticated' if authenticated else 'needs_login',
                        authenticated=authenticated, expected_account='suer_stuff', publishing_enabled=bool(publishing))
            page.wait_for_timeout(10000)
    finally:
        iva_turn.remember_pages(browser)
        browser.close()
        save_status(state='paused_turn' if iva_turn.YIELDED else 'stopped',
                    authentication_verified=False, publishing_enabled=False)
