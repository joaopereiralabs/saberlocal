"""Montagem por UUID e proteção contra gravar acervo na raiz sem cartão."""
import json
import os
import re
import stat
import time
from pathlib import Path
from scripts.system import run

FAT_LIMIT = 2**32 - 1
BEGIN = '# BEGIN SABERLOCAL STORAGE'
END = '# END SABERLOCAL STORAGE'


def validate_uuid(value):
    if not re.fullmatch(r'[a-zA-Z0-9-]{4,64}', value):
        raise ValueError('UUID inválido.')
    return value


def mount_record(path):
    result = run('findmnt', '-J', '-M', str(path), '-o', 'TARGET,SOURCE,FSTYPE,UUID,OPTIONS', check=False)
    if result.returncode:
        return None
    return json.loads(result.stdout)['filesystems'][0]


def require(config):
    mount = config.get('data_mount')
    if not mount:
        # Apenas demonstração/testes legados; instalação 2.0 sempre registra UUID.
        if config.get('storage_uuid'):
            raise RuntimeError('Montagem do cartão não configurada.')
        return
    record = mount_record(mount)
    if not record:
        raise RuntimeError(f'Cartão ausente em {mount}. Nenhum conteúdo será gravado na raiz.')
    expected = config.get('storage_uuid')
    if expected and (record.get('uuid') or '').lower() != expected.lower():
        raise RuntimeError('O volume montado não corresponde ao UUID configurado.')
    if 'ro' in record.get('options', '').split(','):
        raise RuntimeError('Cartão montado somente para leitura; verifique o sistema de arquivos.')
    return record


def fstab_entry(config):
    uuid = validate_uuid(config['storage_uuid'])
    mount = config['data_mount']
    filesystem = config['storage_fstype']
    options = 'noauto,nosuid,nodev,noexec'
    if filesystem in ('vfat', 'msdos'):
        options += ',uid=0,gid=0,fmask=0133,dmask=0022,utf8=1,flush'
    return f'UUID={uuid} {mount} {filesystem} {options} 0 0'


def edit_fstab(text, config=None):
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise RuntimeError('Bloco SABERLOCAL de fstab inválido; não foi alterado.')
    if BEGIN in text:
        start, end = text.index(BEGIN), text.index(END) + len(END)
        text = text[:start] + text[end:].lstrip('\n')
    if config is None:
        return text
    for line in text.splitlines():
        fields = line.split()
        if fields and not fields[0].startswith('#') and len(fields) >= 2:
            if fields[1] == config['data_mount'] or fields[0].lower() == ('UUID=' + config['storage_uuid']).lower():
                # Não duplicar/remover uma montagem que já pertence ao operador.
                if fields[1] == config['data_mount'] and fields[0].lower() == ('UUID=' + config['storage_uuid']).lower():
                    return text
                raise RuntimeError('UUID ou ponto de montagem já usado por outra linha de fstab.')
    return text.rstrip() + '\n\n' + BEGIN + '\n' + fstab_entry(config) + '\n' + END + '\n'


def identify(uuid):
    validate_uuid(uuid)
    matches = run('blkid', '-t', 'UUID=' + uuid, '-o', 'device', check=False).stdout.splitlines()
    if len(matches) != 1:
        raise RuntimeError(f'Esperado um único cartão com UUID {uuid}; encontrados {len(matches)}.')
    device = Path(matches[0]).resolve()
    if not stat.S_ISBLK(device.stat().st_mode):
        raise RuntimeError('UUID não corresponde a um dispositivo de bloco.')
    filesystem = run('blkid', '-s', 'TYPE', '-o', 'value', device).stdout.strip()
    if filesystem not in ('vfat', 'ext4', 'ext3', 'xfs', 'btrfs', 'exfat'):
        raise RuntimeError('Sistema de arquivos não suportado: ' + filesystem)
    root = run('findmnt', '-n', '-o', 'SOURCE', '/').stdout.strip()
    if root and Path(root).resolve() == device:
        raise RuntimeError('O UUID corresponde à partição raiz, não a um cartão de dados separado.')
    size = int(run('lsblk', '-b', '-n', '-o', 'SIZE', device).stdout.strip().splitlines()[0])
    if size < 512 * 1024**2:
        raise RuntimeError('Volume de dados menor que 512 MiB; confira o UUID.')
    return dict(device=str(device), filesystem=filesystem, bytes=size)


def mount(config, timeout=20):
    current = mount_record(config['data_mount'])
    if current:
        require(config)
        if config['storage_fstype'] == 'vfat':
            # Política restaurada no boot mesmo quando localmount já montou o cartão.
            run('mount', '-o', 'remount,nosuid,nodev,noexec,uid=0,gid=0,fmask=0133,dmask=0022,utf8=1', config['data_mount'])
        return require(config)
    deadline = time.monotonic() + timeout
    while True:
        try:
            volume = identify(config['storage_uuid'])
            break
        except (RuntimeError, OSError):
            if time.monotonic() >= deadline:
                raise RuntimeError('Cartão não apareceu dentro do prazo. Verifique UUID e conexão.')
            time.sleep(1)
    if volume['filesystem'] != config['storage_fstype']:
        raise RuntimeError('Formato do cartão mudou; execute novamente o instalador para validar.')
    Path(config['data_mount']).mkdir(parents=True, exist_ok=True)
    run('mount', config['data_mount'])
    require(config)
    if config['storage_fstype'] == 'vfat':
        run('mount', '-o', 'remount,nosuid,nodev,noexec,uid=0,gid=0,fmask=0133,dmask=0022,utf8=1', config['data_mount'])
    return require(config)


def file_limit(config, size):
    if config.get('storage_fstype') in ('vfat', 'msdos') and size > FAT_LIMIT:
        raise RuntimeError('Arquivo maior que o limite FAT32 (4 GiB menos 1 byte). '
                           'Use uma coleção menor ou cartão ext4; nenhum disco foi formatado.')
