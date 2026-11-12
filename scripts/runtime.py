#!/usr/bin/env python3
"""Operações pequenas usadas pelos serviços OpenRC e systemd."""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.system import run
from scripts.storage import mount, require


def directories():
    import pwd
    account = pwd.getpwnam('saberlocal')
    folder = Path('/run/saberlocal')
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o755)
    os.chown(folder, account.pw_uid, account.pw_gid)
    log = Path('/var/log/saberlocal')
    log.mkdir(parents=True, exist_ok=True)
    log.chmod(0o750)
    os.chown(log, 0, account.pw_gid)
    from scripts.system import SERVICES
    for name in SERVICES:
        path = log / (name + '.log')
        path.touch(exist_ok=True)
        path.chmod(0o640)
        os.chown(path, account.pw_uid if name in ('portal', 'kiwix') else 0, account.pw_gid)


def network(config):
    require(config)
    interface = config['interface']
    for attempt in range(20):
        if Path('/sys/class/net', interface).exists():
            break
        time.sleep(1)
    else:
        raise RuntimeError('Interface Wi-Fi não apareceu após 20 segundos.')
    # Regras são instaladas antes do IP: não há janela de administração pela rede dos alunos.
    rules = Path('/etc/saberlocal/firewall.nft').read_text()
    if run('nft', 'list', 'table', 'inet', 'saberlocal', check=False).returncode == 0:
        rules = 'delete table inet saberlocal\n' + rules
    run('nft', '-f', '-', input=rules)
    run('ip', 'link', 'set', 'dev', interface, 'up')
    run('ip', '-4', 'addr', 'flush', 'dev', interface, 'scope', 'global')
    run('ip', 'addr', 'replace', '10.42.0.1/24', 'dev', interface)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['guard', 'storage', 'storage-stop', 'network', 'network-stop', 'kiwix'])
    args = parser.parse_args()
    config = json.loads(Path('/etc/saberlocal/config.json').read_text())
    if args.action == 'storage':
        directories()
        mount(config)
    elif args.action == 'storage-stop':
        # O sistema operacional desmonta durante o shutdown; não retirar mídia em uso.
        return
    elif args.action == 'guard':
        require(config)
    elif args.action == 'network':
        directories()
        network(config)
    elif args.action == 'network-stop':
        run('ip', 'addr', 'del', '10.42.0.1/24', 'dev', config['interface'], check=False)
        run('nft', 'delete', 'table', 'inet', 'saberlocal', check=False)
    elif args.action == 'kiwix':
        require(config)
        books = sorted((Path(config['data_dir']) / 'zim').glob('*.zim'))
        executable = shutil.which('kiwix-serve')
        if not executable or not books:
            raise RuntimeError('Kiwix ou coleções ausentes; serviço não deve ser habilitado neste estado.')
        os.execv(executable, [executable, '--address=127.0.0.1', '--port=8081', '--threads=2',
                             '--urlRootLocation=/biblioteca', *map(str, books)])


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
