"""SQLite FTS5 para materiais locais; arquivos ZIM ficam a cargo do Kiwix."""
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(data_dir):
    db = sqlite3.connect(Path(data_dir) / 'catalog.sqlite', timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000')
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(data_dir):
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    with connect(data_dir) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
          body TEXT NOT NULL, source TEXT NOT NULL, license TEXT NOT NULL,
          reviewed TEXT NOT NULL, filename TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
          id UNINDEXED, title, category, body, tokenize='unicode61 remove_diacritics 2');
        ''')


def add_document(data_dir, item):
    with connect(data_dir) as db:
        db.execute('INSERT OR REPLACE INTO documents VALUES '
                   '(:id,:title,:category,:body,:source,:license,:reviewed,:filename)', item)
        db.execute('DELETE FROM search WHERE id=?', (item['id'],))
        db.execute('INSERT INTO search VALUES (:id,:title,:category,:body)', item)


def query(data_dir, text='', category=''):
    with connect(data_dir) as db:
        tokens = re.findall(r'[^\W_]+', text[:200], re.UNICODE)[:12]
        args = []
        if tokens:
            sql = ('SELECT d.*, substr(d.body,1,220) AS excerpt FROM documents d '
                   'JOIN search s ON s.id=d.id WHERE search MATCH ?')
            args.append(' AND '.join('"' + t + '"*' for t in tokens))
        else:
            sql = 'SELECT d.*, substr(d.body,1,220) AS excerpt FROM documents d WHERE 1=1'
        if category:
            sql += ' AND d.category=?'
            args.append(category)
        sql += ' ORDER BY ' + ('rank' if tokens else 'title') + ' LIMIT 40'
        return [dict(row) for row in db.execute(sql, args)]
