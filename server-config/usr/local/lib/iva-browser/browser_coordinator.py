"""Root service with a fixed service allowlist and cooperative fair turns."""
import grp
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import socket
import struct
import subprocess
import time
from browser_turn import atomic, read, STATE, SOCKET

UNITS={'max-manual':'max130-manual.service','max-worker':'max130-worker.service',
       'max-ads':'max130-ads.service','max-broadcast':'max130-broadcast.service',
       'max-distribute':None,'threads':'threads-browser.service'}
LAUNCH={'max-worker':'worker_launch.json','max-ads':'ads_launch.json','max-broadcast':'broadcast_launch.json'}


def threads_roots():
    root=Path('/opt/iva/data/threads-browser')
    return {'extension':root,'development':root/'development'}


def background_work(task):
    if task=='threads':
        return any((lambda launch:launch.get('enabled') and launch.get('pending'))(read(root/'hourly-launch.json',{})) for root in threads_roots().values())
    return task in LAUNCH and bool(read(Path('/opt/max')/LAUNCH[task],{}).get('enabled'))


def run(*args):
    return subprocess.run(['systemctl',*args],capture_output=True,text=True,timeout=8)


def live_units():
    result=[]
    for task, unit in UNITS.items():
        if not unit: continue
        state=run('show',unit,'--property=ActiveState','--value').stdout.strip()
        cg=run('show',unit,'--property=ControlGroup','--value').stdout.strip()
        busy=False
        if cg:
            root=Path('/sys/fs/cgroup') / cg.lstrip('/')
            busy=any(p.read_text().strip() for p in root.rglob('cgroup.procs')) if root.exists() else False
        if state in ('active','activating','deactivating') or busy: result.append(task)
    return result


def process_identity(pid):
    """Bind a lease to a real process lifetime, not a reusable numeric PID."""
    if type(pid) is not int or pid <= 0:return None
    try:
        stat=(Path('/proc')/str(pid)/'stat').read_text()
        fields=stat.rsplit(')',1)[1].split()
        if fields[0] in ('Z','X'):return None
        return {'pid':pid,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'start_ticks':fields[19]}
    except (OSError,ValueError,IndexError):return None


class Coordinator:
    def __init__(self, directory=STATE, live=live_units, command=run, clock=time.time, work=background_work,
                 process=process_identity, lock_available=None):
        self.root=Path(directory); self.live=live; self.command=command; self.clock=clock
        self.work=work
        self.process=process;self.lock_available=lock_available or self.owner_lock_available
        self._research_lock=None
        self.state=read(self.root/'state.json',{'enabled':True,'quantum_seconds':900,'queue':[],
                                              'current':None,'since':0,'blocked':None})
        self.starting=None
        self.yielding=bool(read(self.root/'grant.json',{}).get('yield'))

    def save(self): atomic(self.root/'state.json',self.state)

    def grant(self, task, yielding=False):
        atomic(self.root/'grant.json',{'task':task,'yield':yielding,'at':self.clock()},mode=0o644)

    def owner_lock_available(self):
        if self._research_lock is not None:return True
        lock=None
        try:
            lock=(self.root/'owner.lock').open('r+')
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # Hold through the entire lease, closing the check/grant race with
            # a browser that read its old grant immediately before this tick.
            self._research_lock=lock
            return True
        except (OSError,BlockingIOError):
            if lock is not None:lock.close()
            return False

    def research_status(self):
        lease=self.state.get('research')
        if not lease:
            last=self.state.get('research_last_release',{})
            return {'status':'idle','active':False,'granted':False,
                    'last_release':{k:last[k] for k in ('at','reason','job_id','cycle_id') if k in last}}
        return {k:lease.get(k) for k in ('job_id','cycle_id','requested_at','granted_at','status','dead_since')} | {
            'active':lease.get('status')=='active','granted':lease.get('status')=='active',
            'owner_alive':self.process(lease['owner']['pid'])==lease['owner']}

    def research_pauses(self, active_since=None, closed=None):
        path=self.root/'research-pauses.json'
        value=read(path,{'intervals':[],'active_since':None})
        intervals=value.get('intervals',[])
        if closed is not None and closed[0] is not None:
            interval=[closed[0],closed[1]]
            if interval not in intervals:intervals.append(interval)
        atomic(path,{'intervals':intervals,'active_since':active_since,'updated_at':self.clock()},mode=0o644)

    def release_research(self, reason):
        lease=self.state.get('research')
        if not lease:return
        self.research_pauses(closed=(lease.get('granted_at'),self.clock()))
        self.state['research_last_release']={'at':self.clock(),'reason':reason,'job_id':lease['job_id'],
            'cycle_id':lease.get('cycle_id'),'token':lease['token'],'owner':lease['owner']}
        self.state['research']=None
        if not self.state.get('current'):self.grant(None)
        self.save()
        if self._research_lock is not None:
            self._research_lock.close();self._research_lock=None

    def expire_research_owner(self):
        lease=self.state.get('research')
        if not lease:return
        if self.process(lease['owner']['pid'])==lease['owner']:
            if lease.get('dead_since') is not None:
                lease['dead_since']=None;self.save()
            return
        if lease.get('dead_since') is None:
            lease['dead_since']=self.clock();self.save()
        elif self.clock()-lease['dead_since']>=120:
            self.release_research('owner_process_disappeared')

    def handle_research(self,data,peer_pid):
        action=data['action']
        self.expire_research_owner()
        if action=='research-status':return {'ok':True,**self.research_status()}
        owner=self.process(peer_pid)
        if owner is None:raise PermissionError('Research requires a live authenticated peer process')
        job=data.get('job_id');cycle=data.get('cycle_id')
        if not isinstance(job,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',job):raise ValueError('Invalid research job_id')
        if cycle is not None and (not isinstance(cycle,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',cycle)):raise ValueError('Invalid research cycle_id')
        lease=self.state.get('research')
        if action=='research-release':
            if not lease:
                last=self.state.get('research_last_release',{})
                if last.get('token')==data.get('lease_token') and last.get('owner')==owner and last.get('job_id')==job:
                    return {'ok':True,'released':True,'duplicate':True,**self.research_status()}
                raise PermissionError('Research lease not owned by this process')
            if (lease['owner']!=owner or lease['job_id']!=job or lease['token']!=data.get('lease_token')
                    or (cycle is not None and lease.get('cycle_id') not in (None,cycle))):
                raise PermissionError('Research lease not owned by this process')
            self.release_research('completed_or_explicit_pause')
            self.tick()
            return {'ok':True,'released':True,**self.research_status()}
        if action!='research-acquire':raise ValueError('Unsupported research action')
        if lease:
            same_job=lease['job_id']==job and (cycle is None or lease.get('cycle_id') in (None,cycle))
            alive=self.process(lease['owner']['pid'])==lease['owner']
            if same_job and (lease['owner']==owner or not alive):
                if lease['owner']!=owner:
                    lease['owner']=owner;lease['token']=secrets.token_hex(24)
                lease['dead_since']=None
                if cycle is not None:lease['cycle_id']=cycle
            else:
                return {'ok':True,'granted':False,'status':'busy','job_id':lease['job_id'],'cycle_id':lease.get('cycle_id')}
        else:
            lease={'job_id':job,'cycle_id':cycle,'owner':owner,'token':secrets.token_hex(24),
                   'requested_at':self.clock(),'granted_at':None,'dead_since':None,'status':'waiting_background'}
            self.state['research']=lease
        self.save();self.tick()
        return {'ok':True,**self.research_status(),'lease_token':lease['token']}

    def research_tick(self,live,current):
        lease=self.state.get('research')
        if not lease:return False
        previous=lease.get('status')
        if live:
            if len(live)!=1 or live[0]!=current:
                lease['status']='waiting_unmanaged'
            elif (current!='max-manual' and self.work(current) and
                  not (current=='threads' and read(threads_roots()['extension']/'status.json',{}).get('state') in ('needs_login','navigation_failed'))):
                lease['status']='waiting_background'
                # Allow one bounded browser bootstrap before a new research
                # request can yield MAX at its still-empty first checkpoint.
                # Never revoke an existing yield or interrupt an active lease.
                age=self.clock()-self.state.get('since',0)
                startup_grace=(current=='max-broadcast' and 0<=age<60 and
                    self.state.get('enabled',True) and current in self.state.get('queue',[]) and
                    self.state.get('requested_next') in (None,current))
                if not self.yielding and not startup_grace:
                    self.grant(current,True);self.yielding=True
            else:
                lease['status']='waiting_interactive'
                # Research itself never evicts a manual window. A subsequent
                # explicit owner pause/switch/remove must still be honoured.
                requested=self.state.get('requested_next')
                owner_yield=(not self.state['enabled'] or current not in self.state['queue'] or
                             (requested in self.state['queue'] and requested!=current))
                if owner_yield and not self.yielding:
                    self.grant(current,True);self.yielding=True
        elif not self.lock_available():
            lease['status']='waiting_owner_lock'
        else:
            lease['status']='active'
            self.grant(None)
            if lease.get('granted_at') is None:
                lease['granted_at']=self.clock()
                self.research_pauses(active_since=lease['granted_at'])
        if previous!=lease['status'] or lease.get('granted_at')==self.clock():self.save()
        return True

    def handle(self,data,peer_pid=None):
        action=data.get('action')
        if action in ('research-acquire','research-release','research-status'):
            return self.handle_research(data,peer_pid)
        if action=='status': return self.status()
        if action=='threads-notify':
            result=self.command('start','--no-block','threads-monitor.service')
            return {'ok':result.returncode==0}
        if action in ('threads-configure','threads-run'):
            campaign=data.get('campaign','all')
            if campaign not in ('all','extension','development'):raise ValueError('Unsupported campaign')
            paths=[root/'hourly-launch.json' for name,root in threads_roots().items() if campaign in ('all',name)]
            launches=[(path,read(path,{})) for path in paths]
            if action=='threads-configure' and not isinstance(data.get('enabled'),bool):raise ValueError('enabled must be boolean')
            if action=='threads-run' and not any(launch.get('enabled') for _,launch in launches):raise ValueError('Campaign paused; enable first')
            for path,launch in launches:
                if not path.parent.is_dir():raise ValueError('Campaign is not installed')
                if action=='threads-configure':
                    launch['enabled']=data['enabled']
                    if not launch['enabled']:launch['pending']=False
                elif launch.get('enabled'):launch['pending']=True
                atomic(path,launch);os.chown(path,pwd.getpwnam('iva').pw_uid,grp.getgrnam('iva').gr_gid)
            if background_work('threads'):
                if 'threads' not in self.state['queue']:self.state['queue'].append('threads')
                self.state['blocked']=None
            else:self.state['queue']=[x for x in self.state['queue'] if x!='threads']
            self.save();return {'ok':True,'accepted':True,**self.status()}
        if action in ('enqueue','switch','remove'):
            task=data.get('task')
            if task not in UNITS or not UNITS[task]: raise ValueError('Unsupported browser task')
            if task in LAUNCH and not read(Path('/opt/max')/LAUNCH[task],{}).get('enabled'):
                raise ValueError('Start/authorize the MAX job in its panel first; coordinator only resumes it')
            queue=self.state['queue']
            if action=='remove': queue[:]=[t for t in queue if t!=task]
            elif task not in queue: queue.append(task)
            if action=='switch':
                queue[:]=[task]+[t for t in queue if t!=task]
                self.state['requested_next']=task if task!=self.state['current'] else None
            self.state['blocked']=None
        elif action=='configure':
            q=data.get('quantum_minutes',15)
            if isinstance(q,bool) or not isinstance(q,int) or not 2<=q<=120: raise ValueError('quantum_minutes: 2..120')
            self.state['quantum_seconds']=q*60
            self.state['enabled']=bool(data.get('enabled',True))
            self.state['blocked']=None
        elif action=='pause': self.state['enabled']=False
        elif action=='resume': self.state['enabled']=True; self.state['blocked']=None
        else: raise ValueError('Unsupported action')
        self.save()
        return {'ok':True,'accepted':True,**self.status()}

    def status(self):
        checkpoints={}
        for task in self.state['queue']:
            value=read(self.root/'checkpoints'/(task+'.json'),{})
            checkpoints[task]={k:value[k] for k in ('updated_at','next_url','next_index','source_file','last_completed_url','last_processed_url','pages','stage') if k in value}
        public_state={k:v for k,v in self.state.items() if k not in ('research','research_last_release')}
        return {'ok':True,**public_state,'research':self.research_status(),'live':self.live(),'checkpoints':checkpoints,
                'timed_rotation':'background_jobs_only','interactive_tasks':['max-manual','threads'],
                'threads_publisher_installed':True,
                'iva_runtime_health':read(Path('/opt/iva/data/runtime-diagnostics/health-status.json'),{}),
                'threads_schedule':read(Path('/opt/iva/data/threads-browser/hourly-launch.json'),{}),
                'threads_promotion':read(Path('/opt/iva/data/threads-browser/hourly-status.json'),{}),
                'threads_statistics':read(Path('/opt/iva/data/threads-browser/statistics.json'),{}),
                'threads_messages':read(Path('/opt/iva/data/threads-browser/inbox-status.json'),{}),
                'threads_repairs':read(Path('/opt/iva/data/threads-browser/repair/status.json'),{}),
                'threads_alerts':read(Path('/opt/iva/data/threads-browser/alerts-status.json'),{}),
                'threads_development':{k:read(threads_roots()['development']/v,{}) for k,v in [('schedule','hourly-launch.json'),('progress','hourly-status.json'),('alerts','alerts-status.json')]}}

    def tick(self):
        self.expire_research_owner()
        live=self.live(); now=self.clock(); current=self.state['current']
        if len(live)>1:
            self.state['blocked']='Multiple browser owners detected; refusing new starts'; self.save();return
        if self.state['blocked']=='Multiple browser owners detected; refusing new starts':
            self.state['blocked']=None
        if current and not live:
            if self.starting and now-self.starting<45: return
            # A cooperative yield retains authorization; normal completion removes demand.
            yielded=read(self.root/'checkpoints'/(current+'.json'),{}).get('yielded',False)
            if current in LAUNCH and not read(Path('/opt/max')/LAUNCH[current],{}).get('enabled'):
                self.state['queue']=[t for t in self.state['queue'] if t!=current]
            elif not self.yielding and not yielded:
                self.state['queue']=[t for t in self.state['queue'] if t!=current]
                self.state['blocked']='Browser exited unexpectedly; requeue after checking its status'
            self.state['current']=None; self.starting=None; self.yielding=False; current=None
            self.grant(None); self.save()
        if self.research_tick(live,current):return
        if live:
            if live[0]!=current:
                self.state['blocked']='Unmanaged browser still running; refusing parallel start';self.save();return
            competing_work=self.work(current) and any(
                t!=current and self.work(t) and t.startswith('max-')!=current.startswith('max-')
                for t in self.state['queue'])
            requested=self.state.get('requested_next')
            should_yield=(not self.state['enabled'] or current not in self.state['queue'] or
                          (requested in self.state['queue'] and requested!=current) or
                          (competing_work and now-self.state['since']>=self.state['quantum_seconds']))
            if should_yield and not self.yielding:
                self.grant(current,True);self.yielding=True
                if current in self.state['queue']:
                    self.state['queue'].remove(current);self.state['queue'].append(current)
                self.save()
            return
        if not self.state['enabled'] or not self.state['queue'] or self.state['blocked']: return
        available=next(int(x.split()[1]) for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))
        if available<640*1024: return
        task=self.state['queue'][0]
        self.state.update(current=task,since=now);self.starting=now
        if self.state.get('requested_next')==task:self.state['requested_next']=None
        # Clear old acknowledgement before granting the next turn.
        cp=self.root/'checkpoints'/(task+'.json');old=read(cp,{})
        atomic(cp,{**old,'yielded':False});os.chown(cp,0,grp.getgrnam('iva').gr_gid);os.chmod(cp,0o660)
        self.grant(task);self.save()
        if task=='threads':
            self.command('start','--no-block','threads-display','threads-wm','threads-vnc','threads-webview')
        result=self.command('start','--no-block',UNITS[task])
        if result.returncode:
            self.state['blocked']='Service start rejected';self.save()


def main():
    coordinator=Coordinator()
    Path(SOCKET).unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(SOCKET);os.chown(SOCKET,0,grp.getgrnam('iva').gr_gid);os.chmod(SOCKET,0o660)
        server.listen(16);server.settimeout(1)
        while True:
            try:
                client,_=server.accept()
                with client:
                    client.settimeout(2)
                    peer_pid,uid,_=struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    if uid not in (0,pwd.getpwnam('iva').pw_uid): raise PermissionError('Invalid peer')
                    data=b''
                    while b'\n' not in data and len(data)<8192:
                        part=client.recv(8192)
                        if not part: break
                        data+=part
                    try: response=coordinator.handle(json.loads(data),peer_pid=peer_pid)
                    except (ValueError,KeyError,TypeError,PermissionError) as exc: response={'ok':False,'error':str(exc)}
                    client.sendall(json.dumps(response,ensure_ascii=False).encode()+b'\n')
            except (socket.timeout,BrokenPipeError,ConnectionResetError): pass
            coordinator.tick()


if __name__=='__main__': main()
