from datasources.base import BaseConnector, column_role, table_id
from datasources.models import SchemaSnapshot, TableSchema, ColumnSchema, RelationSchema
from datasources.demo import load_demo_catalog
from sql.executor import SQLExecutor
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from threading import Timer
import uuid


class DuckDBConnector(BaseConnector):
    def __init__(self, config):
        super().__init__(config)
        self.executor = SQLExecutor(config.path, config.max_rows, config.timeout_seconds)
        self.active=ContextVar('duck_connection_'+config.source_id,default=None)

    @contextmanager
    def query_session(self):
        if self.config.query_mode == 'independent':
            yield None
            return
        with self.executor._connect() as conn:
            conn.execute('BEGIN TRANSACTION')
            # 强制读取一次建立快照。
            conn.execute('SELECT COUNT(*) FROM information_schema.tables').fetchone()
            token=self.active.set(conn)
            expired=[]
            def expire():
                expired.append(True)
                conn.interrupt()
                conn.close()
            timer=Timer(self.config.snapshot_seconds,expire)
            timer.daemon=True
            timer.start()
            try:
                yield uuid.uuid4().hex
                if expired:
                    raise TimeoutError('一致快照超时')
            finally:
                timer.cancel()
                self.active.reset(token)
                if not expired:
                    conn.execute('ROLLBACK')

    def test_connection(self):
        self.execute_readonly("SELECT 1")
        return True

    def inspect_schema(self):
        with self.executor._connect() as conn:
            rows = conn.execute("""SELECT table_schema, table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_catalog = current_database()
                ORDER BY table_schema, table_name, ordinal_position""").fetchall()
            constraints = conn.execute("""SELECT schema_name, table_name, constraint_type,
                constraint_column_names, referenced_table, referenced_column_names
                FROM duckdb_constraints()""").fetchall()
        groups = {}
        for schema, name, column, type_name, nullable in rows:
            if self.config.schemas and schema not in self.config.schemas:
                continue
            groups.setdefault((schema, name), []).append((column, type_name, nullable))
        demo_tables, demo_relations = load_demo_catalog() if self.config.demo_catalog else ([], [])
        demo = {t['id']: t for t in demo_tables}
        tables = []
        ids = {}
        for (schema, name), columns in groups.items():
            identity = name if self.config.demo_catalog and schema == 'main' else table_id(schema, name)
            ids[(schema, name)] = identity
            pk = [c for s,t,k,cs,_,_ in constraints if (s,t,k)==(schema,name,'PRIMARY KEY') for c in cs]
            old = demo.get(name, {}) if schema == 'main' else {}
            fields = []
            for column, type_name, nullable in columns:
                metadata = next((f for f in old.get('fields', []) if f['name']==column), {})
                fields.append(ColumnSchema(name=column, type=type_name, nullable=nullable=='YES',
                    label=metadata.get('label', column), description=metadata.get('description', ''),
                    aliases=metadata.get('aliases', []), role=metadata.get('role', column_role(column,type_name,pk))))
            tables.append(TableSchema(id=identity, name=name, schema_name=schema,
                description=old.get('description', ''), primary_key=pk, fields=fields))
        relations = []
        for s,t,k,cols,target,targets in constraints:
            if k == 'FOREIGN KEY' and (s,t) in ids and (s,target) in ids:
                relations.extend(RelationSchema(left_table=ids[(s,t)],left_field=a,
                    right_table=ids[(s,target)],right_field=b) for a,b in zip(cols,targets))
        known = {t.id for t in tables}
        for r in demo_relations:
            if r['left_table'] in known and r['right_table'] in known:
                if not any((x.left_table,x.left_field,x.right_table,x.right_field)==
                           (r['left_table'],r['left_field'],r['right_table'],r['right_field']) for x in relations):
                    relations.append(RelationSchema(**r))
        return SchemaSnapshot(source_id=self.config.source_id, dialect='duckdb', tables=tables, relations=relations)

    def execute_readonly(self, sql):
        if self.active.get() is not None:
            executor=SQLExecutor(self.config.path,self.config.max_rows,self.config.timeout_seconds)
            executor._connect=lambda: nullcontext(self.active.get())
            return executor.execute(sql)
        return self.executor.execute(sql)

    def sample_values(self, table, column, n=5):
        return [row[0] for row in self.execute_readonly(self.sample_sql(table,column,n)).rows]

    def close(self):
        pass  # DuckDB每次操作独立打开并关闭只读连接。
