"""Fixed public GET reader inside Iva's existing VPN namespace; no model tools."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys

ROOT = Path('/opt/iva')
SOCKET = Path('/run/iva-research-fetch/fetch.sock')
MAX_REQUEST = 8192
MAX_RESPONSE = 600000


def error(category):
    return {'ok': False, 'text': '', 'title': '', 'content_hash': None, 'status': 'unavailable',
            'error_category': category, 'error': 'Public VPN source reader did not return verified evidence'}


def _validated_payload(data):
    from iva_integration.demand_sources import canonical_url
    value = json.loads(data)
    if (not isinstance(value, dict) or set(value) not in (
            {'action', 'url'}, {'action', 'url', 'include_contact_fragments'}) or value['action'] != 'fetch'
            or type(value.get('include_contact_fragments', False)) is not bool):
        raise ValueError('unsupported_request')
    result = {'action': 'fetch', 'url': canonical_url(value['url'])}
    if value.get('include_contact_fragments'):
        result['include_contact_fragments'] = True
    return result


def validated_request(data):
    # Preserve the existing public helper contract for old callers/tests.
    return _validated_payload(data)['url']


def once():
    from iva_integration.demand_sources import _fetch_source_direct
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(data) > MAX_REQUEST:
            raise ValueError('oversized_request')
        request = _validated_payload(data)
        value = (_fetch_source_direct(request['url'], include_contact_fragments=True)
                 if request.get('include_contact_fragments') else _fetch_source_direct(request['url']))
    except Exception:
        value = error('vpn_reader_request_failed')
    print(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def execute(data):
    try:
        payload = json.dumps(_validated_payload(data)).encode()
        process = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--once'],
            input=payload, capture_output=True, timeout=20,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        if process.returncode or len(process.stdout) > MAX_RESPONSE:
            return error('vpn_reader_failed')
        value = json.loads(process.stdout)
        if not isinstance(value, dict) or type(value.get('ok')) is not bool:
            return error('vpn_reader_invalid_result')
        return value
    except subprocess.TimeoutExpired:
        return error('vpn_reader_timeout')
    except (OSError, ValueError, TypeError, KeyError):
        return error('vpn_reader_request_failed')


def handle(client):
    # The filesystem socket is private too. Other UIDs cannot ask for requests.
    _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid not in (0, os.geteuid()):
        return
    client.settimeout(3)
    data = bytearray()
    while b'\n' not in data:
        part = client.recv(4096)
        if not part or len(data) + len(part) > MAX_REQUEST:
            return
        data.extend(part)
    result = execute(bytes(data).split(b'\n', 1)[0])
    outgoing = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
    if len(outgoing) > MAX_RESPONSE:
        outgoing = json.dumps(error('vpn_reader_response_too_large')).encode() + b'\n'
    client.sendall(outgoing)


def main():
    if os.name != 'posix' or not ROOT.is_dir() or os.geteuid() == 0:
        raise RuntimeError('Run this reader as iva in the dedicated systemd unit')
    sys.path.insert(0, str(ROOT))
    if sys.argv[1:] == ['--once']:
        return once()
    if sys.argv[1:]:
        raise ValueError('unsupported_arguments')
    if SOCKET.exists() and not SOCKET.is_socket():
        raise RuntimeError('unsafe_socket_path')
    SOCKET.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET)); os.chmod(SOCKET, 0o660); server.listen(2)
        print('Public research VPN reader ready', flush=True)
        while True:
            client, _ = server.accept()
            with client:
                try:handle(client)
                except (OSError, ValueError):pass


if __name__ == '__main__':
    main()
