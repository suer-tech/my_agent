"""Root-only AmneziaWG namespace; keeps the host/SSH default route unchanged."""
import configparser
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
import time

NAME='iva-codex';IFACE='awg-iva';HOST='iva-vpn-host';PEER='iva-vpn-ns'
CONFIG=Path('/etc/iva-vpn/client.conf')

def run(*args):return subprocess.run(args,check=True,capture_output=True)

def stop():
    for command in [('ip','-n',NAME,'link','del',IFACE),('ip','link','del',HOST),('ip','netns','del',NAME)]:
        subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def start():
    parser=configparser.ConfigParser(interpolation=None,strict=True);parser.optionxform=str;parser.read(CONFIG)
    interface=dict(parser['Interface'])
    allowed={'PrivateKey','ListenPort','FwMark','Jc','Jmin','Jmax','S1','S2','S3','S4','H1','H2','H3','H4',*[f'I{i}' for i in range(1,6)]}
    quick={'Address','DNS','MTU','Table'}
    if set(interface)-allowed-quick:raise ValueError('Unsupported interface options; hooks are never executed')
    peer=dict(parser['Peer'])
    if set(peer)-{'PublicKey','PresharedKey','Endpoint','AllowedIPs','PersistentKeepalive'}:raise ValueError('Unsupported peer options')
    addresses=[str(ipaddress.ip_interface(v.strip())) for v in interface['Address'].split(',')]
    dns=[str(ipaddress.ip_address(v.strip())) for v in interface['DNS'].split(',')]
    mtu=int(interface.get('MTU','1280'))
    if not 1200<=mtu<=1500:raise ValueError('Invalid MTU')
    netdns=Path('/etc/netns')/NAME;netdns.mkdir(parents=True,exist_ok=True)
    (netdns/'resolv.conf').write_text(''.join('nameserver '+v+'\n' for v in dns))
    (netdns/'resolv.conf').chmod(0o644)
    wire='[Interface]\n'+''.join(k+' = '+v+'\n' for k,v in interface.items() if k in allowed)+'\n[Peer]\n'+''.join(k+' = '+v+'\n' for k,v in peer.items())
    conf=Path('/run/iva-awg.conf');conf.write_text(wire);conf.chmod(0o600)
    stop()
    try:
        run('ip','netns','add',NAME)
        run('ip','link','add',HOST,'type','veth','peer','name',PEER)
        run('ip','link','set',PEER,'netns',NAME)
        run('ip','addr','add','10.203.77.1/30','dev',HOST);run('ip','link','set',HOST,'up')
        run('ip','-n',NAME,'addr','add','10.203.77.2/30','dev',PEER);run('ip','-n',NAME,'link','set',PEER,'up')
        run('ip','-n',NAME,'link','set','lo','up')
        # Create UDP transport in the HOST namespace before moving the TUN.
        # Its encrypted endpoint traffic retains the host's normal routing.
        run('/usr/local/bin/amneziawg-go',IFACE)
        for _ in range(30):
            if Path('/var/run/amneziawg/'+IFACE+'.sock').exists() or Path('/var/run/wireguard/'+IFACE+'.sock').exists():break
            time.sleep(.1)
        run('/usr/local/bin/awg','setconf',IFACE,str(conf))
        run('ip','link','set',IFACE,'netns',NAME)
        for address in addresses:run('ip','-n',NAME,'addr','add',address,'dev',IFACE)
        run('ip','-n',NAME,'link','set',IFACE,'mtu',str(mtu),'up')
        run('ip','-n',NAME,'route','add','default','dev',IFACE)
        # No host gateway in this namespace: tunnel failure cannot fall back
        # to a direct route. The veth is only the private Telegram proxy link.
    except BaseException:stop();raise
    finally:conf.unlink(missing_ok=True)

if __name__=='__main__':
    if os.geteuid()!=0:raise PermissionError('root required')
    if sys.argv[1]=='start':start()
    elif sys.argv[1]=='stop':stop()
    else:raise ValueError('Unknown action')
