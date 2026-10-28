"""Owner-authored procedures and their durable, namespaced memory.

SQLite is canonical; SKILL.md files are a repairable projection for Eve's live
custom-skill loader. This module never writes executable code or app settings.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
NAME=re.compile(r'personal-[a-z0-9]+(?:-[a-z0-9]+)*\Z')
MUTATIONS={'save','archive','remember'}


def name_of(value):
    if not isinstance(value,str):raise ValueError('Нужно имя навыка')
    value=value if value.startswith('personal-') else 'personal-'+value
    if len(value)>63 or not NAME.fullmatch(value):
        raise ValueError('Имя: латинские строчные буквы, цифры и дефисы; максимум 63 символа с personal-')
    return value


def text(value,label,limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value:
        raise ValueError(f'{label}: нужен непустой текст до {limit} символов')
    return value.strip()


class Store:
    def __init__(self,state=None,skills=None):
        self.state=Path(state or ROOT/'data/personal-skills')
        self.skills=Path(skills or ROOT/'data/iva-pilot/app/data/custom/agent/skills')
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(self.state/'state.sqlite3',timeout=30)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS skills(name TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL,instructions TEXT NOT NULL,revision INTEGER NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,evidence TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS skill_versions(name TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(name,revision));
        CREATE TABLE IF NOT EXISTS memories(name TEXT NOT NULL REFERENCES skills(name),key TEXT NOT NULL,title TEXT NOT NULL,data TEXT NOT NULL,revision INTEGER NOT NULL,source TEXT NOT NULL,evidence TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(name,key));
        CREATE TABLE IF NOT EXISTS memory_versions(name TEXT NOT NULL,key TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(name,key,revision));
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,source TEXT NOT NULL,result TEXT NOT NULL);
        ''')
        os.chmod(self.state/'state.sqlite3',0o600)

    def close(self):self.db.close()

    def rows(self,sql,args=()):return [dict(r) for r in self.db.execute(sql,args)]

    def skill(self,name):
        rows=self.rows('SELECT * FROM skills WHERE name=?',(name,))
        if not rows:raise ValueError('Навык не найден; сначала list или save')
        return rows[0]

    def folder(self,name):
        # Refuse links before mkdir/replace; a skill cannot escape its own slot
        # or shadow an authored tool, a built-in skill, or a plugin package.
        for p in (self.skills,*self.skills.parents):
            if p.is_symlink():raise ValueError('Каталог навыков не должен быть симлинком')
        p=self.skills/name
        if p.is_symlink() or (p.exists() and not p.is_dir()):raise ValueError('Слот навыка занят')
        if (self.skills/(name+'.md')).exists():raise ValueError('Имя занято плоским навыком')
        if len(self.skills.parents)>3:
            authored=self.skills.parents[3]/'agent/skills'
            plugins=self.skills.parents[1]/'plugins'
            if (authored/name/'SKILL.md').exists() or (authored/(name+'.md')).exists() or any(plugins.glob('*/skills/'+name+'/SKILL.md')):
                raise ValueError('Имя занято встроенным или подключённым навыком; выберите другое personal-имя')
        target=p/'SKILL.md'
        if target.is_symlink():raise ValueError('Файл навыка не должен быть симлинком')
        if target.exists() and 'managed_by: iva-personal-skills\n' not in target.read_text(encoding='utf-8')[:1000]:
            raise ValueError('Этот навык создан другим механизмом; замена запрещена')
        return p

    def publish(self,name):
        # Serialize the DB read + atomic projection with writers, so an older
        # request cannot publish an obsolete version after a newer save/archive.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.skill(name);folder=self.folder(name);target=folder/'SKILL.md'
            if row['status']=='archived':
                if target.exists():target.unlink()
                # Empty directories would be treated as broken skill packages.
                if folder.exists() and not any(folder.iterdir()):folder.rmdir()
            else:
                folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                content=('---\nname: '+name+'\ndescription: '+json.dumps(row['description'],ensure_ascii=False)+
                         '\nmanaged_by: iva-personal-skills\nrevision: '+str(row['revision'])+'\n---\n\n# '+row['title']+
                         '\n\n'+row['instructions']+'\n')
                # Stage in the target directory so replace is atomic on all mounts.
                fd,tmp=tempfile.mkstemp(prefix='.SKILL-',dir=folder)
                try:
                    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
                        f.write(content);f.flush();os.fsync(f.fileno())
                    os.chmod(tmp,0o600);os.replace(tmp,target)
                finally:
                    if Path(tmp).exists():Path(tmp).unlink()
            if os.name!='nt' and folder.parent.exists():
                fd=os.open(folder if folder.exists() else folder.parent,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
            self.db.commit()
        except BaseException:self.db.rollback();raise

    def repair(self):
        for row in self.rows('SELECT name FROM skills'):self.publish(row['name'])

    def execute(self,data,source=None,body=None):
        if not isinstance(data,dict):raise ValueError('Нужен объект параметров')
        allowed={'action','name','title','description','instructions','expected_revision','evidence','key','data_json','query','offset','limit'}
        if set(data)-allowed:raise ValueError('Неизвестные поля инструмента')
        action=data.get('action')
        if action not in MUTATIONS|{'list','read','recall'}:raise ValueError('Неизвестное действие')
        if action in MUTATIONS:
            evidence=data.get('evidence')
            if not source or not body or not isinstance(evidence,str) or len(evidence.strip())<2 or evidence not in body:
                raise PermissionError('Нужна точная цитата текущего прямого сообщения владельца')
        name=name_of(data.get('name')) if action!='list' else None
        if action not in MUTATIONS:
            self.repair()
            if action=='list':
                return {'ok':True,'skills':self.rows("SELECT name,title,description,revision,status FROM skills ORDER BY name")}
            if action=='read':return {'ok':True,'skill':self.skill(name)}
            self.skill(name)
            offset=data.get('offset',0);limit=data.get('limit',50)
            if type(offset)!=int or offset<0 or type(limit)!=int or not 1<=limit<=100:raise ValueError('offset >=0; limit 1..100')
            rows=self.rows('SELECT * FROM memories WHERE name=? ORDER BY key',(name,));stored_total=len(rows)
            if data.get('key'):rows=[r for r in rows if r['key']==data['key']]
            query=data.get('query','')
            if not isinstance(query,str) or len(query)>300:raise ValueError('query: текст до 300 символов')
            matched=True
            if query:
                found=[r for r in rows if query.casefold() in (r['key']+' '+r['title']+' '+r['data']).casefold()]
                matched=bool(found)
                if found:rows=found
                # Broad natural-language searches can miss structured facts.
                # Never equate that with an empty memory: return the records,
                # clearly marked as an unfiltered fallback, with pagination.
            result=rows[offset:offset+limit]
            for r in result:r['data']=json.loads(r['data'])
            return {'ok':True,'entries':result,'total':len(rows),'stored_total':stored_total,'query_matched':matched,
                    'scope':'matching_entries' if matched else 'unfiltered_fallback',
                    'instruction':'query ищет подстроку названия/алиаса/данных. Для профиля и всей истории используйте recall без query. Отсутствие совпадения не означает отсутствие сохранённых фактов.',
                    'next_offset':offset+limit if offset+limit<len(rows) else None}
        encoded=json.dumps(data,sort_keys=True,ensure_ascii=False)
        request=hashlib.sha256((source+'\n'+encoded).encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            cached=self.rows('SELECT result FROM requests WHERE id=?',(request,))
            if cached:
                result={**json.loads(cached[0]['result']),'duplicate':True}
            else:
                expected=data.get('expected_revision')
                if type(expected)!=int or expected<0:raise ValueError('Нужен expected_revision: 0 для создания, текущая версия для изменения')
                now=datetime.now(timezone.utc).isoformat()
                if action=='save':
                    title=text(data.get('title'),'title',160)
                    description=text(data.get('description'),'description',120)
                    if any(c in title+description for c in '\r\n'):raise ValueError('title и description должны быть одной строкой')
                    instructions=text(data.get('instructions'),'instructions',20000)
                    old=self.rows('SELECT * FROM skills WHERE name=?',(name,))
                    if (old[0]['revision'] if old else 0)!=expected:raise ValueError('Версия навыка изменилась; перечитайте read')
                    self.folder(name)
                    row={'name':name,'title':title,'description':description,'instructions':instructions,'revision':expected+1,'status':'active','source':source,'evidence':evidence,'updated':now}
                    self.db.execute('INSERT INTO skills VALUES(:name,:title,:description,:instructions,:revision,:status,:source,:evidence,:updated) ON CONFLICT(name) DO UPDATE SET title=excluded.title,description=excluded.description,instructions=excluded.instructions,revision=excluded.revision,status=excluded.status,source=excluded.source,evidence=excluded.evidence,updated=excluded.updated',row)
                    self.db.execute('INSERT INTO skill_versions VALUES(?,?,?)',(name,row['revision'],json.dumps(row,ensure_ascii=False)))
                    result={'ok':True,'action':action,'name':name,'revision':row['revision'],'available_from':'next_turn','instructions':instructions,'duplicate':False}
                elif action=='archive':
                    row=self.skill(name)
                    if row['revision']!=expected:raise ValueError('Версия навыка изменилась; перечитайте read')
                    row.update(status='archived',revision=expected+1,source=source,evidence=evidence,updated=now)
                    self.db.execute('UPDATE skills SET status=:status,revision=:revision,source=:source,evidence=:evidence,updated=:updated WHERE name=:name',row)
                    self.db.execute('INSERT INTO skill_versions VALUES(?,?,?)',(name,row['revision'],json.dumps(row,ensure_ascii=False)))
                    result={'ok':True,'action':action,'name':name,'revision':row['revision'],'memory_preserved':True,'duplicate':False}
                else:
                    if self.skill(name)['status']!='active':raise ValueError('Навык архивирован')
                    key=text(data.get('key'),'key',160);title=text(data.get('title'),'title',160)
                    raw=text(data.get('data_json'),'data_json',16000)
                    value=json.loads(raw)
                    if not isinstance(value,dict) or not value:raise ValueError('data_json: непустой JSON object')
                    old=self.rows('SELECT * FROM memories WHERE name=? AND key=?',(name,key))
                    if (old[0]['revision'] if old else 0)!=expected:raise ValueError('Версия записи изменилась; перечитайте recall')
                    value={**(json.loads(old[0]['data']) if old else {}),**value}
                    if len(json.dumps(value,ensure_ascii=False))>16000:raise ValueError('Запись памяти слишком велика; разделите её')
                    row={'name':name,'key':key,'title':title,'data':json.dumps(value,ensure_ascii=False,sort_keys=True),'revision':expected+1,'source':source,'evidence':evidence,'updated':now}
                    self.db.execute('INSERT INTO memories VALUES(:name,:key,:title,:data,:revision,:source,:evidence,:updated) ON CONFLICT(name,key) DO UPDATE SET title=excluded.title,data=excluded.data,revision=excluded.revision,source=excluded.source,evidence=excluded.evidence,updated=excluded.updated',row)
                    self.db.execute('INSERT INTO memory_versions VALUES(?,?,?,?)',(name,key,row['revision'],json.dumps(row,ensure_ascii=False)))
                    result={'ok':True,'action':action,'name':name,'key':key,'revision':row['revision'],'data':value,'duplicate':False}
                self.db.execute('INSERT INTO requests VALUES(?,?,?)',(request,source,json.dumps(result,ensure_ascii=False)))
            self.db.commit()
        except BaseException:self.db.rollback();raise
        try:self.publish(name)
        except (OSError,ValueError):
            return {'ok':False,'saved':True,'published':False,'name':name,'error':'Запись сохранена, но навык не опубликован. Повторите исходный вызов или list для восстановления; не заявляйте готовность.'}
        return {**result,'published':True}


def execute(data,ledger=None,owner=None,source=None,*,state=None,skills=None):
    body=None
    if data.get('action') in MUTATIONS:
        from local_finance.received_income import raw_owner_input
        raw=raw_owner_input(ledger,owner,source or '',allow_quoted_text=True) if ledger else None
        if not raw:raise PermissionError('Изменять навыки и их память можно только из текущего прямого сообщения владельца')
        body=raw['body']
    store=Store(state,skills)
    try:return store.execute(data,source,body)
    finally:store.close()
