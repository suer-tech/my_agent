"""Private target SSH transport via the pinned current VPS; never print credentials."""
from pathlib import Path
import base64
import hashlib
import os
import sys
import socket
import paramiko

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy.remote import connect_legacy as connect_source

HOST='5.63.152.247'
STATE=ROOT/'data/vps-deploy'
KNOWN=STATE/'target-server-known-hosts'
PASSWORD=STATE/'target-server-password.txt'
KEY=STATE/'target-server-id_ed25519'


def channel():
    # Port 443 is reachable from this computer and needs no old-server relay.
    try:return paramiko.SSHClient(),socket.create_connection((HOST,443),timeout=8)
    except OSError:pass
    source=connect_source()
    try:return source,source.get_transport().open_channel('direct-tcpip',(HOST,22),('127.0.0.1',0),timeout=15)
    except BaseException:source.close();raise


def pin():
    source,sock=channel();transport=paramiko.Transport(sock)
    try:
        transport.start_client(timeout=15);key=transport.get_remote_server_key()
        keys=paramiko.HostKeys(str(KNOWN)) if KNOWN.exists() else paramiko.HostKeys()
        if HOST in keys and not keys.check(HOST,key):raise RuntimeError('Target host key changed; no authentication attempted')
        keys.add(HOST,key.get_name(),key);keys.save(str(KNOWN))
        print('Pinned target host key: SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('='))
    finally:transport.close();source.close()


def connect(username='root',password_only=False):
    if not KNOWN.exists():raise RuntimeError('Pin target host key before authentication')
    password=None
    if password_only or not KEY.exists():
        password=PASSWORD.read_text(encoding='utf-8-sig').rstrip('\r\n') if PASSWORD.exists() else ''
        if not password:raise RuntimeError('Local target password file is empty; no authentication attempted')
    source,sock=channel();target=paramiko.SSHClient();target.load_host_keys(str(KNOWN));target.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        target.connect(HOST,username=username,sock=sock,password=password,key_filename=None if password is not None else str(KEY),look_for_keys=False,allow_agent=False,timeout=15,auth_timeout=15,banner_timeout=15)
        return source,target
    except BaseException:target.close();source.close();raise


def main():
    if len(sys.argv)>1 and sys.argv[1]=='pin':pin();return
    source,target=connect()
    try:
        data=sys.stdin.buffer.read().replace(b'\r\n',b'\n')
        i,o,e=target.exec_command('bash -se',timeout=60);i.write(data);i.channel.shutdown_write()
        sys.stdout.buffer.write(o.read());sys.stderr.buffer.write(e.read());raise SystemExit(o.channel.recv_exit_status())
    finally:target.close();source.close()


if __name__=='__main__':main()
