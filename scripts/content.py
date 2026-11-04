#!/usr/bin/env python3
"""Downloads verificados e importação de materiais, sem dependências Python extras."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.catalog import add_document, initialize

GIB = 1024**3


def atomic_json(path, value, mode=0o644):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.chmod(mode)
    os.replace(temp, path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(pack, target, budget, opener=urllib.request.urlopen):
    """Retoma por Range; um servidor que ignora Range provoca reinício seguro."""
    url = pack['url']
    if not url.startswith('https://download.kiwix.org/zim/'):
        raise ValueError('Use somente o catálogo HTTPS oficial do Kiwix.')
    expected = pack['sha256']
    if not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('Checksum SHA-256 inválido.')
    target = Path(target)
    if target.exists() and sha256(target) == expected:
        return
    if target.exists():
        raise RuntimeError(f'{target} tem checksum inesperado; mova o arquivo para investigar.')
    partial = target.with_suffix('.zim.part')
    current = partial.stat().st_size if partial.exists() else 0
    with opener(urllib.request.Request(url, method='HEAD'), timeout=60) as head:
        size = int(head.headers['Content-Length'])
    if size <= 0 or size > budget:
        raise RuntimeError(f'Coleção de {size/GIB:.2f} GiB excede o limite disponível.')
    if current > size:
        partial.unlink()
        current = 0
    required = size - current
    if shutil.disk_usage(target.parent).free < required + 512 * 1024**2:
        raise RuntimeError('Sem espaço livre: reserve mais 512 MiB além do download.')
    if current < size:
        req = urllib.request.Request(url, headers={'Range': f'bytes={current}-'} if current else {})
        with opener(req, timeout=60) as response:
            if current and response.status == 206:
                if not response.headers.get('Content-Range', '').startswith(f'bytes {current}-'):
                    raise RuntimeError('Resposta Range inconsistente.')
            else:
                current = 0
            with partial.open('ab' if current else 'wb') as out:
                read = current
                for chunk in iter(lambda: response.read(1024 * 1024), b''):
                    read += len(chunk)
                    if read > size:
                        raise RuntimeError('Download maior que o tamanho informado.')
                    out.write(chunk)
    if partial.stat().st_size != size:
        raise RuntimeError('Download incompleto. Execute novamente para retomar.')
    if sha256(partial) != expected:
        partial.unlink()
        raise RuntimeError('SHA-256 incorreto. Parcial removido; execute novamente.')
    os.replace(partial, target)
    target.chmod(0o644)


def install_packs(config):
    data = Path(config['data_dir'])
    zims = data / 'zim'
    zims.mkdir(parents=True, exist_ok=True)
    manifest = data / 'installed-packs.json'
    installed = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
    for pack in json.loads((ROOT / 'content/packs.json').read_text(encoding='utf-8')):
        filename = pack['url'].rsplit('/', 1)[1]
        used = sum(p.stat().st_size for p in zims.glob('*.zim') if p.name != filename)
        budget = int(float(config.get('max_content_gib', 20)) * GIB) - used
        print(f'Baixando/verificando: {pack["title"]}', flush=True)
        download(pack, zims / filename, budget)
        installed = [p for p in installed if p['id'] != pack['id']]
        installed.append({**pack, 'filename': filename})
        atomic_json(manifest, installed)


def seed(data):
    initialize(data)
    for item in json.loads((ROOT / 'content/guides.json').read_text(encoding='utf-8')):
        add_document(data, item)


def import_document(data, args):
    source = Path(args.file).resolve(strict=True)
    if source.suffix.lower() not in ('.txt', '.md', '.pdf'):
        raise ValueError('Formatos aceitos: PDF, TXT e Markdown.')
    if source.stat().st_size > 100 * 1024**2:
        raise ValueError('Limite por arquivo: 100 MiB.')
    if source.suffix.lower() == '.pdf':
        result = subprocess.run(['pdftotext', '-enc', 'UTF-8', str(source), '-'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, check=True)
        body = result.stdout.decode('utf-8')
    else:
        body = source.read_text(encoding='utf-8')
    if not body.strip():
        raise ValueError('Sem texto pesquisável. PDFs escaneados precisam passar por OCR antes.')
    if len(body.encode('utf-8')) > 5 * 1024**2:
        raise ValueError('Texto extraído excede 5 MiB; divida o documento.')
    doc_id = uuid.uuid4().hex
    folder = Path(data) / 'documents'
    folder.mkdir(parents=True, exist_ok=True)
    filename = doc_id + source.suffix.lower()
    shutil.copyfile(source, folder / filename)
    (folder / filename).chmod(0o644)
    add_document(data, dict(id=doc_id, title=args.title, category=args.category,
                 body=body, source=args.source, license=args.license,
                 reviewed=args.date, filename=filename))
    print(f'Importado: {args.title} ({doc_id})')


def main():
    import fcntl
    lock = open('/run/lock/saberlocal.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    parser = argparse.ArgumentParser(description='Gerenciar a biblioteca SABERLOCAL')
    parser.add_argument('--config', default='/etc/saberlocal/config.json')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('download')
    sub.add_parser('seed')
    add = sub.add_parser('import')
    add.add_argument('file')
    for key in ('title', 'category', 'source', 'license', 'date'):
        add.add_argument('--' + key, required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    # Não criar pastas na eMMC caso a montagem do cartão desapareça.
    mount = config.get('data_mount')
    if mount and not Path(mount).is_mount():
        raise RuntimeError(f'Cartão não montado em {mount}.')
    seed(config['data_dir'])
    if args.action == 'download':
        install_packs(config)
        subprocess.run(['systemctl', 'restart', 'saberlocal-kiwix.service'], check=True)
    elif args.action == 'import':
        import_document(config['data_dir'], args)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'Erro: {error}', file=sys.stderr)
        sys.exit(1)
