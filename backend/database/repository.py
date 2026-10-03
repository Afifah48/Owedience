import sqlite3
from pathlib import Path
from backend.models.domain import Episode, now

class Repository:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)')
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
        return db
    def save(self, e):
        e.updated_at = now()
        with self.connect() as db:
            db.execute('INSERT INTO episodes VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at', (e.id,e.model_dump_json(),e.updated_at.isoformat()))
    def get(self, id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM episodes WHERE id=?',(id,)).fetchone()
        return Episode.model_validate_json(row[0]) if row else None
    def all(self):
        with self.connect() as db: rows = db.execute('SELECT data FROM episodes ORDER BY updated_at DESC').fetchall()
        return [Episode.model_validate_json(row[0]) for row in rows]
