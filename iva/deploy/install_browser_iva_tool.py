"""Add the browser tool to current live sources without replacing unrelated edits."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path('/opt/iva')
sys.path.insert(0,str(ROOT))
from iva_integration.run_pilot import env,APP


def main():
    gateway=ROOT/'iva_integration/gateway.py';text=gateway.read_text()
    backup=ROOT/'data/browser-turns-before';backup.mkdir(mode=0o700,exist_ok=True)
    if not (backup/'gateway.py').exists():shutil.copy2(gateway,backup/'gateway.py')
    if "if self.path=='/browser-control':" not in text:
        a="'personal_mail'}";assert text.count(a)==1
        text=text.replace(a,"'personal_mail','browser_control'}",1)
        a='        from iva_integration.scheduled_tasks import GUIDANCE as SCHEDULED_GUIDANCE'
        assert text.count(a)==1
        text=text.replace(a,a+"\n        from iva_integration.browser_control import GUIDANCE as BROWSER_TURN_GUIDANCE\n        instruction+='\\n'+BROWSER_TURN_GUIDANCE",1)
        a="            if self.path=='/personal-mail':"
        route="""            if self.path=='/browser-control':
                from iva_integration.browser_control import execute
                session_path=ROOT/'data/iva-telegram/session.json'
                session=json.loads(session_path.read_text(encoding='utf-8')) if session_path.exists() else {}
                ledger=Ledger(ROOT/'data/local-finance.sqlite3') if data.get('action')!='status' else None
                try:
                    try:result=execute(data,ledger,OWNER,session.get('active_source'))
                    except (ValueError,PermissionError,RuntimeError,OSError) as exc:result={'ok':False,'error':str(exc)}
                    return self.reply(200,result)
                finally:
                    if ledger:ledger.db.close()
"""
        assert text.count(a)==1;text=text.replace(a,route+a,1)
        compile(text,str(gateway),'exec');gateway.write_text(text)
    shutil.copy2(ROOT/'iva_integration/browser_control.ts',APP/'agent/tools/browser_control.ts')
    # Keep the source in subsequent explicit prepare_pilot runs too.
    p=ROOT/'iva_integration/prepare_pilot.py';s=p.read_text()
    if "'browser_control.ts'" not in s:
        s=s.replace("'scheduled_tasks.ts'}","'scheduled_tasks.ts','browser_control.ts'}")
        a="shutil.copy2(ROOT/'iva_integration/scheduled_tasks.ts',APP/'agent/tools/scheduled_tasks.ts')"
        assert a in s;s=s.replace(a,a+"\nshutil.copy2(ROOT/'iva_integration/browser_control.ts',APP/'agent/tools/browser_control.ts')")
        p.write_text(s)
    subprocess.run(['node','node_modules/eve/bin/eve.js','build'],cwd=APP,env=env(),check=True)
    print('Browser control tool built; no messages or campaign starts')


if __name__=='__main__':main()
