"""One file-backed OAuth owner for native Iva and isolated CLI workers."""
import contextlib
import json
import os
from pathlib import Path
import time
import base64
import urllib.request


@contextlib.contextmanager
def login_lock(home):
    if os.name == 'nt':
        yield
        return
    import fcntl
    with open(Path(home)/'iva-auth.lock', 'a') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def access_expiry(auth):
    payload=auth['tokens']['access_token'].split('.')[1]
    return json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4))).get('exp',0)


def public_token(auth):
    tokens=auth['tokens']
    return {'accessToken':tokens['access_token'],'accountId':tokens.get('account_id')}


def token(home, force=False):
    home=Path(home)
    path=home/'auth.json'
    # Writers replace auth.json atomically. Reusing an unexpired access token
    # need not wait for a CLI worker that holds the refresh lock for its whole
    # model call. Expiring tokens and forced refreshes still serialize below.
    if not force:
        try:
            cached=json.loads(path.read_text())
            if access_expiry(cached)>=time.time()+300:return public_token(cached)
        except (OSError,ValueError,KeyError,IndexError,TypeError):pass
    with login_lock(home):
        auth=json.loads(path.read_text())
        tokens=auth['tokens']
        expiry=access_expiry(auth)
        if force or expiry < time.time()+300:
            proxy=os.environ.get('IVA_AUTH_PROXY','http://10.203.77.2:8888')
            client=urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy}))
            body=json.dumps({'client_id':'app_EMoamEEZ73f0CkXaXp7hrann',
                             'grant_type':'refresh_token','refresh_token':tokens['refresh_token']}).encode()
            req=urllib.request.Request('https://auth.openai.com/oauth/token',data=body,
                                       headers={'Content-Type':'application/json'})
            with client.open(req,timeout=40) as response:
                updated=json.load(response)
            if not isinstance(updated.get('access_token'),str) or not updated['access_token']:
                raise RuntimeError('OAuth refresh did not return an access token')
            for key in ('access_token','refresh_token','id_token'):
                if updated.get(key):tokens[key]=updated[key]
            from datetime import datetime,timezone
            auth['last_refresh']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
            temp=path.with_name('auth.iva.tmp')
            with os.fdopen(os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as out:
                json.dump(auth,out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp,path)
        return public_token(auth)


if __name__=='__main__':
    import sys
    try:
        print(json.dumps(token(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')),
                               '--force' in sys.argv)))
    except Exception:
        print('Shared subscription authorization unavailable',file=sys.stderr)
        raise SystemExit(1)
