"""Isolated native-model proof: authored skill + memory across fresh histories."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from iva_integration.personal_skills import Store
from iva_integration.run_pilot import env


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--release',required=True,type=Path);parser.add_argument('--fresh',action='store_true');args=parser.parse_args()
    release=args.release;app=ROOT/'data/iva-pilot/app'
    movie=ROOT/'iva_integration/skills/personal-movie-discovery/SKILL.md'
    policy=ROOT/'iva_integration/skills/create-personal-skill/SKILL.md'
    with tempfile.TemporaryDirectory(prefix='personal-skill-model-') as tmp:
        temp=Path(tmp);state=temp/'state';skills=temp/'skills'
        s=Store(state,skills)
        original=movie.read_text(encoding='utf-8');description=original.split('description: ',1)[1].splitlines()[0];instructions=original.split('---',2)[2].strip()
        s.execute({'action':'save','name':'personal-movie-discovery','title':'Подбор фильмов','description':description,'instructions':instructions,'expected_revision':0,'evidence':'Test seed'},'fixture:seed','Test seed')
        previous={}
        if args.fresh:
            previous=json.loads((release/'model-verification.json').read_text())
            (release/'model-before-fresh.json').write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding='utf-8')
            for row in previous['watched']:
                s.execute({'action':'remember','name':'personal-movie-discovery','key':row['key'],'title':row['title'],'data_json':json.dumps(row['data'],ensure_ascii=False),'expected_revision':0,'evidence':'Test seed'},'fixture:seed:'+row['key'],'Test seed')
        s.close()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                req=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                s=Store(state,skills)
                try:
                    try:value=s.execute(req['input'],req['source'],req['body'])
                    except (ValueError,PermissionError,TypeError) as exc:value={'ok':False,'error':str(exc)}
                finally:s.close()
                data=json.dumps(value,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        cfg={'app':str(app),'endpoint':f'http://127.0.0.1:{server.server_port}/','definition':str(ROOT/'iva_integration/personal_skills.ts'),'policy':str(policy),'movie':str(movie),'output':str(release/'model-cases.json'),'fresh':args.fresh}
        config=temp/'config.json';config.write_text(json.dumps(cfg))
        # Both scripts and actual schema resolve installed packages from this
        # private test directory; no tool/app source is modified.
        script=temp/'verify.mjs';script.write_bytes((ROOT/'deploy/verify_personal_skills.mjs').read_bytes())
        (temp/'node_modules').symlink_to(app/'node_modules',target_is_directory=True)
        # The source definition lives outside app/node_modules too; a temporary
        # copy beside the runner uses exactly its real content and schema.
        definition=temp/'personal_skills.ts';definition.write_bytes((ROOT/'iva_integration/personal_skills.ts').read_bytes());cfg['definition']=str(definition);config.write_text(json.dumps(cfg))
        try:
            subprocess.run(['node',str(script),str(config)],env=env(),cwd=app,check=True,timeout=560)
            s=Store(state,skills)
            try:
                books=s.rows("SELECT * FROM skills WHERE name='personal-verification-books'")
                memories=s.execute({'action':'recall','name':'personal-movie-discovery'})['entries']
                watched=[r for r in memories if r['data'].get('watched') is True]
                cases=json.loads((release/'model-cases.json').read_text())['cases'];last=cases[-1]
                report={'model':'gpt-6-luna','created_skill':bool(books) or bool(previous.get('created_skill')),'creation_from_previous_run':args.fresh,'created_instructions':books[0]['instructions'] if books else previous.get('created_instructions'),
                        'watched':watched,'memory_count':len(memories),'fresh_recall':any(c['action']=='recall' for c in last['calls']),
                        'fixture_browser_used':any(c['action']=='web_fixture' for c in last['calls']),'answer':last['answer']}
                # A watched film may be mentioned to explain exclusion; inspect
                # stored recommended items, rather than banning its name in prose.
                inception=[r for r in watched if 'inception' in json.dumps(r,ensure_ascii=False).casefold() or 'начало' in json.dumps(r,ensure_ascii=False).casefold()]
                arrival_watched=any('прибытие' in json.dumps(r,ensure_ascii=False).casefold() or 'arrival' in json.dumps(r,ensure_ascii=False).casefold() for r in watched)
                report['fresh_watched_loaded']=any(any(e['data'].get('watched') is True for e in c.get('entries',[])) for c in last['calls'] if c['action']=='recall')
                report['recommendations']=[r for r in memories if r['source']=='isolated-personal-skills:fresh-recommend']
                report['passed']=bool(report['created_skill'] and inception and not arrival_watched and report['fresh_watched_loaded'] and report['fixture_browser_used'] and report['recommendations'] and all(r['data'].get('watched') is not True for r in report['recommendations']))
                (release/'model-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({k:v for k,v in report.items() if k not in ('watched','created_instructions')},ensure_ascii=False))
                if not report['passed']:raise RuntimeError('Native skill/memory verification failed')
            finally:s.close()
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':main()
