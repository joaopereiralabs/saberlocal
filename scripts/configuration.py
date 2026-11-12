"""Renderizadores puros, compartilhados por Alpine e Debian/Ubuntu."""
import re
from pathlib import Path


def validate(config):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,15}', config['interface']):
        raise ValueError('Nome de interface inválido.')
    if not 1 <= len(config['ssid'].encode()) <= 32 or any(ord(c) < 32 for c in config['ssid']):
        raise ValueError('SSID inválido.')
    if not re.fullmatch(r'[\x20-\x7e]{8,63}', config['password']):
        raise ValueError('Senha Wi-Fi: 8 a 63 caracteres ASCII imprimíveis.')
    if not re.fullmatch(r'[A-Z]{2}', config['country']):
        raise ValueError('Código de país inválido.')
    if type(config['channel']) is not int or not 1 <= config['channel'] <= 11:
        raise ValueError('Canal inválido.')
    if not 0 < float(config['max_content_gib']) <= 1024:
        raise ValueError('Orçamento de conteúdo inválido.')
    for key in ('data_dir', 'data_mount'):
        value = config.get(key)
        if not value and key == 'data_mount':
            continue
        if not re.fullmatch(r'/[a-zA-Z0-9_./-]+', value or '') or '..' in Path(value).parts:
            raise ValueError('Caminho absoluto inválido: ' + key)
        if value == '/' or value.startswith(('/opt/', '/home/', '/root/', '/tmp/', '/etc/', '/run/')):
            raise ValueError('Escolha pasta de dados persistente separada.')
    if config.get('data_mount') and not Path(config['data_dir']).is_relative_to(config['data_mount']):
        raise ValueError('O acervo deve estar dentro da montagem do cartão.')
    for key in ('title', 'subtitle', 'institution'):
        if not isinstance(config[key], str) or not 1 <= len(config[key]) <= 160:
            raise ValueError('Campo inválido: ' + key)


def common(config):
    interface = config['interface']
    files = {}
    def add(path, value, mode=0o644):
        files[path] = (value.strip() + '\n', mode)
    add('/etc/saberlocal/hostapd.conf', f'''
interface={interface}
driver=nl80211
ssid={config['ssid']}
country_code={config['country']}
hw_mode=g
channel={config['channel']}
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase={config['password']}
ap_isolate=1
max_num_sta=20
''', 0o600)
    add('/etc/saberlocal/dnsmasq.conf', f'''
interface={interface}
listen-address=10.42.0.1
bind-dynamic
no-resolv
no-hosts
address=/saber.local/10.42.0.1
address=/#/10.42.0.1
local=/#/
dhcp-range=10.42.0.20,10.42.0.100,255.255.255.0,12h
dhcp-option=3,10.42.0.1
dhcp-option=6,10.42.0.1
dhcp-authoritative
dhcp-leasefile=/run/saberlocal/leases
user=saberlocal
group=saberlocal
''')
    add('/etc/saberlocal/nginx.conf', '''
user saberlocal;
pid /run/saberlocal/nginx.pid;
error_log stderr warn;
worker_processes 1;
events { worker_connections 128; }
http {
    include /etc/nginx/mime.types;
    access_log off;
    server_tokens off;
    client_max_body_size 1m;
    client_body_temp_path /run/saberlocal/client;
    proxy_temp_path /run/saberlocal/proxy;
    keepalive_timeout 15;
    server {
        listen 10.42.0.1:80 default_server;
        server_name saber.local 10.42.0.1;
        location = /biblioteca { return 302 http://10.42.0.1/biblioteca/; }
        location /biblioteca/ {
            proxy_pass http://127.0.0.1:8081;
            proxy_set_header Host $host;
            proxy_buffering off;
            proxy_read_timeout 60s;
            proxy_intercept_errors on;
            error_page 502 503 504 = /acervo-indisponivel;
        }
        location / {
            proxy_pass http://127.0.0.1:8090;
            proxy_set_header Host 10.42.0.1;
            proxy_buffering off;
        }
    }
}
''')
    add('/etc/saberlocal/avahi.conf', f'''
[server]
host-name=saber
domain-name=local
use-ipv4=yes
use-ipv6=no
allow-interfaces={interface}
enable-dbus=no
[publish]
publish-hinfo=no
publish-workstation=no
''')
    add('/etc/saberlocal/firewall.nft', f'''
table inet saberlocal {{
    chain input {{
        type filter hook input priority -10; policy accept;
        iifname "{interface}" udp dport {{ 53, 67, 5353 }} accept
        iifname "{interface}" tcp dport {{ 53, 80 }} accept
        iifname "{interface}" drop
    }}
    chain forward {{
        type filter hook forward priority -10; policy accept;
        iifname "{interface}" drop
        oifname "{interface}" drop
    }}
}}
''')
    add('/etc/NetworkManager/conf.d/90-saberlocal.conf', f'[keyfile]\nunmanaged-devices=interface-name:{interface}')
    add('/etc/logrotate.d/saberlocal', '''
/var/log/saberlocal/*.log {
    daily
    maxsize 1M
    rotate 3
    missingok
    notifempty
    compress
    copytruncate
    su root saberlocal
}
''')
    add('/usr/local/bin/saberlocal', '''#!/bin/sh
exec /opt/saberlocal/current/.venv/bin/python /opt/saberlocal/current/scripts/admin.py "$@"
''', 0o755)
    add('/usr/local/bin/saberlocal-display', '''#!/bin/sh
exec /opt/saberlocal/current/.venv/bin/python /opt/saberlocal/current/scripts/display.py "$@"
''', 0o755)
    return files


def command(name, config):
    app = '/opt/saberlocal/current'
    python = app + '/.venv/bin/python'
    return {
        'portal': (python, f'{app}/scripts/production.py', 'saberlocal'),
        'kiwix': (python, f'{app}/scripts/runtime.py kiwix', 'saberlocal'),
        'display': (python, f'{app}/scripts/display.py watch', 'root'),
        'hostapd': ('/usr/sbin/hostapd', '/etc/saberlocal/hostapd.conf', 'root'),
        'dns': ('/usr/sbin/dnsmasq', '--keep-in-foreground --conf-file=/etc/saberlocal/dnsmasq.conf --pid-file=', 'root'),
        'web': ('/usr/sbin/nginx', '-c /etc/saberlocal/nginx.conf -g "daemon off;"', 'root'),
        'mdns': ('/usr/sbin/avahi-daemon', '--no-chroot --file=/etc/saberlocal/avahi.conf', 'root'),
    }[name]


NEEDS = {'storage': (), 'network': ('storage',), 'hostapd': ('network',),
         'dns': ('network',), 'mdns': ('network',), 'portal': ('storage',),
         'kiwix': ('storage',), 'web': ('network', 'portal', 'hostapd', 'dns'), 'display': ()}


def openrc(name, config):
    needs = ' '.join('saberlocal-' + n for n in NEEDS[name])
    base = f'''#!/sbin/openrc-run
description="SABERLOCAL {name}"
depend() {{
    need localmount {needs}
    after networking
}}
'''
    runtime = '/opt/saberlocal/current/.venv/bin/python /opt/saberlocal/current/scripts/runtime.py'
    if name in ('storage', 'network'):
        return base + f'''
start() {{
    ebegin "SABERLOCAL {name}"
    {runtime} {name}
    eend $?
}}
stop() {{
    {runtime} {name}-stop
}}
'''
    executable, args, user = command(name, config)
    guard = '' if name == 'display' else f'    {runtime} guard || return 1\n'
    return base + f'''
supervisor="supervise-daemon"
command="{executable}"
command_args='{args}'
command_user="{user}"
directory="/opt/saberlocal/current"
pidfile="/run/saberlocal/{name}.pid"
output_log="/var/log/saberlocal/{name}.log"
error_log="/var/log/saberlocal/{name}.log"
respawn_delay=5
respawn_max=5
respawn_period=0
retry="TERM/10/KILL/5"
start_pre() {{
    checkpath -d -m 0755 -o saberlocal:saberlocal /run/saberlocal
    checkpath -d -m 0750 -o root:saberlocal /var/log/saberlocal
{guard}}}
'''


def systemd(name, config):
    dependencies = ' '.join('saberlocal-' + n + '.service' for n in NEEDS[name])
    header = f'''[Unit]
Description=SABERLOCAL {name}
After=local-fs.target {dependencies}
Requires={dependencies}
StartLimitIntervalSec=300
StartLimitBurst=5
'''
    runtime = '/opt/saberlocal/current/.venv/bin/python /opt/saberlocal/current/scripts/runtime.py'
    if name in ('storage', 'network'):
        body = f'Type=oneshot\nRemainAfterExit=yes\nExecStart={runtime} {name}\nExecStop={runtime} {name}-stop\n'
    else:
        executable, args, user = command(name, config)
        guard = '' if name == 'display' else f'ExecStartPre={runtime} guard\n'
        body = f'''User={user}
Group={user}
WorkingDirectory=/opt/saberlocal/current
{guard}ExecStart={executable} {args}
Restart=on-failure
RestartSec=10
NoNewPrivileges=yes
PrivateTmp=yes
MemoryMax={480 if name == 'kiwix' else 160}M
'''
    return header + f'''
[Service]
{body}StandardOutput=append:/var/log/saberlocal/{name}.log
StandardError=append:/var/log/saberlocal/{name}.log

[Install]
WantedBy=multi-user.target
'''


def render(config):
    files = common(config)
    from scripts.system import SERVICES
    for name in SERVICES:
        if config['backend'] == 'openrc':
            files['/etc/init.d/saberlocal-' + name] = (openrc(name, config), 0o755)
        else:
            files['/etc/systemd/system/saberlocal-' + name + '.service'] = (systemd(name, config), 0o644)
    if config['backend'] == 'systemd':
        files['/etc/systemd/network/00-saberlocal.network'] = (
            f'[Match]\nName={config["interface"]}\n[Link]\nUnmanaged=yes\n', 0o644)
    return files
