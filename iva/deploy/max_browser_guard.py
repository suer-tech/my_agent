"""Deployed as MAX-only sitecustomize.py; production source files stay unchanged."""
import os
from pathlib import Path
import sys

OWNERS={'manual_browser.py','find_and_click.py','scheduled_worker.py','distribute_pages_links.py','ads_worker.py','broadcast_worker.py'}
if Path(sys.argv[0]).name in OWNERS:
    from browser_turn import admit, TASKS
    try:
        admit(TASKS[Path(sys.argv[0]).name])
    except BaseException as exc:
        code=exc.code if isinstance(exc,SystemExit) else 75
        print('MAX: browser turn queued or unavailable; no parallel browser started.',file=sys.stderr,flush=True)
        os._exit(code)
    import fcntl
    try:
        available=next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
        if available<384*1024:
            print('MAX: недостаточно доступной памяти; запуск отложен, Иве оставлен приоритет.',file=sys.stderr,flush=True);os._exit(75)
        _browser_lock=os.open('/opt/max/browser-owner.lock',os.O_CREAT|os.O_RDWR,0o600)
        fcntl.flock(_browser_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (BlockingIOError,OSError):
        print('MAX: браузер занят другой задачей. Одновременно разрешена одна очередь.',file=sys.stderr,flush=True);os._exit(75)
