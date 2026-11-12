#!/usr/bin/env python3
"""Instalador SABERLOCAL 2.0: Alpine/OpenRC e Debian/Ubuntu/systemd."""
import argparse
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.system import APP, ETC, STATE, SERVICES, run, detect, service, enable, package_commands
from scripts.configuration import validate, render
from scripts.files import write, managed_write, check_conflicts
from scripts.content import seed, install_packs
from scripts import storage


def discover_interface(preferred=None):
    import re
    interfaces = re.findall(r'^\s*Interface\s+(\S+)', run('iw', 'dev').stdout, re.M)
    usable = []
    for interface in interfaces:
        try:
            check_ap(interface)
            usable.append(interface)
        except RuntimeError:
            pass
    if preferred:
        if preferred not in usable:
            raise RuntimeError(f'{preferred} não anuncia modo AP.')
        return preferred
    if 'wlan0' in usable:
        return 'wlan0'
    if len(usable) == 1:
        return usable[0]
    raise RuntimeError('Nenhuma interface AP inequívoca. Use --interface; confira iw dev e iw phy.')


def check_ap(interface):
    import re
    info = run('iw', 'dev', interface, 'info', check=False)
    phy = re.search(r'wiphy\s+(\d+)', info.stdout)
    if not phy:
        raise RuntimeError('Interface sem PHY reconhecido: ' + interface)
    capabilities = run('iw', 'phy', 'phy' + phy.group(1), 'info').stdout
    if not re.search(r'^\s*\* AP\s*$', capabilities, re.M):
        raise RuntimeError('O driver não anuncia AP: ' + interface)


def ensure_packages(system, no_kiwix=False):
    if system['backend'] == 'openrc':
        repositories = Path('/etc/apk/repositories')
        text = repositories.read_text()
        # Mesmo ramo da instalação; nunca mistura edge nem outra versão estável.
        active = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith('#')]
        additions = [url[:-5] + '/community' for url in active if url.endswith('/main')
                     and url[:-5] + '/community' not in active]
        if additions:
            managed_write(repositories, text.rstrip() + '\n' + '\n'.join(additions))
        for cmd in package_commands(system, no_kiwix):
            result = run(*cmd)
            print(result.stdout[-2500:], flush=True)
        return
    policy = Path('/usr/sbin/policy-rc.d')
    created = not policy.exists() and not policy.is_symlink()
    if created:
        write(policy, '#!/bin/sh\nexit 101', 0o755)
    try:
        for cmd in package_commands(system, no_kiwix):
            # O apt recebe ambiente não interativo sem modificar configurações globais.
            result = subprocess.run(cmd, check=True, text=True,
                 env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
    finally:
        if created:
            policy.unlink()


def account():
    import grp
    import pwd
    try:
        grp.getgrnam('saberlocal')
    except KeyError:
        run('addgroup', '-S', 'saberlocal') if shutil.which('apk') else run('groupadd', '--system', 'saberlocal')
    try:
        pwd.getpwnam('saberlocal')
    except KeyError:
        if shutil.which('apk'):
            run('adduser', '-S', '-D', '-H', '-h', '/nonexistent', '-s', '/sbin/nologin', '-G', 'saberlocal', 'saberlocal')
        else:
            run('useradd', '--system', '--no-create-home', '--gid', 'saberlocal', '--home-dir', '/nonexistent',
                '--shell', '/usr/sbin/nologin', 'saberlocal')


def configure(config):
    ETC.mkdir(parents=True, exist_ok=True)
    public = {k: v for k, v in config.items() if k not in ('password', 'display_command')}
    managed_write(ETC / 'config.json', json.dumps(public, ensure_ascii=False, indent=2))
    managed_write(ETC / 'private.json', json.dumps(config, ensure_ascii=False, indent=2), 0o600)
    for path, (text, mode) in render(config).items():
        managed_write(path, text, mode)
    # Logrotate é invocado também durante operação contínua, sem dependência da rede.
    if config['backend'] == 'openrc':
        managed_write('/etc/periodic/15min/saberlocal-logrotate',
                      '#!/bin/sh\nexec /usr/sbin/logrotate /etc/logrotate.d/saberlocal', 0o755)
    else:
        managed_write('/etc/cron.d/saberlocal',
                      '*/15 * * * * root /usr/sbin/logrotate /etc/logrotate.d/saberlocal')


def stage_release():
    parent = Path('/opt/saberlocal/releases')
    parent.mkdir(parents=True, exist_ok=True)
    revision = run('git', '-C', ROOT, 'rev-parse', 'HEAD', check=False).stdout.strip()
    revision = revision[:12] if len(revision) == 40 else 'archive'
    dest = parent / (time.strftime('%Y%m%d%H%M%S') + '-' + revision + '-' + secrets.token_hex(3))
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', 'data',
                    'config.local.json', 'releases', 'current', 'venv'))
    run(sys.executable, '-m', 'venv', '--system-site-packages', '--without-pip', dest / '.venv')
    python = dest / '.venv/bin/python'
    run(python, '-c', 'import flask,waitress,sqlite3; '
        'db=sqlite3.connect(":memory:"); db.execute("CREATE VIRTUAL TABLE s USING fts5(text)")')
    return dest


def switch_release(dest):
    old = APP.resolve() if APP.is_symlink() else None
    if old:
        write(STATE / 'previous-release', str(old))
        index = STATE / 'v2-files.json'
        if index.exists():
            snapshot = {name: {'text': Path(name).read_text(), 'mode': Path(name).stat().st_mode & 0o777}
                        for name in json.loads(index.read_text()) if Path(name).is_file()}
            write(STATE / 'previous-configuration.json', json.dumps(snapshot), 0o600)
    temp = APP.with_name('current.new')
    temp.unlink(missing_ok=True)
    temp.symlink_to(dest, target_is_directory=True)
    os.replace(temp, APP)


def disable_interface_stanza(text, interface):
    """ifupdown: retira somente este rádio, mantendo eth0 e comentários externos."""
    lines, skip = [], False
    for line in text.splitlines(keepends=True):
        fields = line.split()
        if fields and fields[0] == 'iface':
            skip = len(fields) > 1 and fields[1] == interface
        elif fields and fields[0] in ('auto', 'allow-hotplug'):
            skip = False
            names = [n for n in fields[1:] if n != interface]
            if names:
                lines.append(fields[0] + ' ' + ' '.join(names) + '\n')
            continue
        elif fields and not line[0].isspace() and not line.lstrip().startswith('#'):
            skip = False
        if not skip:
            lines.append(line)
    return ''.join(lines)


def detach_radio(config):
    interface = config['interface']
    path = Path('/etc/network/interfaces')
    if path.exists():
        old = path.read_text()
        new = disable_interface_stanza(old, interface)
        if new != old:
            if shutil.which('ifdown'):
                run('ifdown', interface, check=False)
            managed_write(path, new)
    # Também trata arquivos incluídos do ifupdown sem alterar Ethernet.
    for path in Path('/etc/network/interfaces.d').glob('*'):
        if path.is_file():
            old = path.read_text()
            new = disable_interface_stanza(old, interface)
            if new != old:
                if shutil.which('ifdown'):
                    run('ifdown', interface, check=False)
                managed_write(path, new)
    if shutil.which('nmcli'):
        if not (STATE / 'nm-managed.json').exists():
            previous = run('nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', interface, check=False).stdout.strip()
            write(STATE / 'nm-managed.json', json.dumps({'interface': interface, 'managed': previous}))
        run('nmcli', 'device', 'disconnect', interface, check=False)
        run('nmcli', 'general', 'reload', check=False)
        run('nmcli', 'device', 'set', interface, 'managed', 'no', check=False)
    if config['backend'] == 'systemd' and shutil.which('networkctl'):
        run('networkctl', 'reload', check=False)
        run('networkctl', 'reconfigure', interface, check=False)


def running_manual(config):
    """Reconhece apenas os comandos manuais documentados, sem pkill genérico."""
    processes = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = [s.decode() for s in (proc / 'cmdline').read_bytes().split(b'\0') if s]
            if not args:
                continue
            name = Path(args[0]).name
            recognized = False
            for program, filename in [('hostapd', '/tmp/hostapd.conf'), ('dnsmasq', '/tmp/dnsmasq.conf')]:
                if name == program and any(a in (filename, '--conf-file=' + filename) for a in args):
                    contents = Path(filename).read_text()
                    fields = dict(l.split('=', 1) for l in contents.splitlines() if '=' in l and not l.startswith('#'))
                    recognized = fields.get('interface') == config['interface']
                    if program == 'hostapd':
                        recognized = recognized and fields.get('ssid') == config['ssid']
            if 'python' in name and any(a.endswith('scripts/dev.py') for a in args):
                recognized = (proc / 'cwd').resolve() == ROOT
            if recognized:
                processes.append((int(proc.name), (proc / 'stat').read_text().split()[21]))
        except (OSError, ValueError, UnicodeError):
            continue
    return processes


def stop_manual(processes):
    import signal
    for pid, start in processes:
        proc = Path('/proc', str(pid))
        if proc.exists() and (proc / 'stat').read_text().split()[21] == start:
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(Path('/proc', str(pid)).exists() for pid, _ in processes):
            return
        time.sleep(0.2)
    if any(Path('/proc', str(pid)).exists() for pid, _ in processes):
        raise RuntimeError('Um processo manual não encerrou. Nenhum SIGKILL foi enviado.')


def preflight_ports(config, manual):
    # Mesmo numa atualização, ocupantes não pertencentes ao projeto são recusados.
    approved = {pid for pid, _ in manual}
    for component in ('hostapd', 'dns', 'mdns', 'portal', 'kiwix', 'web'):
        # /proc identifica o executável por configuração, não por nome genérico.
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                command = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode()
                legacy_portal = ((proc / 'cwd').resolve() == Path('/opt/saberlocal')
                                 and 'app.server:create_app()' in command)
                if '/etc/saberlocal/' in command or '/opt/saberlocal/' in command or legacy_portal:
                    approved.add(int(proc.name))
            except (OSError, UnicodeError):
                pass
    import re
    for protocol, ports in (('tcp', (80, 8081, 8090)), ('udp', (53, 67, 5353))):
        for port in ports:
            output = run('ss', '-H', '-ltnp' if protocol == 'tcp' else '-lunp', f'sport = :{port}').stdout
            for line in output.splitlines():
                # DNS do resolved em loopback não compete com o IP do hotspot.
                if protocol == 'udp' and port == 53 and re.search(r'127\.[\d.]+:53', line):
                    continue
                pids = {int(p) for p in re.findall(r'pid=(\d+)', line)}
                if not pids or not pids.issubset(approved):
                    raise RuntimeError(f'Porta {protocol}/{port} ocupada por serviço externo. '
                                       'Não será interrompido automaticamente: ' + line.strip())


def selected_services(config):
    return [n for n in SERVICES if not (n == 'kiwix' and
            (not shutil.which('kiwix-serve') or not list((Path(config['data_dir']) / 'zim').glob('*.zim'))))
            and not (n == 'display' and config.get('display_backend', 'none') == 'none')]


def activate(config):
    if config['backend'] == 'systemd':
        run('systemctl', 'daemon-reload')
    for name in reversed(SERVICES):
        service(config, name, 'stop', check=False)
    detach_radio(config)
    for name in selected_services(config):
        enable(config, name)
        if config['backend'] == 'systemd':
            run('systemctl', 'reset-failed', 'saberlocal-' + name, check=False)
        service(config, name, 'start', check=name != 'display')
    for name in set(SERVICES) - set(selected_services(config)):
        enable(config, name, False)
    cron = 'crond' if config['backend'] == 'openrc' else 'cron'
    if config['backend'] == 'openrc':
        run('rc-update', 'add', cron, 'default')
        run('rc-service', cron, 'start')
    else:
        run('systemctl', 'enable', '--now', cron)


def health(config, timeout=45):
    import urllib.request
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen('http://10.42.0.1/healthz', timeout=3) as response:
                if json.load(response)['portal'] == 'ok':
                    break
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Portal não respondeu em 45 s; consulte saberlocal logs.')
        time.sleep(1)
    if 'kiwix' in selected_services(config):
        with urllib.request.urlopen('http://127.0.0.1:8081/biblioteca/', timeout=15) as response:
            if response.status != 200:
                raise RuntimeError('Kiwix não respondeu corretamente.')


def main():
    parser = argparse.ArgumentParser(description='SABERLOCAL 2.0 — instalação escolar offline')
    for name in ('interface', 'data-dir', 'storage-uuid', 'config'):
        parser.add_argument('--' + name)
    for flag in ('no-content', 'no-activate', 'check', 'no-kiwix', 'skip-packages', 'rollback'):
        parser.add_argument('--' + flag, action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Execute com sudo bash install.sh')
    system = detect()
    import fcntl
    Path('/run/lock').mkdir(parents=True, exist_ok=True)
    lock = open('/run/lock/saberlocal.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.rollback:
        from scripts.admin import rollback
        previous = json.loads((ETC / 'private.json').read_text()) if (ETC / 'private.json').exists() else system
        rollback(previous)
        return
    check_conflicts()
    if system['backend'] == 'openrc' and not Path('/run/openrc').exists():
        raise RuntimeError('OpenRC não está ativo. Prepare/inicie o Alpine antes da instalação.')
    if system['backend'] == 'systemd' and not Path('/run/systemd/system').exists():
        raise RuntimeError('systemd não está ativo neste sistema.')
    if system['backend'] == 'openrc':
        root_fs = run('findmnt', '-n', '-o', 'FSTYPE', '/', check=False).stdout.strip() if shutil.which('findmnt') else ''
        if root_fs in ('tmpfs', 'ramfs'):
            raise RuntimeError('Alpine diskless detectado. Use instalação persistente em modo sys; '
                               'não é possível prometer cold boot offline sem persistência de pacotes.')
    config = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
    if (ETC / 'private.json').exists():
        config.update(json.loads((ETC / 'private.json').read_text()))
    if args.config:
        overrides = json.loads(Path(args.config).read_text())
        if not overrides.get('password'):
            overrides.pop('password', None)
        config.update(overrides)
    config.update(system)
    if not config.get('password') or config['password'] == 'SaberLocal2026':
        config['password'] = secrets.token_urlsafe(12)
    if not args.check and not args.skip_packages:
        if shutil.disk_usage('/').free < 160 * 1024**2:
            raise RuntimeError('Menos de 160 MiB livres na raiz; libere espaço antes de instalar pacotes.')
        ensure_packages(system, args.no_kiwix)
    if system['backend'] == 'openrc':
        if run('findmnt', '-n', '-o', 'FSTYPE', '/').stdout.strip() in ('tmpfs', 'ramfs'):
            raise RuntimeError('Raiz volátil detectada. Prepare Alpine persistente em modo sys.')
    config['interface'] = discover_interface(args.interface or config.get('interface'))
    config['data_mount'] = config.get('data_mount') or '/mnt/saberlocal'
    config['data_dir'] = args.data_dir or config.get('data_dir') or config['data_mount'] + '/acervo'
    config['storage_uuid'] = args.storage_uuid or config.get('storage_uuid') or '21B5-12FD'
    volume = storage.identify(config['storage_uuid'])
    config['storage_fstype'] = volume['filesystem']
    validate(config)
    for route in json.loads(run('ip', '-j', '-4', 'route', 'show').stdout):
        dest = route.get('dst')
        if dest and dest != 'default' and route.get('dev') != config['interface']:
            if ipaddress.ip_network(dest, strict=False).overlaps(ipaddress.ip_network('10.42.0.0/24')):
                raise RuntimeError('10.42.0.0/24 já usada fora do rádio selecionado. Ethernet não será alterada.')
    manual = running_manual(config)
    preflight_ports(config, manual)
    if args.check:
        print(json.dumps({'system': system, 'interface': config['interface'], 'volume': volume,
                         'manual_processes_to_migrate': len(manual)}, ensure_ascii=False, indent=2))
        return
    print(f'Sistema: {system["os"]} {system["version"]} / {system["arch"]}; serviços: {system["backend"]}', flush=True)
    print('Instale por Ethernet ou console. O rádio será dedicado ao SABERLOCAL.', flush=True)
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    fstab = Path('/etc/fstab')
    before = fstab.read_text() if fstab.exists() else ''
    after = storage.edit_fstab(before, config)
    if after != before:
        write(STATE / 'fstab-managed.json', json.dumps(config), 0o600)
        write(fstab, after)
    storage.mount(config)
    storage.require(config)
    Path(config['data_dir']).mkdir(parents=True, exist_ok=True)
    seed(config['data_dir'])
    if not args.no_kiwix:
        if not shutil.which('kiwix-serve'):
            raise RuntimeError('kiwix-serve indisponível nos pacotes deste sistema. '
                               'Use --no-kiwix --no-content para instalar somente o portal.')
        run('kiwix-serve', '--version')  # Confirma executável real na arquitetura instalada.
    if not args.no_content:
        if args.no_kiwix:
            raise ValueError('--no-kiwix exige --no-content.')
        install_packs(config)
    release = stage_release()
    account()
    if manual and not args.no_activate:
        print(f'Migrando {len(manual)} processos manuais reconhecidos para serviços persistentes.', flush=True)
        stop_manual(manual)
    if not args.no_activate:
        for name in reversed(SERVICES):
            service(config, name, 'stop', check=False)
    switch_release(release)
    configure(config)
    from scripts.runtime import directories
    directories()
    # Detecta permissões sem conceder ao servidor capacidade de modificar o acervo.
    run('su', '-s', '/bin/sh', 'saberlocal', '-c', 'test -r ' + config['data_dir'] + '/catalog.sqlite')
    run('dnsmasq', '--test', '--conf-file=/etc/saberlocal/dnsmasq.conf')
    run('nft', '--check', '--file', '/etc/saberlocal/firewall.nft')
    if not args.no_activate:
        try:
            activate(config)
            health(config)
        except Exception:
            # Não deixa um AP parcialmente configurado anunciando sucesso.
            for name in reversed(SERVICES):
                service(config, name, 'stop', check=False)
            run('ip', 'addr', 'del', '10.42.0.1/24', 'dev', config['interface'], check=False)
            run('nft', 'delete', 'table', 'inet', 'saberlocal', check=False)
            print('Falha de ativação. Consulte saberlocal logs/status; configurações e acervo preservados.', file=sys.stderr)
            raise
    from scripts.admin import report
    ready = report(config, reveal_password=True)
    if args.no_activate:
        print('Preparado SEM ativação. Execute sudo saberlocal restart para habilitar e iniciar.')
    elif ready:
        print('Instalação concluída. Cold boot e capacidade ainda precisam de validação na TV Box.')
    else:
        raise RuntimeError('Instalação não está saudável; confira os componentes INATIVOS acima.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print('Erro: ' + (getattr(error, 'stderr', None) or str(error)), file=sys.stderr)
        sys.exit(1)
