#!/usr/bin/python3 -I
"""Root-owned, fixed-policy disk inspector. JSON stdin; no shell/path arguments.

Install a COPY outside the iva-owned checkout. Never import application code here.
The application verifies the current Telegram owner before requesting cleanup.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

CACHES = {
    'root_pip': ('/root/.cache/pip', 'Кэш pip (root)', 'pip'),
    'root_npm': ('/root/.npm/_cacache', 'Кэш npm (root)', 'npm'),
    'iva_pip': ('/home/iva/.cache/pip', 'Кэш pip (Ива)', 'pip'),
    'iva_npm': ('/home/iva/.npm/_cacache', 'Кэш npm (Ива)', 'npm'),
    'iva_node_headers': ('/home/iva/.cache/node-gyp', 'Кэш заголовков Node', 'node'),
    'apt_archives': ('/var/cache/apt/archives', 'Скачанные пакеты apt', 'apt'),
}
BACKUPS = Path('/opt/iva/data/server-backups')
BACKUP_NAME = re.compile(r'iva-\d{8}-\d{6}\.tar\.gz\Z')
LIMIT = 200000


def usage():
    total, used, free = shutil.disk_usage('/')
    return {'total_bytes': total, 'used_bytes': used, 'free_bytes': free,
            'used_percent': round(used / total * 100, 1),
            'research_pause_below_bytes': 200 * 1024**2,
            'warning': 'critical' if free < 200 * 1024**2 else 'low' if free < 1024**3 else None}


def policy():
    result = {key: {'id': key, 'path': path, 'label': label, 'group': group,
                    'reason': 'Повторно скачиваемый кэш установки; программы и рабочие данные сохраняются.'}
              for key, (path, label, group) in CACHES.items()}
    # Keep at least the two newest successful archives, even if both are old.
    if BACKUPS.exists() and not BACKUPS.is_symlink():
        entries = sorted((p for p in BACKUPS.iterdir() if BACKUP_NAME.fullmatch(p.name)
                          and p.is_file() and not p.is_symlink()), key=lambda p: p.name, reverse=True)
        for p in entries[2:]:
            if p.stat().st_mtime < time.time() - 14 * 86400:
                key = 'backup_' + p.name[4:-7]
                result[key] = {'id': key, 'path': str(p), 'label': 'Просроченная копия ' + p.name,
                               'group': 'backups', 'reason': 'Старше 14 дней; две последние копии сохраняются.'}
    return result


def parent_fd(path):
    """Pin every ancestor; a symlink or concurrent rename cannot redirect removal."""
    parts = Path(path).parts
    if not Path(path).is_absolute() or '..' in parts: raise ValueError('Invalid policy path')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4])
        if mount == path or mount.startswith(path + '/'):
            raise ValueError('Точка монтирования: автоматическая очистка запрещена')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd); raise


def snapshot(fd, name, root_ctime=None):
    digest = hashlib.sha256(); inodes = set(); count = 0; allocated = 0; logical = 0
    device = os.stat(name, dir_fd=fd, follow_symlinks=False).st_dev
    started = time.monotonic()

    def walk(parent, child, relative):
        nonlocal count, allocated, logical
        info = os.stat(child, dir_fd=parent, follow_symlinks=False)
        count += 1
        if count > LIMIT or time.monotonic() - started > 40: raise ValueError('Слишком большой каталог для безопасной очистки')
        if info.st_dev != device or stat.S_ISLNK(info.st_mode): raise ValueError('Ссылка или вложенная файловая система: требуется ручной разбор')
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)): raise ValueError('Специальный файл: автоматическая очистка запрещена')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1: raise ValueError('Файл с жёсткими ссылками: автоматическая очистка запрещена')
        record = [relative, info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns,
                  root_ctime if relative == '' and root_ctime is not None else info.st_ctime_ns]
        digest.update(json.dumps(record, ensure_ascii=True).encode() + b'\n')
        inodes.add((info.st_dev, info.st_ino))
        allocated += info.st_blocks * 512; logical += info.st_size if stat.S_ISREG(info.st_mode) else 0
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if os.fstat(child_fd).st_ino != info.st_ino: raise ValueError('Каталог изменился во время проверки')
                for item in sorted(os.listdir(child_fd)): walk(child_fd, item, relative + '/' + item)
            finally: os.close(child_fd)
    original_ctime = os.stat(name, dir_fd=fd, follow_symlinks=False).st_ctime_ns
    walk(fd, name, '')
    return {'fingerprint': digest.hexdigest(), 'allocated_bytes': allocated,
            'logical_bytes': logical, 'entries': count, 'root_ctime': original_ctime}, inodes


def busy(inodes, path, group):
    """Fail closed on inaccessible process data. No process arguments are returned."""
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid(): continue
        try:
            command = (proc / 'cmdline').read_bytes().split(b'\0')
            names = [os.path.basename(c.decode(errors='replace')) for c in command[:3]]
            manager = any(n in ('npm', 'npx', 'npm-cli.js', 'npx-cli.js', 'node-gyp', 'pip', 'pip3', 'apt', 'apt-get', 'dpkg') for n in names)
            manager = manager or len(command) > 2 and command[1:3] == [b'-m', b'pip']
            if manager and group != 'backups': return 'Работает установщик пакетов (PID ' + proc.name + ')'
            refs = list((proc / 'fd').iterdir()) + [proc / 'cwd', proc / 'exe']
            for ref in refs:
                try:
                    info = ref.stat()
                    if (info.st_dev, info.st_ino) in inodes: return 'Файл используется процессом PID ' + proc.name
                except FileNotFoundError: pass
            for line in (proc / 'maps').read_text(errors='replace').splitlines():
                fields = line.split(None, 5)
                if len(fields) == 6 and (fields[5] == path or fields[5].startswith(path + '/')):
                    return 'Файл отображён в память процесса PID ' + proc.name
        except (FileNotFoundError, ProcessLookupError): continue
        except PermissionError: return 'Не удалось проверить все процессы; удаление отложено'
    return None


def candidate(item):
    fd, name = parent_fd(item['path'])
    try:
        snap, inodes = snapshot(fd, name)
        reason = busy(inodes, item['path'], item['group'])
        return {**item, **snap, 'cleanable': not reason, 'blocked_reason': reason}
    finally: os.close(fd)


def inspect():
    candidates = []; skipped = []
    for item in policy().values():
        try:
            row = candidate(item)
            if row['logical_bytes']: candidates.append(row)
        except FileNotFoundError: pass
        except (OSError, ValueError) as exc: skipped.append({'id': item['id'], 'reason': str(exc)[:200]})
    # Directory sizes only, never file contents. Bound runtime and output.
    sizes = []; errors = []
    for paths, depth in [(['/'], '1'), (['/opt/iva', '/home/iva', '/root', '/var'], '2'), (['/usr'], '1')]:
        try:
            run = subprocess.run(['/usr/bin/du', '-x', '-B1', '-d' + depth, '--', *paths],
                                 capture_output=True, timeout=35, text=True)
            if run.returncode: errors.append('Часть каталогов недоступна для подсчёта')
            for line in run.stdout.splitlines():
                size, path = line.split('\t', 1)
                sizes.append({'path': path, 'allocated_bytes': int(size)})
        except (subprocess.TimeoutExpired, ValueError): errors.append('Подсчёт размеров не завершён в срок')
    return {'ok': True, 'disk': usage(), 'directories': sorted(sizes, key=lambda r: r['allocated_bytes'], reverse=True)[:100],
            'candidates': sorted(candidates, key=lambda r: r['allocated_bytes'], reverse=True),
            'skipped': skipped, 'coverage_gaps': errors,
            'recoverable_bytes': sum(r['allocated_bytes'] for r in candidates if r['cleanable']),
            'protected': ['Базы, документы, память и переписка', 'Текущие исследования и исходные файлы',
                          'Установленные браузеры ms-playwright, Node и модель распознавания речи',
                          'Свежие резервные копии и копии отката кода', 'Системные файлы, swap и активные журналы'],
            'recommendations': ['Начните с доступных кэшей установки: при следующей установке они скачаются заново.',
                                'Большие рабочие каталоги не означают мусор. Архивы отката и документы требуют отдельного разбора.',
                                'Если после очистки свободного места мало, увеличьте диск; очистка кэшей даёт разовый запас.']}


def clean(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 32: raise ValueError('Укажите кандидатов из анализа')
    available = policy(); selected = []; seen = set()
    for item in items:
        if set(item) != {'id', 'fingerprint'} or item['id'] not in available or item['id'] in seen:
            raise ValueError('Неизвестный или повторный кандидат')
        seen.add(item['id']); selected.append((available[item['id']], item['fingerprint']))
    results = []; before = usage()
    for item, expected in selected:
        fd = None; quarantine = None; deletion_started = False; snap = None
        try:
            if item['id'] not in policy(): raise ValueError('Копия больше не соответствует сроку хранения')
            fd, name = parent_fd(item['path'])
            snap, inodes = snapshot(fd, name)
            if snap['fingerprint'] != expected: raise ValueError('Содержимое изменилось; нужен новый анализ')
            reason = busy(inodes, item['path'], item['group'])
            if reason: raise ValueError(reason)
            quarantine = '.iva-clean-' + os.urandom(12).hex()
            os.rename(name, quarantine, src_dir_fd=fd, dst_dir_fd=fd)
            # Rename changes root ctime. All other metadata must still match.
            renamed = os.stat(quarantine, dir_fd=fd, follow_symlinks=False)
            if (renamed.st_dev, renamed.st_ino) not in inodes: raise ValueError('Каталог заменён перед удалением')
            post, post_inodes = snapshot(fd, quarantine, root_ctime=snap['root_ctime'])
            if post['fingerprint'] != snap['fingerprint']:
                raise ValueError('Содержимое изменилось перед удалением')
            reason = busy(post_inodes, item['path'], item['group'])
            if reason: raise ValueError(reason)
            if stat.S_ISDIR(renamed.st_mode):
                if not shutil.rmtree.avoids_symlink_attacks: raise ValueError('Нет безопасного удаления каталогов')
                deletion_started = True
                shutil.rmtree(quarantine, dir_fd=fd)
            else:
                deletion_started = True
                os.unlink(quarantine, dir_fd=fd)
            quarantine = None
            results.append({'id': item['id'], 'path': item['path'], 'deleted': True,
                            'removed_allocated_bytes': snap['allocated_bytes']})
        except (OSError, ValueError) as exc:
            removed = None
            if deletion_started and fd is not None and snap:
                try:
                    remaining, _ = snapshot(fd, quarantine)
                    removed = max(0, snap['allocated_bytes'] - remaining['allocated_bytes'])
                except FileNotFoundError: removed = snap['allocated_bytes']
                except (OSError, ValueError): pass
            if quarantine and fd is not None:
                try:
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    try: os.rename(quarantine, name, src_dir_fd=fd, dst_dir_fd=fd); quarantine = None
                    except OSError: pass
            results.append({'id': item['id'], 'deleted': False, 'reason': str(exc)[:200],
                            'remaining_quarantine': quarantine, 'partial_or_unknown': deletion_started,
                            'removed_allocated_bytes': removed or 0, 'removed_size_known': removed is not None or not deletion_started})
        finally:
            if fd is not None: os.close(fd)
    after = usage()
    return {'ok': all(r['deleted'] for r in results), 'results': results,
            'removed_allocated_bytes': sum(r.get('removed_allocated_bytes', 0) for r in results),
            'disk_before': before, 'disk_after': after,
            'free_delta_bytes': after['free_bytes'] - before['free_bytes']}


def main():
    if len(sys.argv) != 1: raise ValueError('Arguments are not supported')
    data = sys.stdin.buffer.read(65537)
    if len(data) > 65536: raise ValueError('Request too large')
    request = json.loads(data)
    if not isinstance(request, dict): raise ValueError('Invalid request')
    action = request.get('action')
    if action == 'status' and set(request) == {'action'}: return {'ok': True, 'disk': usage()}
    if action == 'analyze' and set(request) == {'action'}: return inspect()
    if action == 'clean' and set(request) == {'action', 'items'}: return clean(request['items'])
    raise ValueError('Unknown operation')


if __name__ == '__main__':
    try:
        import fcntl
        with open('/run/iva-disk-storage.lock', 'a') as lock:
            os.chmod('/run/iva-disk-storage.lock', 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            print(json.dumps(main(), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:250]}, ensure_ascii=False)); sys.exit(1)
