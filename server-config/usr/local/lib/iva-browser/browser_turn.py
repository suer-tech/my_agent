"""Cooperative browser ownership. Installed root-owned; never reads credentials."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from urllib.parse import urlsplit, urlunsplit

STATE = Path('/var/lib/iva-browser')
SOCKET = '/run/iva-browser/control.sock'
TASKS = {'manual_browser.py':'max-manual','scheduled_worker.py':'max-worker',
         'find_and_click.py':'max-worker','ads_worker.py':'max-ads',
         'broadcast_worker.py':'max-broadcast','distribute_pages_links.py':'max-distribute',
         'threads_browser_login.py':'threads'}
TASK = os.environ.get('IVA_BROWSER_TASK', '')
YIELDED = False
_lock = None


def read(path, default=None):
    try: return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError): return default


def atomic(path, value, mode=0o600):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with tmp.open('w') as out:
        os.chmod(tmp, mode)
        json.dump(value, out, ensure_ascii=False)
        out.flush(); os.fsync(out.fileno())
    tmp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def request(data):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(SOCKET)
        client.sendall(json.dumps(data).encode() + b'\n')
        response = b''
        while b'\n' not in response:
            chunk = client.recv(65536)
            if not chunk: raise RuntimeError('Browser coordinator disconnected')
            response += chunk
        return json.loads(response.split(b'\n', 1)[0])


def admit(task):
    global TASK, _lock
    TASK = task
    os.environ['IVA_BROWSER_TASK'] = task
    # The supervisor grants exactly one service. Fail closed if unavailable.
    state = read(STATE / 'grant.json', {})
    if state.get('task') != task or state.get('yield'):
        request({'action':'enqueue','task':task})
        raise SystemExit(75)
    _lock = (STATE / 'owner.lock').open('r+')
    try: fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit(75)
    state = read(STATE / 'grant.json', {})
    if state.get('task') != task or state.get('yield'): raise SystemExit(75)


def cursor_path(): return STATE / 'checkpoints' / (TASK + '.json')


def launch_key():
    names={'max-worker':'worker_launch.json','max-ads':'ads_launch.json','max-broadcast':'broadcast_launch.json'}
    if TASK not in names: return TASK
    value=read(Path('/opt/max') / names[TASK], {})
    value.pop('enabled', None); value.pop('recheck', None)
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def checkpoint(**details):
    global YIELDED
    if not TASK: return
    old=read(cursor_path(), {})
    atomic(cursor_path(), {**old, **details, 'task':TASK,'updated_at':time.time(),'launch_key':launch_key()})
    grant=read(STATE / 'grant.json', {})
    if grant.get('task') != TASK or grant.get('yield'):
        YIELDED=True
        value=read(cursor_path(), {})
        atomic(cursor_path(),{**value,'yielded':True,'yielded_at':time.time()})
        raise SystemExit(0)


def completed(url):
    if not TASK: return False
    old=read(cursor_path(), {})
    return old.get('launch_key') == launch_key() and url in old.get('completed_urls', [])


def record_completed(url):
    if not TASK: return
    old=read(cursor_path(), {})
    done=old.get('completed_urls', []) if old.get('launch_key') == launch_key() else []
    if url not in done: done.append(url)
    atomic(cursor_path(), {**old,'completed_urls':done,'last_completed_url':url,
                          'launch_key':launch_key(),'updated_at':time.time()})


def remember_pages(context):
    if not TASK: return
    urls=[]
    for page in context.pages:
        u=urlsplit(page.url)
        if u.scheme=='https' and u.hostname in ('web.max.ru','max.ru','www.threads.com','threads.com'):
            urls.append(urlunsplit((u.scheme,u.netloc,u.path,'','')))
    old=read(cursor_path(), {})
    atomic(cursor_path(), {**old,'pages':urls,'updated_at':time.time()})


def restore_url(default):
    old=read(cursor_path(), {})
    urls=old.get('pages', [])
    return urls[0] if urls else default
