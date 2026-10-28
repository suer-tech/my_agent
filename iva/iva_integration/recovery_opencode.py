"""Read-only adapter for root-authored, confirmed OpenCode restart evidence.

The watchdog retains bounded immutable events even after normal ticks replace
its current status. The caller must commit ``state`` with its durable outbox;
event IDs deliberately remain stable across polls for delivery deduplication.
"""
import json
import math
import os
from pathlib import Path
import re
import stat


_MAX_BYTES = 16384
_MAX_EVENTS = 32
_FRESH_SECONDS = 300
_EVENT_ID = re.compile(r"opencode:service_restart:[0-9a-f]{32}\Z")


def _stamp(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value) if 0 < value < 1e12 else None


def _trusted(info, directory=False):
    # The real producer runs on Linux. Windows is used only for isolated tests.
    if os.name != 'posix':
        return True
    import grp
    return (info.st_uid == 0 and info.st_gid == grp.getgrnam('iva').gr_gid
            and stat.S_IMODE(info.st_mode) == (0o750 if directory else 0o640)
            and (directory or info.st_nlink == 1))


def _read(path):
    if any(item.is_symlink() for item in (path, *path.parents)):
        return None
    if not _trusted(path.parent.stat(), directory=True):
        return None
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES or not _trusted(info):
            return None
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        return None
    value = json.loads(raw)
    return value if isinstance(value, dict) else None


def _event(value, baseline, checked):
    if not isinstance(value, dict):
        return None
    ident = value.get('id')
    proof = value.get('proof')
    verified = _stamp(value.get('verified_at'))
    if (not isinstance(ident, str) or not _EVENT_ID.fullmatch(ident)
            or value.get('source') != 'opencode' or value.get('code') != 'provider_unavailable'
            or value.get('action') != 'service_restart' or not isinstance(proof, dict)
            or verified is None or not baseline < verified <= checked):
        return None
    attempted = _stamp(proof.get('attempted_at'))
    if (attempted is None or attempted > verified
            or any(proof.get(key) is not True for key in
                   ('restart_succeeded', 'global_healthy', 'provider_available', 'provider_503_confirmed'))):
        return None
    return {'id': ident, 'source': 'opencode', 'code': 'provider_unavailable',
            'action': 'service_restart', 'verified_at': verified,
            'proof': {'attempted_at': attempted, 'restart_succeeded': True,
                      'global_healthy': True, 'provider_available': True, 'provider_503_confirmed': True}}


def collect(status_path: Path, previous: dict | None, now: float) -> dict:
    """Baseline at installation; report only actual restart + both health checks.

    Missing/unreadable telemetry, stale probes, a new unresolved failure, global
    health alone, and an old ``last_restart_result`` never claim a repair. No
    model, service action, database write or notification happens in this adapter.
    """
    stamp = _stamp(now)
    if stamp is None:
        raise ValueError('Invalid recovery collection time')
    prior = previous if isinstance(previous, dict) else {}
    baseline = _stamp(prior.get('baseline_at')) if prior.get('version') == 1 else None
    baseline = baseline if baseline is not None and baseline <= stamp else stamp
    output = {'state': {'version': 1, 'baseline_at': baseline}, 'events': []}
    if previous is None or baseline == stamp:
        return output
    try:
        value = _read(Path(status_path))
    except (OSError, ValueError, KeyError, UnicodeError):
        return output
    if not isinstance(value, dict) or value.get('version') != 1:
        return output
    checked = _stamp(value.get('checked_at'))
    values = value.get('events')
    if (checked is None or not 0 <= stamp - checked <= _FRESH_SECONDS
            or value.get('observation') != 'healthy' or value.get('global_http_status') != 200
            or value.get('provider_http_status') != 200 or not isinstance(values, list)
            or len(values) > _MAX_EVENTS):
        return output
    found = {}
    for item in values:
        event = _event(item, baseline, checked)
        if event is not None:
            found.setdefault(event['id'], event)
    output['events'] = sorted(found.values(), key=lambda item: (item['verified_at'], item['id']))
    return output
