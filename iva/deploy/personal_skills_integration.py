"""Small idempotent insertions into the latest shared files, never old copies."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.personal_skills_policy import GUIDANCE

ENDPOINT='''            if self.path=='/personal-skills':
                from iva_integration.personal_skills import execute
                session_path=ROOT/'data/iva-telegram/session.json'
                session=json.loads(session_path.read_text(encoding='utf-8')) if session_path.exists() else {}
                ledger=Ledger(ROOT/'data/local-finance.sqlite3') if data.get('action') in ('save','archive','remember') else None
                try:
                    try:
                        result=execute(data,ledger,OWNER,session.get('active_source'),state=ROOT/'data/personal-skills',skills=ROOT/'data/iva-pilot/app/data/custom/agent/skills')
                    except (ValueError,PermissionError,TypeError,KeyError) as exc:result={'ok':False,'error':str(exc)}
                    return self.reply(200,result)
                finally:
                    if ledger:ledger.db.close()
'''


def once(s,old,new):
    if new in s:return s
    if s.count(old)!=1:raise ValueError('Shared source changed; expected exactly one insertion point: '+old[:70])
    return s.replace(old,new,1)


def transform(relative,s):
    if relative=='iva_integration/gateway.py':
        if "'personal_skills'" not in s:s=once(s,"'user_update'}","'user_update','personal_skills'}")
        s=s.replace('You can load skills but cannot currently write/create skills yourself.','You can load and create personal skills through personal_skills; they become discoverable on the next turn.')
        if 'PERSONAL SKILLS:' not in s:
            marker='        # Load maintained financial guidance per step, including existing sessions.'
            s=once(s,marker,'        instruction+='+repr('\n'+GUIDANCE)+'\n'+marker)
        if "self.path=='/personal-skills'" not in s:
            marker="            if self.path=='/reply-format':"
            s=once(s,marker,ENDPOINT+marker)
    elif relative=='iva_integration/prepare_pilot.py':
        if "'personal_skills.ts'" not in s:
            s=once(s,"'user_update.ts'}","'user_update.ts','personal_skills.ts'}")
            marker="shutil.copy2(ROOT/'iva_integration/user_update.ts',APP/'agent/tools/user_update.ts')"
            s=once(s,marker,marker+"\nshutil.copy2(ROOT/'iva_integration/personal_skills.ts',APP/'agent/tools/personal_skills.ts')\nshutil.copytree(ROOT/'iva_integration/skills/create-personal-skill',APP/'agent/skills/create-personal-skill',dirs_exist_ok=True)")
    elif relative=='local_finance/iva_telegram.py':
        if 'CONVERSATION_GUIDANCE+=PERSONAL_SKILLS_GUIDANCE' not in s:
            marker='\n\nclass EveBridge:'
            s=once(s,marker,'\nfrom iva_integration.personal_skills_policy import GUIDANCE as PERSONAL_SKILLS_GUIDANCE\nCONVERSATION_GUIDANCE+=PERSONAL_SKILLS_GUIDANCE\n'+marker)
    elif relative=='deploy/backup.py':
        if "personal_copy=Path(d)/'personal-skills.sqlite3'" not in s:
            marker="        with tarfile.open(temporary,'w:gz') as archive:"
            addition="""        personal=ROOT/'data/personal-skills/state.sqlite3'
        personal_copy=Path(d)/'personal-skills.sqlite3'
        if personal.exists():
            source=sqlite3.connect(f'file:{personal}?mode=ro',uri=True);copy=sqlite3.connect(personal_copy)
            try:
                source.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Personal skills backup integrity failed')
            finally:source.close();copy.close()
"""
            s=once(s,marker,addition+marker)
            marker="            archive.add(path,arcname='data/local-finance.sqlite3')"
            s=once(s,marker,marker+"\n            if personal_copy.exists():archive.add(personal_copy,arcname='data/personal-skills/state.sqlite3')")
            marker="'data/tender-research']"
            s=once(s,marker,"'data/tender-research','data/iva-pilot/app/data/custom/agent/skills']")
    else:raise ValueError('Unlisted integration file')
    compile(s,relative,'exec')
    return s


SHARED=('iva_integration/gateway.py','iva_integration/prepare_pilot.py','local_finance/iva_telegram.py','deploy/backup.py')
