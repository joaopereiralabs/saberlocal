#!/usr/bin/env python3
"""Instalação Ubuntu/systemd. Não formata mídias, troca DTB ou compila drivers."""
import argparse
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.content import atomic_json, install_packs, seed

ETC = Path('/etc/saberlocal')
STATE = Path('/var/lib/saberlocal-installer')
DEST = Path('/opt/saberlocal')
UNITS = ['network', 'hostapd', 'dns', 'portal', 'kiwix', 'web']
PACKAGES = ['python3-flask', 'gunicorn', 'nginx-core', 'kiwix-tools',
            'hostapd', 'dnsmasq-base', 'iw', 'iproute2', 'nftables',
            'poppler-utils', 'ca-certificates']


def run(*args, check=True):
    return subprocess.run(list(args), check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)


def write(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new')
    temp.write_text(text.strip() + '\n', encoding='utf-8')
    temp.chmod(mode)
    os.replace(temp, path)


def managed_write(path, text, mode=0o644):
    """Guarda o estado anterior dos arquivos externos ao /etc/saberlocal."""
    path = Path(path)
    STATE.mkdir(parents=True, exist_ok=True)
    index = STATE / 'files.json'
    files = json.loads(index.read_text()) if index.exists() else {}
    if str(path) not in files:
        backup = None
        if path.exists():
            backup = str(STATE / (secrets.token_hex(12) + '.backup'))
            shutil.copy2(path, backup)
        files[str(path)] = backup
        atomic_json(index, files)
    write(path, text, mode)


def validate(config):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,15}', config['interface']):
        raise ValueError('Nome de interface inválido.')
    ssid = config['ssid']
    if not 1 <= len(ssid.encode('utf-8')) <= 32 or any(ord(c) < 32 for c in ssid):
        raise ValueError('SSID deve ter 1 a 32 bytes sem quebras de linha.')
    password = config['password']
    if not re.fullmatch(r'[\x20-\x7e]{8,63}', password):
        raise ValueError('Senha Wi-Fi: 8 a 63 caracteres ASCII imprimíveis.')
    if not re.fullmatch(r'[A-Z]{2}', config['country']):
        raise ValueError('País inválido; use BR.')
    if type(config['channel']) is not int or not 1 <= config['channel'] <= 11:
        raise ValueError('Canal deve estar entre 1 e 11.')
    if not 0 < float(config['max_content_gib']) <= 24:
        raise ValueError('Limite do acervo deve ser maior que zero e até 24 GiB.')
    if not re.fullmatch(r'/[a-zA-Z0-9_./-]+', config['data_dir']):
        raise ValueError('Use caminho absoluto para dados, sem espaços ou caracteres especiais.')
    data = Path(config['data_dir']).resolve()
    if data == Path('/') or data == DEST or DEST in data.parents:
        raise ValueError('Selecione uma pasta de dados separada do aplicativo.')
    if str(data).startswith(('/home/', '/root/', '/tmp/', '/var/tmp/')):
        raise ValueError('Use uma pasta persistente acessível ao serviço, como /mnt/saberlocal/acervo.')
    config['data_dir'] = str(data)
    for key in ('title', 'subtitle', 'institution'):
        if not isinstance(config[key], str) or not 1 <= len(config[key]) <= 160:
            raise ValueError(f'Campo {key} inválido.')


def discover_interface():
    result = run('iw', 'dev').stdout
    interfaces = re.findall(r'^\s*Interface\s+(\S+)', result, re.M)
    if len(interfaces) != 1:
        raise RuntimeError('Use --interface NOME. Veja as interfaces com: iw dev')
    return interfaces[0]


def check_ap(interface):
    info = run('iw', 'dev', interface, 'info').stdout
    phy = re.search(r'wiphy\s+(\d+)', info)
    if not phy:
        raise RuntimeError('A interface não possui PHY reconhecido pelo iw.')
    capabilities = run('iw', 'phy', 'phy' + phy.group(1), 'info').stdout
    if not re.search(r'^\s*\* AP\s*$', capabilities, re.M):
        raise RuntimeError('Este driver não anuncia modo AP. Consulte o projeto rbeninca/tvbox '
                           'para habilitar AP no SSV6051, ou use adaptador USB compatível.')


def detect_mount(data_dir, allow_internal):
    parent = Path(data_dir)
    while not parent.exists():
        parent = parent.parent
    mount = json.loads(run('findmnt', '-J', '-T', str(parent), '-o', 'TARGET,SOURCE,FSTYPE').stdout)['filesystems'][0]
    if mount['target'] == '/':
        # O sistema pode ter sido inicializado no próprio SD; exige opção explícita.
        if not allow_internal:
            raise RuntimeError('O cartão não está montado no caminho escolhido. Monte-o antes '
                               'ou use --allow-internal para autorizar dados na raiz do sistema.')
        return None
    if mount['target'].startswith(('/boot', '/proc', '/sys', '/dev', '/run')):
        raise RuntimeError('Escolha uma montagem de dados, como /mnt/saberlocal.')
    if mount['fstype'] not in ('ext4', 'ext3', 'xfs', 'btrfs'):
        raise RuntimeError('O cartão deve usar um sistema de arquivos Linux (recomendado: ext4).')
    return mount['target']


def ensure_packages():
    # Evita iniciar os serviços genéricos dos pacotes durante o apt.
    policy = Path('/usr/sbin/policy-rc.d')
    created = not policy.exists() and not policy.is_symlink()
    if created:
        write(policy, '#!/bin/sh\nexit 101', 0o755)
    try:
        env = {**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'}
        subprocess.run(['apt-get', 'update'], env=env, check=True)
        subprocess.run(['apt-get', 'install', '-y', '--no-install-recommends', *PACKAGES],
                       env=env, check=True)
    finally:
        if created:
            policy.unlink()


def unit(description, after, body, config, requires=''):
    mount = config.get('data_mount')
    mount_lines = (f'RequiresMountsFor={mount}\nConditionPathIsMountPoint={mount}\n'
                   if mount else '')
    return (f'[Unit]\nDescription={description}\nAfter={after}\n'
            f'{requires}{mount_lines}\n[Service]\n{body.strip()}\n\n'
            '[Install]\nWantedBy=multi-user.target\n')


def configure(config):
    iface = config['interface']
    data = config['data_dir']
    ETC.mkdir(parents=True, exist_ok=True)
    # Configuração pública não contém a senha de rede.
    public = {k: v for k, v in config.items() if k != 'password'}
    atomic_json(ETC / 'config.json', public)
    (ETC / 'config.json').chmod(0o644)
    write(ETC / 'hostapd.conf', f'''
interface={iface}
driver=nl80211
ssid={config['ssid']}
country_code={config['country']}
hw_mode=g
channel={config['channel']}
ieee80211n=0
wmm_enabled=0
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase={config['password']}
ap_isolate=1
max_num_sta=20
''', 0o600)
    # Senha preservada para reinstalação; acessível somente a root.
    atomic_json(ETC / 'private.json', config, mode=0o600)
    (ETC / 'private.json').chmod(0o600)
    write(ETC / 'dnsmasq.conf', f'''
interface={iface}
listen-address=10.42.0.1
bind-dynamic
no-resolv
no-hosts
address=/#/10.42.0.1
dhcp-range=10.42.0.10,10.42.0.100,255.255.255.0,12h
dhcp-option=3,10.42.0.1
dhcp-option=6,10.42.0.1
dhcp-authoritative
dhcp-leasefile=/run/saberlocal-dns/leases
user=nobody
group=nogroup
''')
    write(ETC / 'nginx.conf', '''
pid /run/saberlocal-web/nginx.pid;
error_log stderr warn;
worker_processes 1;
events { worker_connections 256; }
http {
    include /etc/nginx/mime.types;
    access_log off;
    server_tokens off;
    client_max_body_size 1m;
    client_body_temp_path /run/saberlocal-web/client;
    proxy_temp_path /run/saberlocal-web/proxy;
    server {
        listen 10.42.0.1:80 default_server;
        server_name _;
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
    # Gerenciadores de rede recebem exceção somente para a interface do projeto.
    managed_write('/etc/NetworkManager/conf.d/90-saberlocal.conf', f'''
[keyfile]
unmanaged-devices=interface-name:{iface}
''')
    managed_write('/etc/systemd/network/00-saberlocal.network', f'''
[Match]
Name={iface}
[Link]
Unmanaged=yes
''')
    write(ETC / 'network-up.sh', f'''
#!/bin/bash
set -Eeuo pipefail
ip link set dev {iface} up
ip -4 address flush dev {iface} scope global
ip address add 10.42.0.1/24 dev {iface}
# Política dedicada: não limpa tabelas de outros projetos.
nft list table inet saberlocal >/dev/null 2>&1 && nft delete table inet saberlocal
nft -f /etc/saberlocal/firewall.nft
''', 0o755)
    write(ETC / 'network-down.sh', f'''
#!/bin/bash
ip address del 10.42.0.1/24 dev {iface} 2>/dev/null || true
nft delete table inet saberlocal 2>/dev/null || true
''', 0o755)
    write(ETC / 'firewall.nft', f'''
table inet saberlocal {{
    chain input {{
        type filter hook input priority -10; policy accept;
        iifname "{iface}" udp dport {{ 53, 67 }} accept
        iifname "{iface}" tcp dport {{ 53, 80 }} accept
        iifname "{iface}" drop
    }}
    chain forward {{
        type filter hook forward priority -10; policy accept;
        iifname "{iface}" drop
        oifname "{iface}" drop
    }}
}}
''')
    dependent = 'Requires=saberlocal-network.service\nPartOf=saberlocal-network.service\n'
    device = 'sys-subsystem-net-devices-' + iface.replace('-', r'\x2d') + '.device'
    services = {
      'network': unit('SABERLOCAL - rede escolar', f'network.target {device}', '''
Type=oneshot
RemainAfterExit=yes
ExecStart=/etc/saberlocal/network-up.sh
ExecStop=/etc/saberlocal/network-down.sh
''', config, f'BindsTo={device}\n'),
      'hostapd': unit('SABERLOCAL - Wi-Fi', 'saberlocal-network.service', '''
ExecStart=/usr/sbin/hostapd /etc/saberlocal/hostapd.conf
Restart=on-failure
RestartSec=5
''', config, dependent),
      'dns': unit('SABERLOCAL - DNS e DHCP', 'saberlocal-network.service', '''
RuntimeDirectory=saberlocal-dns
RuntimeDirectoryMode=0755
ExecStartPre=/usr/bin/chown nobody:nogroup /run/saberlocal-dns
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=/etc/saberlocal/dnsmasq.conf --pid-file=
Restart=on-failure
RestartSec=5
''', config, dependent),
      'portal': unit('SABERLOCAL - portal escolar', 'network.target', f'''
User=saberlocal
Group=saberlocal
WorkingDirectory=/opt/saberlocal
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=SABERLOCAL_CONFIG=/etc/saberlocal/config.json
ExecStart=/usr/bin/gunicorn --bind 127.0.0.1:8090 --workers 1 --threads 2 --timeout 45 --error-logfile - app.server:create_app()
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
MemoryMax=160M
''', config),
      'kiwix': unit('SABERLOCAL - biblioteca Kiwix', 'network.target', '''
User=saberlocal
Group=saberlocal
ExecStart=/bin/bash /opt/saberlocal/scripts/kiwix.sh
Restart=on-failure
RestartSec=10
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
MemoryMax=480M
''', config),
      'web': unit('SABERLOCAL - acesso HTTP', 'saberlocal-network.service saberlocal-portal.service saberlocal-kiwix.service', '''
User=www-data
Group=www-data
RuntimeDirectory=saberlocal-web
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=/usr/sbin/nginx -c /etc/saberlocal/nginx.conf -g "daemon off;"
ExecReload=/usr/sbin/nginx -c /etc/saberlocal/nginx.conf -s reload
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
MemoryMax=80M
''', config, dependent)
    }
    for name, text in services.items():
        managed_write(f'/etc/systemd/system/saberlocal-{name}.service', text)
    managed_write('/usr/local/bin/saberlocal', (ROOT / 'scripts/saberlocal').read_text(), 0o755)


def stop_services():
    run('systemctl', 'disable', '--now', *(f'saberlocal-{n}.service' for n in reversed(UNITS)), check=False)


def rollback():
    if not (STATE / 'files.json').exists():
        raise RuntimeError('Nenhuma instalação com backup foi encontrada.')
    previous = {}
    if (STATE / 'network.json').exists():
        previous = json.loads((STATE / 'network.json').read_text())
    stop_services()
    files = json.loads((STATE / 'files.json').read_text())
    for name, backup in files.items():
        if backup:
            shutil.copy2(backup, name)
        else:
            Path(name).unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')
    run('nmcli', 'general', 'reload', check=False) if shutil.which('nmcli') else None
    if shutil.which('nmcli') and previous.get('managed') in ('yes', 'no'):
        run('nmcli', 'device', 'set', previous['interface'], 'managed', previous['managed'], check=False)
    if shutil.which('networkctl'):
        run('networkctl', 'reload', check=False)
    print('Serviços e exceções de rede removidos/restaurados. Acervo preservado. '
          'Reconecte o Wi-Fi anterior se necessário. Pacotes e /opt/saberlocal preservados.')


def main():
    parser = argparse.ArgumentParser(description='Instalar SABERLOCAL na TV Box')
    parser.add_argument('--interface')
    parser.add_argument('--data-dir')
    parser.add_argument('--config')
    parser.add_argument('--allow-internal', action='store_true')
    parser.add_argument('--no-content', action='store_true')
    parser.add_argument('--no-activate', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Execute com sudo.')
    import fcntl
    lock = open('/run/lock/saberlocal.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.rollback:
        rollback()
        return
    if not Path('/run/systemd/system').exists():
        raise RuntimeError('Requer Ubuntu/Armbian com systemd em execução.')
    release = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=(?:"?ubuntu"?|"?debian"?)$', release, re.M):
        raise RuntimeError('Distribuição não suportada; use Ubuntu/Armbian baseado em Ubuntu ou Debian.')
    config = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
    if (ETC / 'private.json').exists():
        config.update(json.loads((ETC / 'private.json').read_text(encoding='utf-8')))
    if args.config:
        config.update(json.loads(Path(args.config).read_text(encoding='utf-8')))
    config['password'] = config.get('password') or secrets.token_hex(6)
    config['data_dir'] = args.data_dir or config.get('data_dir') or '/mnt/saberlocal/acervo'
    if not args.check:
        if shutil.disk_usage('/').free < 800 * 1024**2:
            raise RuntimeError('Reserve pelo menos 800 MiB livres na partição do sistema para os pacotes.')
        ensure_packages()
    for command in ('iw', 'ip', 'findmnt', 'ss', 'nft'):
        if not shutil.which(command):
            raise RuntimeError(f'Comando ausente: {command}. Instale as dependências ou execute sem --check.')
    config['interface'] = args.interface or config.get('interface') or discover_interface()
    validate(config)
    check_ap(config['interface'])
    config['data_mount'] = detect_mount(config['data_dir'], args.allow_internal)
    routes = json.loads(run('ip', '-j', '-4', 'route', 'show').stdout)
    for route in routes:
        dest = route.get('dst', '')
        if dest not in ('default', '') and route.get('dev') != config['interface']:
            if ipaddress.ip_network(dest, strict=False).overlaps(ipaddress.ip_network('10.42.0.0/24')):
                raise RuntimeError('A rede 10.42.0.0/24 já está em uso em outra interface.')
    # O canal de instalação deve continuar disponível até os downloads terminarem.
    print(f'Interface: {config["interface"]}; dados: {config["data_dir"]}', flush=True)
    print('O Wi-Fi escolhido será dedicado à escola. Faça a instalação por cabo Ethernet ou console.', flush=True)
    # Não substitui outros servidores locais.
    existing = (ETC / 'private.json').exists()
    if not existing:
        for port in (80, 8081, 8090):
            if run('ss', '-H', '-ltn', f'sport = :{port}').stdout.strip():
                raise RuntimeError(f'Porta TCP {port} já ocupada. Ajuste o serviço existente antes de instalar.')
        for port in (53, 67):
            lines = run('ss', '-H', '-lun', f'sport = :{port}').stdout
            if re.search(r'(?:0\.0\.0\.0|\*|\[::\]|10\.42\.0\.1):' + str(port), lines):
                raise RuntimeError(f'Porta UDP {port} ocupada por DNS/DHCP global. Resolva o conflito primeiro.')
    if args.check:
        print('Verificações básicas concluídas. A criação real do AP ainda precisa ser testada no aparelho.')
        return
    data = Path(config['data_dir'])
    data.mkdir(parents=True, exist_ok=True)
    seed(data)
    if not args.no_content:
        install_packs(config)
    # Copia apenas arquivos do projeto; não copia Git, ambiente virtual ou dados.
    if ROOT != DEST:
        shutil.copytree(ROOT, DEST, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', 'data', 'config.local.json'))
    if run('id', '-u', 'saberlocal', check=False).returncode:
        run('useradd', '--system', '--no-create-home', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', 'saberlocal')
    # Permite somente leitura do acervo ao serviço; administração é feita pelo terminal.
    for folder, dirs, files in os.walk(data):
        Path(folder).chmod(0o755)
        for name in files:
            Path(folder, name).chmod(0o644)
    if not (STATE / 'network.json').exists():
        STATE.mkdir(parents=True, exist_ok=True)
        previous = {'interface': config['interface']}
        if shutil.which('nmcli'):
            previous['managed'] = run('nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show',
                                      config['interface'], check=False).stdout.strip()
        atomic_json(STATE / 'network.json', previous)
    configure(config)
    run('runuser', '-u', 'saberlocal', '--', 'test', '-r', str(data / 'catalog.sqlite'))
    run('systemctl', 'daemon-reload')
    run('nft', '--check', '--file', '/etc/saberlocal/firewall.nft')
    run('dnsmasq', '--test', '--conf-file=/etc/saberlocal/dnsmasq.conf')
    if args.no_activate:
        print('Instalação preparada sem ativar rede. Para ativar: sudo systemctl enable --now saberlocal-{network,hostapd,dns,portal,kiwix,web}')
        return
    try:
        # NM desconecta somente o rádio selecionado, sem reiniciar o gerenciador.
        if shutil.which('nmcli'):
            run('nmcli', 'device', 'disconnect', config['interface'], check=False)
            run('nmcli', 'general', 'reload', check=False)
            run('nmcli', 'device', 'set', config['interface'], 'managed', 'no', check=False)
        if shutil.which('networkctl'):
            run('networkctl', 'reload', check=False)
            run('networkctl', 'reconfigure', config['interface'], check=False)
        run('systemctl', 'stop', *(f'saberlocal-{n}' for n in reversed(UNITS)), check=False)
        run('systemctl', 'enable', '--now', *(f'saberlocal-{n}' for n in UNITS))
        time.sleep(3)
        for name in UNITS:
            if name == 'kiwix' and not list((data / 'zim').glob('*.zim')):
                continue
            run('systemctl', 'is-active', '--quiet', f'saberlocal-{name}')
        import urllib.request
        with urllib.request.urlopen('http://10.42.0.1/healthz', timeout=15) as response:
            if json.load(response)['portal'] != 'ok':
                raise RuntimeError('Portal não respondeu corretamente.')
    except (subprocess.SubprocessError, OSError, RuntimeError):
        stop_services()
        print('Falha ao ativar. Serviços interrompidos. Veja sudo saberlocal logs; '
              'para restaurar as exceções de rede use sudo saberlocal rollback.', file=sys.stderr)
        raise
    print(f'\nSABERLOCAL instalado. Rede: {config["ssid"]}\nSenha Wi-Fi: {config["password"]}\n'
          'Portal: http://10.42.0.1\nAnote a senha. Ela também está em /etc/saberlocal/private.json (somente root).')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr or str(error), file=sys.stderr)
        else:
            print(f'Erro: {error}', file=sys.stderr)
        sys.exit(1)
