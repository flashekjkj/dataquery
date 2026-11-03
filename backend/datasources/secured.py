from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import time
import uuid
from datetime import datetime,timezone
from threading import Lock
from datasources.base import BaseConnector
from datasources.models import QueryContext
from datasources.policy import Policy, authorize_sql
from datasources.semantics import SemanticConfig, demo_semantics, load_semantics
from sql.executor import SqlExecuteError


def version(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class SecuredConnector(BaseConnector):
    def __init__(self, raw):
        super().__init__(raw.config)
        self.raw=raw
        self.lifecycle=Lock()
        self.active_sessions=0
        self.session=ContextVar('query_session_'+self.config.source_id,default=None)
        self.refresh()

    def refresh(self):
        with self.lifecycle:
            if self.active_sessions:
                raise ValueError('刷新前请等待当前问答结束')
            return self._refresh()

    def _refresh(self):
        from pathlib import Path
        full=self.raw.inspect_schema()
        self.full_version=full.version
        self.policy=Policy.model_validate_json(Path(self.config.policy_file).read_text(encoding='utf-8')) if self.config.policy_file else Policy(
            tables={t.id:None for t in full.tables} if self.config.demo_catalog else {})
        visible=self.policy.apply(full)
        semantics=demo_semantics(full) if self.config.demo_catalog else SemanticConfig()
        if self.config.semantic_files:
            # 默认低优先级，文件按顺序覆盖；同名条目整项替换。
            override=load_semantics(self.config.semantic_files,full)
            payload=semantics.model_dump()
            for section,data in override.model_dump().items():
                payload[section].update(data)
            semantics=SemanticConfig.model_validate(payload).validate_snapshot(full)
        names={t.id for t in visible.tables}
        payload=semantics.model_dump()
        payload['joins']={k:v for k,v in payload['joins'].items() if {v['left'],v['right']} <= names
            and all(a in {f.name for t in visible.tables if t.id==v['left'] for f in t.fields}
                    and b in {f.name for t in visible.tables if t.id==v['right'] for f in t.fields} for a,b in v['keys'])}
        payload['time_fields']={k:v for k,v in payload['time_fields'].items() if k in names
                               and v in {f.name for t in visible.tables if t.id==k for f in t.fields}}
        payload['metrics']={}
        for k,v in semantics.metrics.items():
            try:
                SemanticConfig(metrics={k:v}).validate_snapshot(visible)
                payload['metrics'][k]=v.model_dump()
            except ValueError:
                pass
        self.semantics=SemanticConfig.model_validate(payload)
        visible=visible.model_copy(deep=True)
        for table in visible.tables:
            for column in table.fields:
                if payload['time_fields'].get(table.id)==column.name:
                    column.role='time'
        self.context=QueryContext(snapshot=visible,policy_version=version(self.policy.model_dump()),
            semantic_version=version(payload),semantics=payload)
        return self.context

    def inspect_schema(self):
        return self.context.snapshot.model_copy(deep=True)

    def test_connection(self):
        return self.raw.test_connection()

    @contextmanager
    def query_session(self):
        with self.lifecycle:
            self.active_sessions+=1
        try:
            with self._query_session() as state:
                yield state
        finally:
            with self.lifecycle:
                self.active_sessions-=1

    @contextmanager
    def _query_session(self):
        if self.raw.inspect_schema().version != self.full_version:
            raise ValueError('Schema已变化，请刷新元数据并重建Agent')
        with self.raw.query_session() as snapshot_id:
            state={'snapshot_id':snapshot_id,'mode':self.config.query_mode,
                   'started':time.monotonic(),'source_id':self.config.source_id}
            token=self.session.set(state)
            try:
                yield {k:v for k,v in state.items() if k!='started'} | {'as_of':datetime.now(timezone.utc).isoformat()}
            finally:
                self.session.reset(token)

    def execute_readonly(self, sql):
        active=self.session.get()
        if active and active['mode']=='consistent' and time.monotonic()-active['started'] > self.config.snapshot_seconds:
            raise SqlExecuteError(sql,'快照生命周期已超时')
        query=authorize_sql(sql,self.context.snapshot,self.semantics,enforce_joins=True)
        return self.raw.execute_readonly(query)

    def sample_values(self, table, column, n=5):
        return [r[0] for r in self.execute_readonly(self.sample_sql(table,column,n)).rows]

    def close(self):
        self.raw.close()
