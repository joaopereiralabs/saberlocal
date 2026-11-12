import json
import os
import urllib.parse
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_from_directory
from .catalog import connect, query

ROOT = Path(__file__).resolve().parents[1]


def create_app(config=None):
    app = Flask(__name__)
    config_path = Path(os.environ.get('SABERLOCAL_CONFIG', '/etc/saberlocal/config.json'))
    if config is None:
        config = json.loads(config_path.read_text(encoding='utf-8'))
    app.config['SABERLOCAL'] = config
    data = Path(config['data_dir'])

    @app.before_request
    def storage_guard():
        from scripts.storage import require
        try:
            require(config)
        except (RuntimeError, OSError):
            return render_template('storage.html'), 503

    @app.context_processor
    def context():
        return {'settings': config}

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
        response.headers['Cache-Control'] = 'no-store'
        return response

    def packs():
        manifest = data / 'installed-packs.json'
        if not manifest.exists():
            return []
        return [p for p in json.loads(manifest.read_text(encoding='utf-8'))
                if (data / 'zim' / p['filename']).is_file()]

    @app.get('/')
    def home():
        text = request.args.get('q', '')[:200].strip()
        category = request.args.get('category', '')[:60]
        return render_template('index.html', q=text, category=category,
                               documents=query(data, text, category), packs=packs())

    @app.get('/documento/<doc_id>')
    def document(doc_id):
        with connect(data, readonly=True) as db:
            item = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if item is None:
            abort(404)
        return render_template('document.html', doc=dict(item))

    @app.get('/arquivo/<doc_id>')
    def file(doc_id):
        with connect(data, readonly=True) as db:
            item = db.execute('SELECT filename FROM documents WHERE id=?', (doc_id,)).fetchone()
        if item is None or not item['filename']:
            abort(404)
        return send_from_directory(data / 'documents', item['filename'], as_attachment=True)

    @app.get('/pesquisar/<pack_id>')
    def search_book(pack_id):
        pack = next((p for p in packs() if p['id'] == pack_id), None)
        if pack is None:
            abort(404)
        text = request.args.get('q', '')[:200].strip()
        if not text:
            return redirect('/biblioteca/')
        # O nome padrão do livro no kiwix-serve é o nome do ZIM sem extensão.
        params = urllib.parse.urlencode({'books.name': Path(pack['filename']).stem,
                                        'pattern': text})
        return redirect('/biblioteca/search?' + params)

    @app.get('/colecao/<pack_id>')
    def open_book(pack_id):
        pack = next((p for p in packs() if p['id'] == pack_id), None)
        if pack is None:
            abort(404)
        return redirect('/biblioteca/content/' + urllib.parse.quote(Path(pack['filename']).stem) + '/')

    @app.get('/acervo-indisponivel')
    def unavailable():
        return render_template('unavailable.html'), 503

    @app.get('/captive-portal')
    def captive_api():
        # RFC 8908 exige HTTPS para o endpoint; HTTP é só auxílio de descoberta
        # neste equipamento offline. Os probes HTTP usuais também são tratados.
        response = app.response_class(json.dumps({'captive': True,
                 'user-portal-url': 'http://10.42.0.1/'}),
                 mimetype='application/captive+json')
        return response

    @app.get('/healthz')
    def health():
        with connect(data, readonly=True) as db:
            db.execute('SELECT COUNT(*) FROM documents').fetchone()
        return {'portal': 'ok', 'packs': len(packs())}

    @app.errorhandler(404)
    def captive(_error):
        return redirect('http://10.42.0.1/', code=302)

    return app
