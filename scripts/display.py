#!/usr/bin/env python3
"""Adaptadores de visor. Não escreve registradores, /dev/mem ou drivers desconhecidos."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def probe():
    candidates = []
    for pattern in ('/sys/class/leds/*', '/sys/class/display/*', '/sys/bus/platform/drivers/*fd65*',
                    '/dev/*vfd*', '/dev/*fd65*'):
        import glob
        candidates.extend(glob.glob(pattern))
    modules = Path('/proc/modules')
    return {'candidates': sorted(candidates), 'tx9_show': shutil.which('tx9-show'),
            'modules': [l.split()[0] for l in modules.read_text().splitlines()
                        if any(t in l.lower() for t in ('fd65', 'vfd', 'display'))] if modules.exists() else [],
            'note': 'Detecção não confirma capacidade de texto. Nenhum dispositivo foi escrito.'}


def text_for(state, config):
    width = int(config.get('display_width', 4))
    if not 2 <= width <= 32:
        raise ValueError('display_width deve estar entre 2 e 32.')
    text = {'boot': 'boot', 'ready': 'SaberLocal', 'error': 'Err'}[state]
    if state == 'ready' and width < len(text):
        text = 'SAbE' if width >= 4 else 'SL'
    return text[:width]


def show(state, config):
    backend = config.get('display_backend', 'none')
    text = text_for(state, config)
    if backend == 'none':
        return {'state': 'unsupported', 'message': 'Sem adaptador de visor configurado.'}
    if backend == 'command':
        command = config.get('display_command')
        if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
            raise ValueError('display_command deve ser uma lista de argumentos.')
        if not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
            raise ValueError('Executável do adaptador ausente; use um caminho absoluto.')
        if not any('{text}' in arg for arg in command):
            raise ValueError('Adaptador precisa de um argumento {text}.')
        subprocess.run([v.replace('{text}', text) for v in command], check=True,
                       timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env={**os.environ, 'LC_ALL': 'C'})
    elif backend == 'sysfs':
        # Uso explícito somente após o operador identificar um atributo de texto real.
        path = Path(config.get('display_path', '')).resolve()
        if not str(path).startswith('/sys/devices/') or path.name not in ('text', 'message'):
            raise ValueError('Configure um atributo sysfs de texto documentado (text/message).')
        with path.open('w') as stream:
            stream.write(text + '\n')
    else:
        raise ValueError('Adaptador de visor desconhecido.')
    return {'state': state, 'text': text, 'backend': backend}


def publish(value):
    folder = Path('/run/saberlocal')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'display.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value))
    temp.chmod(0o644)
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser(description='Visor SABERLOCAL; diagnóstico sem escrita por padrão')
    parser.add_argument('action', choices=['probe', 'watch', 'boot', 'ready', 'error'], default='probe', nargs='?')
    args = parser.parse_args()
    if args.action == 'probe':
        print(json.dumps(probe(), ensure_ascii=False, indent=2))
        return
    config = json.loads(Path('/etc/saberlocal/private.json').read_text())
    if args.action != 'watch':
        publish(show(args.action, config))
        return
    failures = 0
    previous = None
    state = 'boot'
    while True:
        try:
            if state != previous:
                result = show(state, config)
                publish(result)
                if result['state'] == 'unsupported':
                    # Serviço opcional não mantém um processo ocioso sem hardware integrado.
                    return
                previous = state
            time.sleep(10)
            from scripts.storage import require
            require(config)
            with urllib.request.urlopen('http://10.42.0.1/healthz', timeout=3) as response:
                state = 'ready' if response.status == 200 else 'error'
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            state = 'error'
            failures += 1
            publish({'state': 'error', 'message': str(error)})
            if failures >= 5:
                # Falha do adaptador não derruba os serviços educacionais.
                return
            time.sleep(5)


if __name__ == '__main__':
    main()
