import argparse
import hashlib
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from app.catalog import add_document, query
from app.server import create_app
from scripts.content import download, import_document, seed
from scripts.install import validate

ROOT = Path(__file__).resolve().parents[1]


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        seed(self.data)
        self.config = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
        self.config['data_dir'] = str(self.data)
        self.client = create_app(self.config).test_client()

    def test_home_is_school_portal_and_works_without_books(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('ENSINO FUNDAMENTAL E MÉDIO', html)
        self.assertIn('Frações: uma parte', html)
        self.assertNotIn('medicina', html.lower())
        self.assertIn('script-src', response.headers['Content-Security-Policy'])

    def test_accent_insensitive_fulltext_and_category(self):
        docs = query(self.data, 'fracoes', 'Matemática')
        self.assertEqual([d['id'] for d in docs], ['fracao'])
        self.assertFalse(query(self.data, 'fracoes', 'Geografia'))
        # Palavra do corpo, não apenas do título.
        self.assertEqual(query(self.data, 'denominador')[0]['id'], 'fracao')

    def test_search_characters_are_safe(self):
        for text in ['" OR *', '<script>alert(1)</script>', 'NEAR(a,b)', '"', '🎓']:
            response = self.client.get('/', query_string={'q': text})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('<script>', response.get_data(as_text=True))

    def test_seed_is_repeatable(self):
        before = len(query(self.data))
        seed(self.data)
        self.assertEqual(len(query(self.data)), before)

    def test_document_html_is_escaped(self):
        add_document(self.data, dict(id='xss', title='<script>x</script>', category='Tecnologia',
             body='<img src=x onerror=alert(1)>', source='<b>Autor</b>', license='MIT',
             reviewed='2026-11-04', filename=''))
        html = self.client.get('/documento/xss').get_data(as_text=True)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;script&gt;', html)

    def test_import_text_can_be_searched_and_downloaded(self):
        source = self.data / 'aula.txt'
        source.write_text('A fotossíntese transforma energia luminosa.', encoding='utf-8')
        args = argparse.Namespace(file=str(source), title='Ciências — 6º ano', category='Ciências',
                source='Professor', license='Autorizado', date='2026-11-04')
        import_document(self.data, args)
        item = query(self.data, 'fotossintese')[0]
        response = self.client.get('/arquivo/' + item['id'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), source.read_bytes())
        self.assertIn('attachment', response.headers['Content-Disposition'])
        response.close()

    def test_captive_probe_and_health(self):
        response = self.client.get('/generate_204')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, 'http://10.42.0.1/')
        self.assertEqual(self.client.get('/healthz').json, {'portal': 'ok', 'packs': 0})
        self.assertEqual(self.client.get('/acervo-indisponivel').status_code, 503)

    def test_search_links_use_installed_book_and_encode_query(self):
        pack = json.loads((ROOT / 'content/packs.json').read_text(encoding='utf-8'))[0]
        pack['filename'] = pack['url'].rsplit('/', 1)[1]
        (self.data / 'zim').mkdir()
        (self.data / 'zim' / pack['filename']).write_bytes(b'test fixture, not an actual ZIM')
        (self.data / 'installed-packs.json').write_text(json.dumps([pack]), encoding='utf-8')
        response = self.client.get('/pesquisar/matematica', query_string={'q': 'frações & números'})
        self.assertEqual(response.status_code, 302)
        url = urllib.parse.urlparse(response.location)
        self.assertEqual(url.path, '/biblioteca/search')
        params = urllib.parse.parse_qs(url.query)
        self.assertEqual(params['books.name'], [Path(pack['filename']).stem])
        self.assertEqual(params['pattern'], ['frações & números'])
        self.assertIn('/biblioteca/content/', self.client.get('/colecao/matematica').location)

    def test_missing_book_is_not_advertised(self):
        (self.data / 'installed-packs.json').write_text(json.dumps([{'filename':'missing.zim'}]))
        self.assertEqual(self.client.get('/healthz').json['packs'], 0)


class FakeResponse(io.BytesIO):
    def __init__(self, data=b'', status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'book.zim'
        self.payload = b'educational offline content'
        self.pack = {'url':'https://download.kiwix.org/zim/wikipedia/book.zim',
                     'sha256':hashlib.sha256(self.payload).hexdigest()}

    def opener(self, request, timeout):
        if request.get_method() == 'HEAD':
            return FakeResponse(headers={'Content-Length':str(len(self.payload))})
        return FakeResponse(self.payload)

    def test_checksum_verified_and_existing_does_not_download(self):
        download(self.pack, self.target, 1000, self.opener)
        self.assertEqual(self.target.read_bytes(), self.payload)
        def fail(*args, **kwargs):
            self.fail('Arquivo verificado não deve ser baixado novamente')
        download(self.pack, self.target, 1000, fail)

    def test_resume_range(self):
        self.target.with_suffix('.zim.part').write_bytes(self.payload[:5])
        def opener(req, timeout):
            if req.get_method() == 'HEAD':
                return self.opener(req, timeout)
            self.assertEqual(req.get_header('Range'), 'bytes=5-')
            return FakeResponse(self.payload[5:], 206, {'Content-Range':f'bytes 5-{len(self.payload)-1}/{len(self.payload)}'})
        download(self.pack, self.target, 1000, opener)
        self.assertEqual(self.target.read_bytes(), self.payload)

    def test_server_ignores_range_restarts_safely(self):
        self.target.with_suffix('.zim.part').write_bytes(b'old')
        download(self.pack, self.target, 1000, self.opener)
        self.assertEqual(self.target.read_bytes(), self.payload)

    def test_corruption_not_published_and_partial_removed(self):
        self.pack['sha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
            download(self.pack, self.target, 1000, self.opener)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_suffix('.zim.part').exists())

    def test_budget_rejected_before_transfer(self):
        with self.assertRaisesRegex(RuntimeError, 'limite'):
            download(self.pack, self.target, 1, self.opener)
        self.assertFalse(self.target.exists())

    def test_no_space_rejected(self):
        with patch('scripts.content.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaisesRegex(RuntimeError, 'espaço'):
                download(self.pack, self.target, 1000, self.opener)

    def test_incomplete_download_can_resume(self):
        def opener(req, timeout):
            if req.get_method() == 'HEAD':
                return self.opener(req, timeout)
            return FakeResponse(self.payload[:5])
        with self.assertRaisesRegex(RuntimeError, 'incompleto'):
            download(self.pack, self.target, 1000, opener)
        self.assertTrue(self.target.with_suffix('.zim.part').exists())
        self.assertFalse(self.target.exists())


class InstallerValidationTests(unittest.TestCase):
    def config(self):
        config = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
        config.update(interface='wlan0', password='senha-escola-123', data_dir='/mnt/saberlocal/acervo')
        return config

    def test_accepts_expected_config(self):
        validate(self.config())

    def test_rejects_injection_into_root_configs(self):
        for key, value in [('interface','wlan0\nexec=x'), ('ssid','Escola\nssid2=outro'),
                           ('password','senha\ninjetada'), ('data_dir','/mnt/$(id)'),
                           ('channel',True), ('country','BR\nXX')]:
            config = self.config()
            config[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(config)

    def test_manifest_education_only_and_hashes(self):
        packs = json.loads((ROOT / 'content/packs.json').read_text(encoding='utf-8'))
        self.assertEqual({p['id'] for p in packs}, {'matematica','ciencias','historia','geografia','livros'})
        for pack in packs:
            self.assertRegex(pack['sha256'], r'^[a-f0-9]{64}$')
            self.assertTrue(pack['url'].startswith('https://download.kiwix.org/zim/'))


if __name__ == '__main__':
    unittest.main()
