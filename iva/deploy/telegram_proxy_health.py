"""Root-owned systemd probe. No Telegram token, owner data, or financial retries."""
from pathlib import Path
from contextlib import contextmanager,closing
from datetime import datetime
import json
import sqlite3
import subprocess
import time
import urllib.request

STATE=Path('/run/iva-telegram-proxy-health.json')
PROXY='http://10.203.77.2:8888'
ROOT=Path('/opt/iva')


@contextmanager
def deployment_guard(root=ROOT):
    """Never interfere with an intentional stop/build/rollback."""
    import fcntl
    with (root/'data/application-deploy.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:yield False;return
        try:yield True
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def restart_proxy():
    with deployment_guard() as allowed:
        if not allowed:return False
        subprocess.run(['systemctl','restart','iva-telegram-proxy'],check=True,timeout=25,capture_output=True)
        return True


def consumer_issue(clock,root=ROOT,unit_state=None):
    """Detect a stopped failed unit or stuck idle poller; never kill an owner turn."""
    if (root/'data/iva-telegram/stop').exists():return None
    if unit_state is None:
        r=subprocess.run(['systemctl','show','iva@telegram','--property=ActiveState','--value'],capture_output=True,text=True,timeout=5)
        unit_state=r.stdout.strip()
    if unit_state=='failed':return 'failed_unit'
    if unit_state!='active':return None
    try:
        value=json.loads((root/'data/iva-telegram/status.json').read_text())
        heartbeat=datetime.fromisoformat(value['heartbeat']).timestamp()
        if clock-heartbeat<180:return None
        with closing(sqlite3.connect('file:'+str(root/'data/local-finance.sqlite3')+'?mode=ro',uri=True,timeout=2)) as db:
            busy=db.execute("SELECT count(*) FROM local_updates WHERE state IN ('processing','sending')").fetchone()[0]
        if busy:return 'owner_turn_in_progress'
        return 'stale_idle_poll'
    except (OSError,ValueError,KeyError,sqlite3.Error):return 'status_unavailable'


def recover_consumer(clock,root=ROOT):
    with deployment_guard(root) as allowed:
        if not allowed:return 'deployment_in_progress'
        issue=consumer_issue(clock,root)
        if issue in ('failed_unit','stale_idle_poll'):
            subprocess.run(['systemctl','restart','iva@telegram'],check=True,capture_output=True,timeout=20)
            return 'restarted_idle_consumer'
        return issue or 'ok'


def probe():
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':PROXY}))
    try:
        with opener.open('https://api.telegram.org',timeout=12) as response:return response.status==200
    except Exception:return False


def tick(check=probe,restart=None,clock=None,state=STATE,consumer=None):
    clock=time.time() if clock is None else clock
    try:saved=json.loads(state.read_text())
    except (OSError,ValueError):saved={}
    ok=check();failures=0 if ok else saved.get('failures',0)+1
    last=saved.get('last_restart',0);restarted=False
    if failures>=2 and clock-last>=300:
        # Persist rate limiting even if systemctl itself fails.
        last=clock
        temporary=state.with_suffix('.tmp')
        temporary.write_text(json.dumps({**saved,'last_restart':last,'failures':failures,'checked_at':clock,'healthy':False}))
        temporary.replace(state)
        try:
            executed=(restart or restart_proxy)()
            restarted=executed is not False
        except Exception:restarted=False
        if restarted:ok=check()
        if ok:failures=0
    consumer_state=saved.get('consumer_state');consumer_restart=saved.get('consumer_restart',0)
    if ok and clock-consumer_restart>=300:
        try:consumer_state=consumer(clock) if consumer is not None else None
        except Exception:consumer_state='check_failed'
        if consumer_state=='restarted_idle_consumer':consumer_restart=clock
    result={'healthy':ok,'failures':failures,'checked_at':clock,'last_restart':last,'restarted':restarted,'consumer_state':consumer_state,'consumer_restart':consumer_restart}
    temporary=state.with_suffix('.tmp');temporary.write_text(json.dumps(result));temporary.replace(state)
    print('Telegram proxy health: '+('ok' if ok else 'unavailable')+('; restarted' if restarted else ''))
    return result


if __name__=='__main__':tick(consumer=recover_consumer)
