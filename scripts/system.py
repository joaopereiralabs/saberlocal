"""Backend do sistema; nenhuma ação é executada ao importar este módulo."""
import os
import platform
import shlex
import subprocess
from pathlib import Path

SERVICES = ('storage', 'network', 'hostapd', 'dns', 'mdns', 'portal', 'kiwix', 'web', 'display')
APP = Path('/opt/saberlocal/current')
ETC = Path('/etc/saberlocal')
STATE = Path('/var/lib/saberlocal-installer')


def run(*args, check=True, **kwargs):
    return subprocess.run(list(map(str, args)), check=check, text=True,
                          capture_output=True, env={**os.environ, 'LC_ALL': 'C'}, **kwargs)


def detect(text=None, arch=None):
    text = text if text is not None else Path('/etc/os-release').read_text()
    values = {}
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key] = shlex.split(value)[0] if value else ''
    distro = values.get('ID', '')
    if distro not in ('alpine', 'ubuntu', 'debian'):
        raise RuntimeError('Sistema não suportado: ' + distro)
    return dict(os=distro, version=values.get('VERSION_ID', ''),
                arch=arch or platform.machine(), backend='openrc' if distro == 'alpine' else 'systemd')


ALPINE_PACKAGES = ('python3', 'py3-flask', 'py3-waitress', 'bash', 'git', 'ca-certificates',
    'openrc', 'hostapd', 'dnsmasq', 'nginx', 'iw', 'iproute2', 'nftables',
    'findmnt', 'lsblk', 'blkid', 'util-linux', 'poppler-utils', 'avahi', 'logrotate')
DEBIAN_PACKAGES = ('python3', 'python3-venv', 'python3-flask', 'python3-waitress', 'bash', 'git',
    'ca-certificates', 'hostapd', 'dnsmasq-base', 'nginx-core', 'iw', 'iproute2',
    'nftables', 'util-linux', 'poppler-utils', 'avahi-daemon', 'logrotate', 'cron')


def package_commands(system, no_kiwix=False):
    packages = list(ALPINE_PACKAGES if system['backend'] == 'openrc' else DEBIAN_PACKAGES)
    if not no_kiwix:
        packages.append('kiwix-tools')
    if system['backend'] == 'openrc':
        return [['apk', 'update'], ['apk', 'add', '--no-cache', *packages]]
    return [['apt-get', 'update'], ['apt-get', 'install', '-y', '--no-install-recommends', *packages]]


def service(system, name, action, check=True):
    full = 'saberlocal-' + name
    command = ['rc-service', full, action] if system['backend'] == 'openrc' else ['systemctl', action, full]
    return run(*command, check=check)


def enable(system, name, enabled=True):
    full = 'saberlocal-' + name
    if system['backend'] == 'openrc':
        return run('rc-update', 'add' if enabled else 'del', full, 'default', check=enabled)
    return run('systemctl', 'enable' if enabled else 'disable', full, check=enabled)


def active(system, name):
    if system['backend'] == 'openrc':
        result = run('rc-service', 'saberlocal-' + name, 'status', check=False)
    else:
        result = run('systemctl', 'is-active', '--quiet', 'saberlocal-' + name, check=False)
    return result.returncode == 0
