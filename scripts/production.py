#!/usr/bin/env python3
"""Servidor WSGI de produção, em loopback atrás do nginx."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.server import create_app
from scripts.storage import require


def main():
    config = json.loads(Path(os.environ.get('SABERLOCAL_CONFIG', '/etc/saberlocal/config.json')).read_text())
    require(config)
    from waitress import serve
    serve(create_app(config), host='127.0.0.1', port=8090, threads=2,
          connection_limit=48, channel_timeout=30, max_request_body_size=1024**2)


if __name__ == '__main__':
    main()
