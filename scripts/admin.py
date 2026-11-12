#!/usr/bin/env python3
"""Comandos reais de operação, atualização e recuperação."""
import argparse
import collections
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.system import APP, ETC, STATE, SERVICES, run, service, enable, active, detect
from scripts import storage
from scripts.files import restore, write, managed_write


def report(config, reveal_password=False):
    statuses = {name: active(config, name) for name in SERVICES}
    if statuses['portal'] and statuses['web']:
        import urllib.request
        try:
            with urllib.request.urlopen('http://10.42.0.1/healthz', timeout=3) as response:
                statuses['portal'] = json.load(response).get('portal') == 'ok'
        except (OSError, ValueError):
            statuses['portal'] = False
    try:
        mounted = storage.require(config)
        disk = shutil.disk_usage(config['data_dir'])
        storage_state = f'MONTADO · UUID {config.get("storage_uuid")} · {disk.free/1024**3:.2f} GiB livres'
    except (RuntimeError, OSError) as error:
        mounted = None
        storage_state = 'INDISPONÍVEL · ' + str(error)
    zims = list((Path(config['data_dir']) / 'zim').glob('*.zim')) if mounted else []
    display_path = Path('/run/saberlocal/display.json')
    display = {'state': 'unsupported' if config.get('display_backend', 'none') == 'none' else 'não iniciado'}
    if config.get('display_backend', 'none') != 'none' and display_path.exists():
        try:
            display = json.loads(display_path.read_text())
        except (OSError, ValueError):
            display = {'state': 'erro de leitura do estado'}
    enabled = []
    expected = [n for n in SERVICES if n not in ('kiwix', 'display')]
    if config['backend'] == 'openrc':
        enabled = [n for n in expected if Path('/etc/runlevels/default', 'saberlocal-' + n).is_symlink()]
    else:
        enabled = [n for n in expected if run('systemctl', 'is-enabled', '--quiet', 'saberlocal-' + n, check=False).returncode == 0]
    print('\n=====================================\n             SABERLOCAL 2.0\n=====================================')
    print(f'Sistema: {config.get("os")} {config.get("version")}\nArquitetura: {config.get("arch")}\nServiços: {config["backend"]}')
    print('Wi-Fi: ' + config['ssid'])
    if reveal_password:
        print('Senha: ' + config['password'])
    print('Portal: http://saber.local\nIP: http://10.42.0.1')
    for label, name in [('Hotspot','hostapd'),('DHCP/DNS','dns'),('HTTP','web'),('Portal','portal'),('mDNS','mdns')]:
        print(f'{label}: {"ATIVO" if statuses[name] else "INATIVO"}')
    print(f'Biblioteca: {len(zims)} ZIM(s) · Kiwix {"ATIVO" if statuses["kiwix"] else "INATIVO"}')
    if not zims:
        print('Acervo: materiais locais disponíveis se o cartão estiver montado; coleções ainda não instaladas.')
    print('Início automático: ' + ('HABILITADO' if len(enabled) == len(expected) else 'INCOMPLETO'))
    print('Cartão microSD: ' + storage_state)
    print('Visor frontal: ' + display['state'] + (' · ' + display['message'] if display.get('message') else ''))
    print('=====================================')
    return all(statuses[n] for n in ('network','hostapd','dns','portal','web')) and bool(mounted)


def stop_all(config):
    for name in reversed(SERVICES):
        service(config, name, 'stop', check=False)
        enable(config, name, False)
    # Também limpa uma rede cujo oneshot falhou antes de marcar o serviço como iniciado.
    if config.get('interface'):
        run('ip', 'addr', 'del', '10.42.0.1/24', 'dev', config['interface'], check=False)
    run('nft', 'delete', 'table', 'inet', 'saberlocal', check=False)


def rollback(config, app_only=False):
    if app_only:
        previous = STATE / 'previous-release'
        snapshot = STATE / 'previous-configuration.json'
        if not previous.exists() or not snapshot.exists():
            raise RuntimeError('Não há versão anterior do aplicativo registrada.')
        target = Path(previous.read_text().strip()).resolve(strict=True)
        if target.parent != Path('/opt/saberlocal/releases'):
            raise RuntimeError('Caminho de recuperação fora do diretório de versões.')
        entries = json.loads(snapshot.read_text())
        old_config = json.loads(entries[str(ETC / 'private.json')]['text'])
        storage.require(old_config)
        stop_all(config)
        for name, entry in entries.items():
            managed_write(name, entry['text'], entry['mode'])
        APP.with_name('current.rollback').unlink(missing_ok=True)
        APP.with_name('current.rollback').symlink_to(target, target_is_directory=True)
        os.replace(APP.with_name('current.rollback'), APP)
        fstab = Path('/etc/fstab')
        if storage.BEGIN in fstab.read_text():
            write(fstab, storage.edit_fstab(fstab.read_text(), old_config))
        from scripts.install import activate, health
        activate(old_config)
        health(old_config)
        print('Versão anterior restaurada. Acervo e materiais preservados.')
        return
    if not (STATE / 'v2-files.json').exists():
        raise RuntimeError('Instalação 2.0 com backups não encontrada.')
    # Recusa sobrescrever edições externas antes de interromper serviços.
    from scripts.files import digest
    entries = json.loads((STATE / 'v2-files.json').read_text())
    conflicts = [name for name, entry in entries.items()
                 if Path(name).exists() and digest(name) != entry['installed']]
    if conflicts:
        raise RuntimeError('Preservadas edições externas; resolva antes do rollback: ' + ', '.join(conflicts))
    stop_all(config)
    restore()
    if (STATE / 'fstab-managed.json').exists():
        path = Path('/etc/fstab')
        write(path, storage.edit_fstab(path.read_text(), None))
        (STATE / 'fstab-managed.json').unlink()
    if config['backend'] == 'systemd':
        run('systemctl', 'daemon-reload')
    if shutil.which('nmcli'):
        run('nmcli', 'general', 'reload', check=False)
        if (STATE / 'nm-managed.json').exists():
            previous = json.loads((STATE / 'nm-managed.json').read_text())
            if previous['managed'] in ('yes', 'no'):
                run('nmcli', 'device', 'set', previous['interface'], 'managed', previous['managed'], check=False)
    if config['backend'] == 'systemd' and shutil.which('networkctl'):
        run('networkctl', 'reload', check=False)
    print('Serviços e arquivos da instalação restaurados. Acervo, cartão, pacotes e versões do código preservados. '
          'A Ethernet não foi reconfigurada. Processos manuais antigos não são reiniciados.')


def update(args):
    if not re.fullmatch(r'[a-fA-F0-9]{40}', args.ref or ''):
        raise ValueError('Atualização controlada exige --ref com SHA completo de 40 caracteres.')
    source = 'https://github.com/joaopereiralabs/saberlocal.git'
    if args.source:
        source = str(Path(args.source).resolve(strict=True))
    staging = STATE / ('update-' + secrets.token_hex(6))
    run('git', 'init', staging)
    run('git', '-C', staging, 'remote', 'add', 'origin', source)
    run('git', '-C', staging, 'fetch', '--depth=1', 'origin', args.ref)
    actual = run('git', '-C', staging, 'rev-parse', 'FETCH_HEAD').stdout.strip()
    if actual.lower() != args.ref.lower():
        raise RuntimeError('Commit recebido não corresponde ao solicitado.')
    run('git', '-C', staging, 'checkout', '--detach', actual)
    if not (staging / 'scripts/install.py').is_file():
        raise RuntimeError('Commit sem instalador SABERLOCAL compatível.')
    # O novo instalador adquire o lock e só troca a versão após preparar dependências e acervo.
    subprocess.run([sys.executable, str(staging / 'scripts/install.py'), '--skip-packages', '--no-content'], check=True)


def main():
    parser = argparse.ArgumentParser(description='Administrar SABERLOCAL')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('status','logs','restart'):
        sub.add_parser(action)
    restore_parser = sub.add_parser('rollback')
    restore_parser.add_argument('--app', action='store_true')
    updater = sub.add_parser('update')
    updater.add_argument('--ref', required=True)
    updater.add_argument('--source', help='Repositório Git local para atualização sem internet')
    for action in ('download','import','seed'):
        child = sub.add_parser(action, add_help=False)
        child.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Execute com sudo saberlocal ...')
    config = json.loads((ETC / 'private.json').read_text())
    if args.action in ('download','import','seed'):
        os.execv(sys.executable, [sys.executable, str(ROOT / 'scripts/content.py'), args.action, *args.arguments])
    if args.action == 'update':
        update(args)
        return
    if args.action == 'status':
        if not report(config):
            sys.exit(1)
    elif args.action == 'logs':
        for path in sorted(Path('/var/log/saberlocal').glob('*.log')):
            print('\n--- ' + path.name + ' ---')
            with path.open(errors='replace') as stream:
                print(''.join(collections.deque(stream, maxlen=60)))
    else:
        import fcntl
        Path('/run/lock').mkdir(parents=True, exist_ok=True)
        with open('/run/lock/saberlocal.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == 'rollback':
                rollback(config, args.app)
            elif args.action == 'restart':
                storage.mount(config)
                from scripts.install import activate, health
                activate(config)
                health(config)
                report(config)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('Erro: ' + (getattr(error, 'stderr', None) or str(error)), file=sys.stderr)
        sys.exit(1)
