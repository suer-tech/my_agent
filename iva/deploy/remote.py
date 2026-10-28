"""Private-key SSH transport for this deployment. Commands from stdin, no secrets logged."""
from pathlib import Path
import sys
import json
import paramiko

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def connect_legacy():
    client=paramiko.SSHClient()
    client.load_host_keys(str(ROOT/'data/vps-known-hosts'))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect('185.182.110.82',username='root',key_filename=str(ROOT/'data/vps-deploy/id_ed25519'),timeout=20,allow_agent=False,look_for_keys=False)
    return client


def connect():
    deployment=ROOT/'data/remote-deployment.json'
    host=json.loads(deployment.read_text(encoding='utf-8')).get('host') if deployment.exists() else '185.182.110.82'
    if host=='185.182.110.82':return connect_legacy()
    if host!='5.63.152.247':raise RuntimeError('Unknown deployment host; configure pinned SSH transport')
    from deploy.migration_remote import connect as target_connect
    source,target=target_connect()
    original_close=target.close
    def close():
        try:original_close()
        finally:source.close()
    target.close=close
    return target

def main():
    client=connect()
    try:
        if len(sys.argv)==4 and sys.argv[1]=='put':
            with client.open_sftp() as s:s.put(sys.argv[2],sys.argv[3])
            print('Uploaded');return
        data=sys.stdin.buffer.read().replace(b'\r\n',b'\n')
        i,o,e=client.exec_command('bash -se',timeout=60)
        i.write(data);i.channel.shutdown_write()
        sys.stdout.buffer.write(o.read());sys.stderr.buffer.write(e.read())
        raise SystemExit(o.channel.recv_exit_status())
    finally:client.close()

if __name__=='__main__':main()
