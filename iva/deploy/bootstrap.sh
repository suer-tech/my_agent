#!/usr/bin/env bash
# Run as root on the target AFTER the private cutover bundle is unpacked at /.
# Leaves Telegram stopped until all server-side checks pass.
set -euo pipefail
umask 077
test "$(id -u)" = 0
test -f /opt/iva/data/local-finance.sqlite3
test -f /home/iva/.codex/auth.json
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git python3 python3-venv build-essential cmake ffmpeg xz-utils
id iva >/dev/null 2>&1 || useradd --create-home --shell /bin/bash iva
python3 - <<'PY'
import hashlib,json,platform,tarfile,tempfile,urllib.request,subprocess
from pathlib import Path
if platform.machine()!='x86_64':raise RuntimeError('Verify Node architecture before continuing')
with tempfile.TemporaryDirectory() as d:
    index=json.load(urllib.request.urlopen('https://nodejs.org/dist/index.json'))
    version=next(x['version'] for x in index if x['version'].startswith('v24.'))
    name=f'node-{version}-linux-x64.tar.xz';base=f'https://nodejs.org/dist/{version}/'
    expected=next(s.split()[0] for s in urllib.request.urlopen(base+'SHASUMS256.txt').read().decode().splitlines() if s.split()[-1]==name)
    package=Path(d)/name;urllib.request.urlretrieve(base+name,package)
    assert hashlib.sha256(package.read_bytes()).hexdigest()==expected
    with tarfile.open(package) as t:t.extractall('/opt',filter='data')
    for binary in ('node','npm','npx'):
        path=Path('/usr/local/bin')/binary
        if path.exists() or path.is_symlink():
            if not path.is_symlink():raise RuntimeError('Existing non-symlink '+str(path))
            path.unlink()
        path.symlink_to(Path('/opt')/f'node-{version}-linux-x64/bin'/binary)
PY
npm install -g @openai/codex@0.163.0-alpha.10.2
ln -s "$(npm prefix -g)/bin/codex" /usr/local/bin/codex
chmod -R a+rX "$(npm prefix -g)"
python3 -m venv /opt/iva/.venv
/opt/iva/.venv/bin/pip install -r /opt/iva/deploy/requirements.txt
chown -R iva:iva /opt/iva /home/iva/.codex
chown iva:iva /home/iva
chmod 750 /home/iva
chmod 700 /home/iva/.codex /opt/iva/data
chmod 600 /home/iva/.codex/auth.json /opt/iva/.env /opt/iva/data/cartesia.env /opt/iva/data/iva-pilot/app/.env
runuser -u iva -- bash -c 'cd /opt/iva/data/iva-pilot/app && npm ci'
/opt/iva/.venv/bin/python -m playwright install-deps chromium
runuser -u iva -- /opt/iva/.venv/bin/python -m playwright install chromium
runuser -u iva -- bash -c 'git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git /opt/iva/data/voice/whisper && cmake -S /opt/iva/data/voice/whisper -B /opt/iva/data/voice/whisper/build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF && cmake --build /opt/iva/data/voice/whisper/build --target whisper-cli -j2'
runuser -u iva -- /opt/iva/.venv/bin/python /opt/iva/iva_integration/run_pilot.py build
install -m 644 /opt/iva/deploy/iva@.service /etc/systemd/system/iva@.service
systemctl daemon-reload
systemctl enable --now iva@gateway iva@engine
printf '%s\n' 'Infrastructure installed. Verify Codex, finance, memory, browser, audio and reminders BEFORE enabling iva@telegram and iva@memory.'
