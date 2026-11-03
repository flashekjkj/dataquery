"""SQLite字段/表向量缓存；不存问题、凭据或查询结果。"""
import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager


class VectorCache:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS vectors(namespace TEXT, document TEXT, hash TEXT, vector TEXT, PRIMARY KEY(namespace,document))')
        self.hits=self.misses=0

    @contextmanager
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def embedding(self,namespace,document,content,client):
        digest=hashlib.sha256(content.encode()).hexdigest()
        with self.connect() as conn:
            row=conn.execute('SELECT hash,vector FROM vectors WHERE namespace=? AND document=?',(namespace,document)).fetchone()
        if row and row[0]==digest:
            self.hits+=1
            return json.loads(row[1])
        vector=client.embedding(content).vector
        with self.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?)',(namespace,document,digest,json.dumps(vector)))
        self.misses+=1
        return vector

    def prune(self,namespace,documents):
        with self.connect() as conn:
            current=[r[0] for r in conn.execute('SELECT document FROM vectors WHERE namespace=?',(namespace,))]
            conn.executemany('DELETE FROM vectors WHERE namespace=? AND document=?',
                             [(namespace,d) for d in current if d not in documents])
