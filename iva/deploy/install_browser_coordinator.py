"""Install shared browser turns without touching campaign authorization or credentials."""
from pathlib import Path
import grp
import hashlib
import json
import os
import shutil
import subprocess
import time

ROOT=Path('/opt/iva'); MAX=Path('/opt/max')
BACKUP=Path('/opt/max-deploy/before-browser-turns')


def replace(text, old, new):
    if old not in text: raise RuntimeError('Runtime source anchor changed: '+old[:85])
    return text.replace(old,new,1)


def main():
    BACKUP.mkdir(mode=0o700,exist_ok=True)
    changes={}
    for name in ('find_and_click.py','daily_schedule.py','ads_worker.py','broadcast_worker.py','manual_browser.py'):
        raw=(MAX/name).read_text()
        if '# IVA_BROWSER_TURNS_V1' in raw: continue
        data='# IVA_BROWSER_TURNS_V1\nimport browser_turn as iva_turn\n'+raw
        if name=='find_and_click.py':
            data=replace(data,'with open("found_links_from_pages.txt", "w", encoding="utf-8") as f:',
                         'with open("found_links_from_pages.txt", "a", encoding="utf-8") as f:')
            data=data.replace('✓ Файл found_links_from_pages.txt очищен перед началом работы',
                              '✓ Файл found_links_from_pages.txt сохранён для продолжения работы')
            data=replace(data,'                for i, link in links_batch:\n',
                '                for i, link in links_batch:\n'
                '                    if not schedule and iva_turn.completed(link):\n                        continue\n'
                '                    iva_turn.checkpoint(stage="before_link", next_index=i, next_url=link, source_file=links_file)\n')
            data=replace(data,'                    # Статистика каждые 10 ссылок',
                '                    if not schedule and result and result[1] != [TEMPORARY]:\n'
                '                        iva_turn.record_completed(link)\n'
                '                    iva_turn.checkpoint(stage="link_saved", last_processed_url=link, next_url=None, next_index=None)\n\n'
                '                    # Статистика каждые 10 ссылок')
            # Non-combined legacy delays yield only after the current link's data is saved.
            data=replace(data,'                            time.sleep(LINKS_PAUSE_MINUTES * 60)',
                '                            if result and result[1] != [TEMPORARY]:\n                                iva_turn.record_completed(link)\n'
                '                            for _ in range(int(LINKS_PAUSE_MINUTES * 60)):\n'
                '                                iva_turn.checkpoint(stage="between_links")\n                                time.sleep(1)')
        elif name=='daily_schedule.py':
            data=replace(data,'        while self.clock() < until:\n',
                '        while self.clock() < until:\n            iva_turn.checkpoint(stage="quota_wait", source_file=self.source_file)\n')
        elif name=='ads_worker.py':
            data=replace(data,'    for url in run.pending():\n',
                '    for url in run.pending():\n        iva_turn.checkpoint(stage="classification_next", next_url=url, source_file=run.mode)\n')
            data=replace(data,'        run.save(url, group, result)\n',
                '        run.save(url, group, result)\n        iva_turn.checkpoint(stage="classification_saved", last_completed_url=url, next_url=None)\n')
        elif name=='broadcast_worker.py':
            data=replace(data,'    while True:\n        if stopped():\n            break\n        refresh()\n',
                '    while True:\n        iva_turn.checkpoint(stage="between_deliveries")\n        if stopped():\n            break\n        refresh()\n')
            data=replace(data,'        while not stopped():\n',
                '        while not stopped():\n            iva_turn.checkpoint(stage="delivery_wait", next_url=row["recipient"])\n')
            data=replace(data,"        progress('sending', row)\n",
                "        # Never yield between sending-state persistence and acknowledgement.\n        progress('sending', row)\n")
            data=replace(data,"        launch['enabled'] = False\n        write_json(directory/'broadcast_launch.json', launch)",
                "        if not iva_turn.YIELDED:\n            launch['enabled'] = False\n            write_json(directory/'broadcast_launch.json', launch)")
        else:
            data=replace(data,'target_url = "https://web.max.ru/"',
                'target_url = iva_turn.restore_url("https://web.max.ru/")')
            data=replace(data,'            while not stopped and context.pages:\n',
                '            while not stopped and context.pages:\n                iva_turn.checkpoint(stage="manual_idle")\n')
            data=data.replace('                time.sleep(.2)','                time.sleep(1)')
            data=replace(data,'            context.close()',
                '            iva_turn.remember_pages(context)\n            context.close()')
        compile(data,name,'exec');changes[name]=data
    for name,data in changes.items():
        target=MAX/name
        if not (BACKUP/name).exists(): shutil.copy2(target,BACKUP/name)
        target.write_text(data)
    lib=Path('/usr/local/lib/iva-browser');lib.mkdir(exist_ok=True)
    for name in ('browser_turn.py','browser_coordinator.py'):
        shutil.copy2(ROOT/'deploy'/name,lib/name);os.chown(lib/name,0,0);os.chmod(lib/name,0o644)
    shutil.copy2(ROOT/'deploy/max_browser_guard.py',MAX/'runtime-guard/sitecustomize.py')
    link=MAX/'runtime-guard/browser_turn.py'
    if not link.exists():link.symlink_to(lib/'browser_turn.py')
    state=Path('/var/lib/iva-browser');state.mkdir(exist_ok=True)
    os.chown(state,0,grp.getgrnam('iva').gr_gid);os.chmod(state,0o770)
    (state/'checkpoints').mkdir(exist_ok=True)
    os.chown(state/'checkpoints',0,grp.getgrnam('iva').gr_gid);os.chmod(state/'checkpoints',0o770)
    lock=state/'owner.lock';lock.touch(exist_ok=True);os.chown(lock,0,grp.getgrnam('iva').gr_gid);os.chmod(lock,0o660)
    unit='''[Unit]
Description=Exclusive cooperative turns for MAX and Threads browsers
After=network.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/iva-browser/browser_coordinator.py
RuntimeDirectory=iva-browser
RuntimeDirectoryMode=0755
Restart=on-failure
RestartSec=5
UMask=0077
MemoryMax=64M
CPUQuota=10%
[Install]
WantedBy=multi-user.target
'''
    Path('/etc/systemd/system/iva-browser-coordinator.service').write_text(unit)
    drop=Path('/etc/systemd/system/threads-browser.service.d');drop.mkdir(exist_ok=True)
    (drop/'turns.conf').write_text('[Unit]\nWants=iva-browser-coordinator.service\nAfter=iva-browser-coordinator.service\n[Service]\nEnvironment=PYTHONPATH=/usr/local/lib/iva-browser\nKillMode=mixed\nSendSIGKILL=no\nTimeoutStopSec=100\n')
    for name in ('max130-worker','max130-ads','max130-broadcast','max130-manual'):
        drop=Path('/etc/systemd/system/'+name+'.service.d');drop.mkdir(exist_ok=True)
        (drop/'90-browser-turns.conf').write_text('[Unit]\nWants=iva-browser-coordinator.service\nAfter=iva-browser-coordinator.service\n[Service]\nSendSIGKILL=no\nRestart=no\n')
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','iva-browser-coordinator'],check=True)
    print(json.dumps({'patched_files':list(changes),'backup':str(BACKUP),'campaigns_started':0}))


if __name__=='__main__': main()
