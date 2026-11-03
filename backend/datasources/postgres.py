"""PostgreSQL反射、有限连接池与每次操作独立的只读事务。"""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Timer
import uuid
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
import sqlglot
from sqlglot import exp
from datasources.base import BaseConnector, column_role, table_id
from datasources.models import SchemaSnapshot, TableSchema, ColumnSchema, RelationSchema
from sql.executor import QueryResult, SqlExecuteError, _json_safe


class PostgresConnector(BaseConnector):
    def __init__(self, config):
        super().__init__(config)
        url = make_url(config.url.get_secret_value())
        if url.get_backend_name() != 'postgresql':
            raise ValueError('仅支持PostgreSQL URL')
        url = url.set(drivername='postgresql+psycopg')
        self.engine = create_engine(url, pool_size=config.pool_size, max_overflow=0,
            pool_pre_ping=True, pool_timeout=config.timeout_seconds, pool_reset_on_return='rollback',
            connect_args={'connect_timeout': max(1, int(config.timeout_seconds)),
                          'options': '-c default_transaction_read_only=on'}, hide_parameters=True)
        self.active=ContextVar('pg_connection_'+config.source_id,default=None)

    @contextmanager
    def query_session(self):
        if self.config.query_mode == 'independent':
            yield None
            return
        with self._connection() as conn:
            token=self.active.set(conn)
            driver=conn.connection.driver_connection
            expired=[]
            def expire():
                expired.append(True)
                try:
                    driver.cancel()
                finally:
                    driver.close()
            timer=Timer(self.config.snapshot_seconds,expire)
            timer.daemon=True
            timer.start()
            try:
                # 在首次业务查询前固定MVCC快照。
                conn.exec_driver_sql('SELECT txid_current_snapshot()')
                yield uuid.uuid4().hex
                if expired:
                    raise TimeoutError('一致快照超时')
            finally:
                timer.cancel()
                self.active.reset(token)

    @contextmanager
    def _connection(self):
        current=self.active.get()
        if current is not None:
            with current.begin_nested():
                yield current
            return
        with self.engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                             {'timeout': str(max(1, int(self.config.timeout_seconds*1000)))})
                # 不依赖调用者的search_path；生成和采样使用限定名。
                conn.exec_driver_sql('SET LOCAL search_path = pg_catalog')
                yield conn
            finally:
                transaction.rollback()

    def test_connection(self):
        with self._connection() as conn:
            conn.exec_driver_sql('SELECT 1').scalar_one()
        return True

    def inspect_schema(self):
        tables, relations = [], []
        with self._connection() as conn:
            inspector = inspect(conn)
            schemas = self.config.schemas or tuple(s for s in inspector.get_schema_names()
                if not s.startswith('pg_') and s != 'information_schema')
            for schema in schemas:
                if schema not in inspector.get_schema_names():
                    raise ValueError('配置的schema不存在')
                for name in sorted(set(inspector.get_table_names(schema=schema) + inspector.get_view_names(schema=schema))):
                    identity = table_id(schema, name)
                    pk = inspector.get_pk_constraint(name,schema=schema).get('constrained_columns') or []
                    columns = inspector.get_columns(name,schema=schema)
                    tables.append(TableSchema(id=identity,name=name,schema_name=schema,
                        description=inspector.get_table_comment(name,schema=schema).get('text') or '',
                        primary_key=pk, fields=[ColumnSchema(name=c['name'],type=str(c['type']),
                            nullable=c['nullable'],label=c['name'],description=c.get('comment') or '',
                            role=column_role(c['name'],str(c['type']),pk)) for c in columns]))
                    for fk in inspector.get_foreign_keys(name,schema=schema,postgresql_ignore_search_path=True):
                        target = table_id(fk.get('referred_schema') or schema, fk['referred_table'])
                        relations.extend(RelationSchema(left_table=identity,left_field=a,
                            right_table=target,right_field=b) for a,b in
                            zip(fk['constrained_columns'],fk['referred_columns']))
        known = {t.id for t in tables}
        return SchemaSnapshot(source_id=self.config.source_id,dialect='postgres',tables=tables,
                              relations=[r for r in relations if r.right_table in known])

    def _check_safe(self, sql):
        try:
            statements = sqlglot.parse(sql,read='postgres')
            if len(statements)!=1 or not isinstance(statements[0],exp.Query):
                raise ValueError('仅允许单条SELECT/WITH查询')
            query = statements[0]
            forbidden=(exp.DML, exp.DDL, exp.Into, exp.Lock, exp.Command)
            if any(isinstance(node,forbidden) for node in query.walk()):
                raise ValueError('查询中包含写入、锁或命令')
            return query.sql(dialect='postgres')
        except Exception as exc:
            raise SqlExecuteError(sql,'安全拦截：仅允许只读查询') from exc

    def execute_readonly(self, sql):
        query = self._check_safe(sql)
        try:
            with self._connection() as conn:
                result = conn.exec_driver_sql(f'SELECT * FROM ({query}) AS _askdata_result LIMIT {self.config.max_rows+1}')
                columns = list(result.keys())
                raw = result.fetchmany(self.config.max_rows+1)
                rows = [[_json_safe(v) for v in r] for r in raw[:self.config.max_rows]]
                return QueryResult(columns=columns,rows=rows,row_count=len(rows),
                                   is_truncated=len(raw)>self.config.max_rows)
        except Exception as exc:
            # DBAPI异常可能带连接信息或长SQL，只回传PG诊断主消息。
            diag = getattr(getattr(exc,'orig',None),'diag',None)
            message = getattr(diag,'message_primary',None) or 'PostgreSQL查询失败或连接不可用'
            raise SqlExecuteError(sql,message) from exc

    def sample_values(self, table, column, n=5):
        return [r[0] for r in self.execute_readonly(self.sample_sql(table,column,n)).rows]

    def close(self):
        self.engine.dispose()
