"""Escrita atômica, backups e restauração que respeita alterações posteriores."""
import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path
from scripts.system import STATE


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.new')
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), mode)
    os.chmod(tmp, mode)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(text.rstrip() + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def managed_write(path, text, mode=0o644):
    path = Path(path)
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    index = STATE / 'v2-files.json'
    files = json.loads(index.read_text()) if index.exists() else {}
    entry = files.get(str(path))
    if entry is None:
        backup = None
        if path.exists():
            backup = str(STATE / (secrets.token_hex(12) + '.backup'))
            shutil.copy2(path, backup)
        entry = {'backup': backup}
    elif path.exists() and digest(path) != entry['installed']:
        raise RuntimeError(f'{path} foi alterado fora do instalador. Preserve/revise a alteração antes de continuar.')
    write(path, text, mode)
    entry['installed'] = digest(path)
    files[str(path)] = entry
    write(index, json.dumps(files, indent=2), 0o600)


def restore():
    index = STATE / 'v2-files.json'
    files = json.loads(index.read_text())
    conflicts = [name for name, entry in files.items()
                 if Path(name).exists() and digest(name) != entry['installed']]
    if conflicts:
        raise RuntimeError('Restauração cancelada para preservar edições externas: ' + ', '.join(conflicts))
    for name, entry in files.items():
        if entry['backup']:
            shutil.copy2(entry['backup'], name)
        else:
            Path(name).unlink(missing_ok=True)
    # Próxima instalação terá uma nova linha de base, preservando estes backups.
    index.rename(STATE / ('restored-' + secrets.token_hex(6) + '.json'))


def check_conflicts():
    index = STATE / 'v2-files.json'
    if not index.exists():
        return
    files = json.loads(index.read_text())
    conflicts = [name for name, entry in files.items()
                 if Path(name).exists() and digest(name) != entry['installed']]
    if conflicts:
        raise RuntimeError('Arquivos gerados foram alterados externamente: ' + ', '.join(conflicts))
