"""One fixed-origin authenticated POST, via systemd's existing VPN namespace.

Installed root-owned; User=iva; socket stdin/stdout only. No CLI options, proxy
environment, redirects, caller URL/headers/token, SMTP, or source-page reads.
"""
import http.client
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import sys

HOST = "lead-flow-ten-virid.vercel.app"
PATHS = frozenset(("/api/agent/leads", "/api/agent/drafts", "/api/agent/drafts/revise", "/api/agent/outreach/claim",
    "/api/agent/replies", "/api/agent/heartbeat", "/api/agent/maintenance"))


def allowed_path(path):
    return isinstance(path, str) and (path in PATHS or re.fullmatch(r"/api/agent/outreach/[a-f0-9]{32}/result", path) is not None)
CONFIG = Path("/etc/lead-flow/crm-transport.json")
TOKEN = Path("/etc/lead-flow/crm-agent.token")
MAX_BYTES = 128 * 1024
CONNECT_SECONDS = 5
REQUEST_SECONDS = 10
READ_SECONDS = 10
TOTAL_SECONDS = 30


def private_read(path, maximum, secret=False):
    # File names are constants, not supplied in IPC/configuration. Refuse mutable
    # parents and symlinks, including the token leaf. Open without following links.
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("unsafe_config_parent")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        permitted = (0o600, 0o640) if secret else (0o600, 0o640, 0o644)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) not in permitted or info.st_size > maximum
                or secret and stat.S_IMODE(info.st_mode) == 0o640 and info.st_gid != os.getegid()):
            raise ValueError("unsafe_config_file")
        result = stream.read(maximum + 1)
        if len(result) > maximum:
            raise ValueError("oversized_config")
        return result


def load_token():
    config = json.loads(private_read(CONFIG, 256))
    if (not isinstance(config, dict) or set(config) != {"enabled"}
            or type(config["enabled"]) is not bool or not config["enabled"]):
        raise ValueError("transport_disabled")
    token = private_read(TOKEN, 1024, secret=True).decode("ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9._~+/-]{32,512}={0,2}", token):
        raise ValueError("invalid_token")
    return token


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def parse_request(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError("oversized_request")
    request = json.loads(raw, object_pairs_hook=no_duplicates,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid_number")))
    if (not isinstance(request, dict) or set(request) != {"path", "payload"}
            or not allowed_path(request["path"]) or not isinstance(request["payload"], dict)):
        raise ValueError("unsupported_request")
    return request


def failure(category, status=None):
    return {"ok": False, "http_status": status, "error": category}


def request_once(request, token):
    connection = None
    try:
        # http.client ignores HTTP(S)_PROXY, performs no redirect/retry, and uses
        # the same fixed hostname for DNS, Host, SNI and certificate validation.
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(HOST, port=443, timeout=CONNECT_SECONDS, context=context)
        connection.connect()
        connection.sock.settimeout(REQUEST_SECONDS)
        body = json.dumps(request["payload"], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        connection.request("POST", request["path"], body=body, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"})
        connection.sock.settimeout(READ_SECONDS)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            return failure("crm_http_error", response.status)
        if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            return failure("crm_response_invalid")
        length = response.getheader("Content-Length")
        if length and (not length.isdecimal() or int(length) > MAX_BYTES):
            return failure("crm_response_too_large")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return failure("crm_response_too_large")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return failure("crm_response_invalid")
        return {"ok": True, "http_status": response.status, "data": data}
    except Exception:
        # Exception strings can contain remote HTML or secrets. Never log them.
        return failure("crm_request_failed")
    finally:
        if connection is not None:
            connection.close()


def execute(raw):
    try:
        request = parse_request(raw)
        token = load_token()
        result = request_once(request, token)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            return json.dumps(failure("crm_response_too_large"), separators=(",", ":")).encode()
        return encoded
    except Exception:
        return json.dumps(failure("crm_transport_unavailable"), separators=(",", ":")).encode()


class Deadline(BaseException):
    pass


def deadline_expired(signum, frame):
    raise Deadline()


def main():
    if len(sys.argv) != 1:
        return 2
    signal.signal(signal.SIGALRM, deadline_expired)
    signal.alarm(TOTAL_SECONDS)
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        result = execute(raw)
    except Deadline:
        result = json.dumps(failure("crm_transport_timeout"), separators=(",", ":")).encode()
    except Exception:
        result = json.dumps(failure("crm_transport_unavailable"), separators=(",", ":")).encode()
    finally:
        signal.alarm(0)
    sys.stdout.buffer.write(result)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
