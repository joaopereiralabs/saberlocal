"""Integração em contêiner descartável; não simula Wi-Fi, cartão ou cold boot."""
import json
import argparse
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
from scripts.content import seed, import_document
from app.catalog import query
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
    # PDF mínimo válido para exercitar Poppler e os limites do subprocesso.
    stream = b'BT /F1 12 Tf 20 100 Td (Denominador: texto do PDF) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    pdf, offsets = b'%PDF-1.4\n', [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += str(i).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    start = len(pdf)
    pdf += b'xref\n0 6\n0000000000 65535 f \n'
    pdf += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets[1:])
    pdf += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
    source = Path(config['data_dir']) / 'fixture.pdf'
    source.write_bytes(pdf)
    import_document(config['data_dir'], argparse.Namespace(file=str(source), title='PDF de teste',
        category='Matemática', source='CI', license='MIT', date='2026-11-12'), config)
    assert any(d['title'] == 'PDF de teste' for d in query(config['data_dir'], 'denominador'))
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
        start_result = run('rc-service', '--nodeps', 'saberlocal-portal', 'start')
        print(start_result.stdout, start_result.stderr, flush=True)
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
            deadline = time.monotonic() + 10
            old = portal_pid()
            while old is None and time.monotonic() < deadline:
                time.sleep(0.3)
                old = portal_pid()
            if not old:
                log = Path('/var/log/saberlocal/portal.log')
                print(log.read_text()[-4000:] if log.exists() else 'Sem log do portal', flush=True)
                print(run('rc-service', 'saberlocal-portal', 'status', check=False), flush=True)
                for cmdline in Path('/proc').glob('[0-9]*/cmdline'):
                    try:
                        raw = cmdline.read_bytes().replace(b'\0', b' ')
                        if b'/opt/saberlocal' in raw:
                            print(raw.decode(), flush=True)
                    except OSError:
                        pass
                raise AssertionError('OpenRC não iniciou o WSGI')
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
