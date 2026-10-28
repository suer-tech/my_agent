"""Download official public CA with normal TLS; scoped to EIS client, not OS trust."""
from pathlib import Path
import ssl
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
url='https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer'
with urllib.request.urlopen(url,timeout=20) as response:
    data=response.read(100001)
if len(data)>100000:raise ValueError('Unexpected certificate size')
if not data.startswith(b'-----BEGIN CERTIFICATE-----'):
    data=ssl.DER_cert_to_PEM_cert(data).encode()
context=ssl.create_default_context()
context.load_verify_locations(cadata=data.decode('ascii'))
target=ROOT/'data/eis-ca.pem'
target.write_bytes(data)
print('EIS-specific CA installed; system trust unchanged')
