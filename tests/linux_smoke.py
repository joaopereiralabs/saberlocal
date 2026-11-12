"""Integração em contêiner descartável; não simula Wi-Fi, cartão ou cold boot."""
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.configuration import render
from scripts.content import seed
from scripts.files import write
from scripts.install import account
from scripts.runtime import directories
from scripts.system import APP, detect, run


def main():
    if os.environ.get('SABERLOCAL_CI') != '1' or not Path('/.dockerenv').exists():
        raise RuntimeError('Execute somente em contêiner descartável com SABERLOCAL_CI=1.')
    system = detect()
    print('Linux integration:', system, flush=True)
    config = json.loads((ROOT / 'config.example.json').read_text())
    # Fixture de acervo local, explicitamente sem cartão/rádio em CI.
    config.update(system, interface='wlan0', password='test-only-password', data_dir='/var/lib/saberlocal-smoke',
                  data_mount=None, storage_uuid=None, storage_fstype='ext4')
    account()
    directories()
    APP.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT, APP, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__'))
    run(sys.executable, '-m', 'venv', '--system-site-packages', '--without-pip', APP / '.venv')
    seed(config['data_dir'])
    Path(config['data_dir']).chmod(0o755)
    Path(config['data_dir'], 'catalog.sqlite').chmod(0o644)
    for name, (text, mode) in render(config).items():
        write(name, text, mode)
    write('/etc/saberlocal/config.json', json.dumps(config))
    write('/etc/saberlocal/private.json', json.dumps(config), 0o600)
    # Interface Ethernet fictícia no namespace do contêiner. Não é um teste de rádio.
    run('ip', 'link', 'add', 'wlan0', 'type', 'dummy')
    run('ip', 'link', 'set', 'wlan0', 'up')
    run('ip', 'addr', 'add', '10.42.0.1/24', 'dev', 'wlan0')
    run('dnsmasq', '--test', '--conf-file=/etc/saberlocal/dnsmasq.conf')
    run('nginx', '-t', '-c', '/etc/saberlocal/nginx.conf')
    run('kiwix-serve', '--version')
    run('avahi-daemon', '--version')
    for path in Path('/etc/init.d').glob('saberlocal-*'):
        run('sh', '-n', path)
    if system['backend'] == 'systemd':
        units = list(Path('/etc/systemd/system').glob('saberlocal-*.service'))
        run('systemd-analyze', 'verify', *units)
    # Executa o mesmo servidor WSGI de produção com usuário sem privilégios.
    proc = subprocess.Popen(['su', '-s', '/bin/sh', 'saberlocal', '-c',
                   str(APP / '.venv/bin/python') + ' ' + str(APP / 'scripts/production.py')],
                   start_new_session=True)
    children = []
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=1) as response:
                    assert json.load(response)['portal'] == 'ok'
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        with urllib.request.urlopen('http://127.0.0.1:8090/?q=denominador') as response:
            assert 'Frações' in response.read().decode()
        for cmd in [
            ['dnsmasq', '--keep-in-foreground', '--conf-file=/etc/saberlocal/dnsmasq.conf', '--pid-file='],
            ['avahi-daemon', '--no-chroot', '--file=/etc/saberlocal/avahi.conf'],
            ['nginx', '-c', '/etc/saberlocal/nginx.conf', '-g', 'daemon off;']]:
            child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            children.append(child)
        time.sleep(1)
        for child in children:
            if child.poll() is not None:
                raise RuntimeError(child.communicate()[0].decode())
        with urllib.request.urlopen('http://10.42.0.1/healthz', timeout=3) as response:
            assert json.load(response)['portal'] == 'ok'
        # Consulta DNS A sem dependência de resolver público.
        question = b'\x05saber\x05local\x00' + struct.pack('!HH', 1, 1)
        packet = struct.pack('!HHHHHH', 123, 0x100, 1, 0, 0, 0) + question
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            sock.sendto(packet, ('10.42.0.1', 53))
            answer = sock.recv(4096)
        assert answer[:2] == packet[:2] and socket.inet_aton('10.42.0.1') in answer
    finally:
        for child in reversed(children):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
            child.communicate(timeout=10)
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    if system['backend'] == 'openrc':
        # Exercita de verdade o init script e uma recuperação de queda do filho.
        Path('/run/openrc').mkdir(exist_ok=True)
        Path('/run/openrc/softlevel').touch()
        run('rc-service', '--nodeps', 'saberlocal-portal', 'start')
        def portal_pid():
            for path in Path('/proc').glob('[0-9]*/cmdline'):
                try:
                    args = path.read_bytes().split(b'\0')
                    if args and b'python' in Path(os.fsdecode(args[0])).name.encode() and str(APP / 'scripts/production.py').encode() in args:
                        return int(path.parent.name)
                except OSError:
                    pass
            return None
        try:
            time.sleep(1)
            old = portal_pid()
            assert old, 'OpenRC não iniciou o WSGI'
            os.kill(old, signal.SIGTERM)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                new = portal_pid()
                if new and new != old:
                    break
                time.sleep(0.3)
            else:
                log = Path('/var/log/saberlocal/portal.log')
                print(log.read_text()[-4000:] if log.exists() else 'Sem log do portal', flush=True)
                print(run('rc-service', 'saberlocal-portal', 'status', check=False), flush=True)
                raise AssertionError('OpenRC não recuperou o processo encerrado')
            run('rc-service', 'saberlocal-portal', 'status')
        finally:
            run('rc-service', '--nodeps', 'saberlocal-portal', 'stop', check=False)
    print('OK: real package executables, daemon configuration and production portal.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stdout or '', file=sys.stderr)
        print(error.stderr or '', file=sys.stderr)
        raise
