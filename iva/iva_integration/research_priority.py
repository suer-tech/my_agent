"""Internal research lease client; the browser coordinator owns resource arbitration."""
import json
import os
from pathlib import Path
import socket
import time

SOCKET='/run/iva-browser/control.sock'
STATUS=Path(__file__).resolve().parents[1]/'data/research-priority.json'
_held=None


def request(payload):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(SOCKET)
        client.sendall(json.dumps(payload).encode()+b'\n')
        data=b''
        while b'\n' not in data:
            part=client.recv(65536)
            if not part or len(data)+len(part)>262144:raise OSError('Invalid coordinator response')
            data+=part
        result=json.loads(data.split(b'\n',1)[0])
        if not isinstance(result,dict):raise OSError('Invalid coordinator response')
        return result


def record(result):
    value={key:result[key] for key in ('ok','granted','status','job_id','cycle_id','error') if key in result}
    value['at']=time.time()
    temporary=STATUS.with_suffix('.json.tmp')
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False)
        os.replace(temporary,STATUS)
    except OSError:pass


def acquire(job_id,cycle_id=None):
    global _held
    payload={'action':'research-acquire','job_id':job_id}
    if cycle_id:payload['cycle_id']=cycle_id
    try:result=request(payload)
    except (OSError,ValueError):result={'ok':False,'granted':False,'status':'coordinator_unavailable','job_id':job_id}
    if result.get('ok') and result.get('lease_token'):
        _held={'job_id':job_id,'cycle_id':cycle_id or result.get('cycle_id'),'lease_token':result['lease_token']}
    record(result)
    return result


def release():
    global _held
    if not _held:return True
    try:result=request({'action':'research-release',**_held})
    except (OSError,ValueError):result={'ok':False,'status':'release_pending','job_id':_held['job_id']}
    record(result)
    if result.get('ok'):
        _held=None
        return True
    return False


def reconcile(store):
    """Run only between work units, after Store.finish committed cursor/outbox.

    A cancelled job or a terminal/suspended checkpoint cannot leave the live
    scheduler PID holding the browser queue forever. A failed release is retried
    on the next worker loop; process death is handled by the coordinator.
    """
    if not _held:return
    rows=store.rows('SELECT state,cursor FROM scheduled_jobs WHERE id=?',(_held['job_id'],))
    if (not rows or rows[0]['state'] not in ('active','running') or
            json.loads(rows[0]['cursor']).get('research_lease_token')!=_held['lease_token']):
        release()
