"""One explicitly authorized server-side Threads recommendation test, never recurring."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from urllib.parse import urlsplit,urlunsplit

ROOT=Path('/opt/iva/data/threads-browser')
RUN='owner-first-live-test-20261023'
STORE='https://chromewebstore.google.com/detail/ppgijillphlomodnhnilgmcceigedpnp'


def rpc(action,**data):
    ident=uuid.uuid4().hex
    p=ROOT/'rpc-request.json';tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps({'id':ident,'action':action,**data},ensure_ascii=False));tmp.chmod(0o600);tmp.replace(p)
    end=time.monotonic()+90
    while time.monotonic()<end:
        p=ROOT/'rpc-response.json'
        if p.exists():
            result=json.loads(p.read_text())
            if result.get('id')==ident:
                if not result.get('ok'):raise RuntimeError(result.get('error'))
                return result['snapshot']
        time.sleep(.5)
    raise RuntimeError('Browser command result unknown; no automatic retry')


def canonical(url):
    u=urlsplit(url);return urlunsplit(('https','www.threads.com',u.path,'',''))


def database():
    db=sqlite3.connect(ROOT/'promotion.sqlite3');db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,state TEXT,post_url TEXT,comment TEXT,reason TEXT,result TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_recommendation_per_post ON runs(post_url) WHERE post_url IS NOT NULL;''')
    return db


def save_snapshot(name,data):
    p=ROOT/(name+'.json');p.write_text(json.dumps(data,ensure_ascii=False,indent=2));p.chmod(0o600)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['discover','inspect','draft','publish','status'])
    parser.add_argument('--url');parser.add_argument('--text');parser.add_argument('--button');args=parser.parse_args()
    ROOT.mkdir(exist_ok=True)
    with (ROOT/'promotion.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=database()
        with db:db.execute('INSERT OR IGNORE INTO runs(id,state) VALUES (?,?)',(RUN,'created'))
        row=db.execute('SELECT * FROM runs WHERE id=?',(RUN,)).fetchone()
        if args.action=='status':print(json.dumps(dict(row),ensure_ascii=False));return
        if row['state'] in ('sending','uncertain','sent'):raise RuntimeError('This one-comment test already attempted publication; inspect status, never retry')
        if args.action=='discover':
            snap=rpc('open',url='https://www.threads.com/')
            if 'New thread' not in snap['text'] and 'Новая ветка' not in snap['text']:raise RuntimeError('Signed-in creation navigation missing')
            snaps=[snap]
            for _ in range(2):snaps.append(rpc('scroll'))
            save_snapshot('test-discovery',snaps)
            with db:db.execute('UPDATE runs SET state=? WHERE id=?',('discovered',RUN))
            print(json.dumps(snaps,ensure_ascii=False));return
        if args.action=='inspect':
            url=canonical(args.url)
            snap=rpc('open',url=url);save_snapshot('test-discussion',snap)
            with db:db.execute('UPDATE runs SET state=?,post_url=? WHERE id=?',('inspected',url,RUN))
            print(json.dumps(snap,ensure_ascii=False));return
        if args.action=='draft':
            if row['state']!='inspected':raise RuntimeError('Inspect a real discussion first')
            text=args.text or ''
            if not 30<=len(text)<=500 or STORE not in text:raise ValueError('One short product recommendation with the official store link required')
            if not any(x in text.lower() for x in ('я делаю','я сделал','я разработ','моё расширение','мое расширение')):raise ValueError('Disclose creator ownership')
            with db:db.execute('UPDATE runs SET state=?,comment=? WHERE id=?',('drafted',text,RUN))
            print(json.dumps({'state':'drafted','text':text,'post_url':row['post_url']},ensure_ascii=False));return
        if args.action=='publish':
            if row['state']!='drafted':raise RuntimeError('Durable draft required')
            snap=rpc('open',url=row['post_url'])
            snap=rpc('snapshot')
            # The exact visible Reply button is supplied from the inspected DOM.
            if not snap['editors']:
                snap=rpc('click',role='button',name=args.button or 'Reply')
            if any(button.get('label')=='Expand composer' for button in snap['buttons']):
                snap=rpc('click',role='button',name='Expand composer')
            save_snapshot('test-reply-editor',snap)
            snap=rpc('fill',text=row['comment'])
            snap=rpc('snapshot')
            if canonical(snap['url'])!=row['post_url']:raise RuntimeError('Thread changed; refuse publication')
            save_snapshot('test-before-send',snap)
            buttons=[x for x in snap['buttons'] if x['text'] in ('Post','Reply','Опубликовать','Ответить')]
            names=[x['text'] for x in buttons]
            # Long discussions can place the modal footer after the bounded button inventory.
            # Its rendered text is still observed; the browser enforces unique exact role matching.
            if not names and '\nPost\nPost Options' in snap['text'] and snap['editors']:
                names=['Post']
            if len(names)!=1:raise RuntimeError('A unique visible submission button was not found; draft retained')
            # Commit BEFORE clicking. A crash/timeout never permits an automatic second click.
            with db:db.execute('UPDATE runs SET state=? WHERE id=?',('sending',RUN))
            try:
                snap=rpc('click',role='button',name=names[0]);save_snapshot('test-after-send',snap)
                time.sleep(2);snap=rpc('snapshot')
                prefix=' '.join(row['comment'].split('https://')[0].split())
                matches=[x for x in snap['links'] if prefix in ' '.join(x['context'].split()) and '@suer_stuff/post/' in x['url']]
                proof={'links':matches,'snapshot':snap}
                # The editable draft is not proof of publication. Require own post permalink.
                state='sent' if matches else 'uncertain'
                with db:db.execute('UPDATE runs SET state=?,result=? WHERE id=?',(state,json.dumps(proof,ensure_ascii=False),RUN))
                print(json.dumps({'state':state,'links':matches},ensure_ascii=False))
            except BaseException:
                with db:db.execute('UPDATE runs SET state=? WHERE id=?',('uncertain',RUN))
                raise


if __name__=='__main__':main()
