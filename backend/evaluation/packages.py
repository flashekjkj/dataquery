"""标准离线包：本地夹具、独立参考SQL、预测SQL、权限与路径指标。"""
import argparse
import json
import shutil
import tempfile
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
import duckdb
import sqlglot
from sqlglot import exp
from datasources import DataSourceConfig, DataSourceRegistry
from evaluation.run import matches

ROOT=Path(__file__).resolve().parents[2]/'evaluation/packages'


class PackageManifest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=1
    name:str
    category:str
    fixture:str='init.sql'
    policy:str='policy.json'
    semantics:str='semantics.json'
    cases:str='cases.jsonl'


class PackageCase(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str
    question:str
    reference_sql:str=''
    replay_sql:str=''
    expected_action:str='query'
    expected_tables:list[str]=Field(default_factory=list)
    expected_edges:list[tuple[str,str]]=Field(default_factory=list)
    mutation_sql:list[str]=Field(default_factory=list)
    ordered:bool=False


def local_file(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('评测包文件不可越出包目录')
    return path


def paths(sql,dialect,snapshot):
    from sqlglot.optimizer.scope import traverse_scope
    tree=sqlglot.parse_one(sql,read=dialect)
    tables=set();edges=set()
    for scope in traverse_scope(tree):
        aliases={}
        current=scope
        while current is not None:
            for alias,(_,source) in current.selected_sources.items():
                if not isinstance(source,exp.Table):continue
                found=[t.id for t in snapshot.tables if t.name==source.name and (not source.db or t.schema_name==source.db)]
                if len(found)==1:
                    aliases.setdefault(alias,found[0]);tables.add(found[0])
            current=current.parent
        for eq in scope.expression.find_all(exp.EQ):
            if isinstance(eq.left,exp.Column) and isinstance(eq.right,exp.Column):
                a,b=aliases.get(eq.left.table),aliases.get(eq.right.table)
                if a and b and a!=b:edges.add(tuple(sorted((a,b))))
    return tables,edges


def evaluate(package,predictions=None,reference_replay=False):
    package=Path(package).resolve()
    manifest=PackageManifest.model_validate_json((package/'manifest.json').read_text(encoding='utf-8'))
    cases=[PackageCase.model_validate_json(line) for line in local_file(package,manifest.cases).read_text(encoding='utf-8').splitlines()]
    ids=[c.id for c in cases]
    if len(ids)!=len(set(ids)):
        raise ValueError('重复案例ID')
    if not reference_replay and predictions is None:
        raise ValueError('提供预测SQL；参考重放必须显式开启')
    results=[]
    for case in cases:
        if case.expected_action=='clarification':
            continue  # 单独的SQL评测包不伪造自然语言澄清分数。
        with tempfile.TemporaryDirectory() as tmp:
            database=Path(tmp)/'fixture.duckdb'
            fixture=local_file(package,manifest.fixture)
            if fixture.suffix=='.duckdb':
                shutil.copyfile(fixture,database)
            else:
                with duckdb.connect(str(database)) as conn:
                    conn.execute(fixture.read_text(encoding='utf-8'))
            registry=DataSourceRegistry()
            connector=registry.register(DataSourceConfig(source_id='eval',dialect='duckdb',path=str(database),
                demo_catalog=manifest.category=='ecommerce', policy_file=str(local_file(package,manifest.policy)),
                semantic_files=(str(local_file(package,manifest.semantics)),)))
            try:
                if case.mutation_sql:
                    with duckdb.connect(str(database)) as conn:
                        for sql in case.mutation_sql:
                            conn.execute(sql)
                sql=(case.replay_sql or case.reference_sql) if reference_replay else predictions.get(case.id,'')
                delivered=False;blocked=False;correct=False;coverage=0.;error=None
                try:
                    with connector.query_session():
                        query=connector.execute(sql)
                    delivered=True
                    if case.expected_action=='query':
                        with duckdb.connect(str(database),read_only=True) as conn:
                            expected=conn.execute(case.reference_sql).fetchall()
                        from sql.executor import _json_safe
                        expected=[[_json_safe(v) for v in row] for row in expected]
                        correct=not query.is_truncated and matches(expected,query.rows,case.ordered)
                    actual_tables,actual_edges=paths(sql,'duckdb',connector.inspect_schema())
                    gold_edges={tuple(sorted(e)) for e in case.expected_edges}
                    total=len(case.expected_tables)+len(gold_edges)
                    coverage=(len(set(case.expected_tables)&actual_tables)+len(gold_edges&actual_edges))/total if total else 1.
                except Exception as exc:
                    # 只有显式授权/Schema版本拒绝才算正确阻止，连接故障和语法错误不能冒充权限成功。
                    error=str(exc)
                    blocked=('Schema已变化' in error if case.expected_action=='stale' else '安全拦截' in error)
                action_correct=(correct if case.expected_action=='query' else blocked)
                results.append({'id':case.id,'sql':sql,'expected_action':case.expected_action,
                    'execution_success':delivered,'sql_correct':correct,'action_correct':action_correct,
                    'path_coverage':coverage,'error':error})
            finally:
                registry.close()
    queries=[r for r in results if r['expected_action']=='query']
    denies=[r for r in results if r['expected_action']!='query']
    mean=lambda rows,key:sum(r[key] for r in rows)/len(rows) if rows else None
    from datasources.secured import version
    import hashlib
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    fingerprints={key:digest(local_file(package,getattr(manifest,key))) for key in ('fixture','policy','semantics','cases')}
    return {'fingerprints':fingerprints,'scorer_sha256':digest(Path(__file__)), 'package':manifest.name,'category':manifest.category,'reference_replay':reference_replay,
        'note':'参考重放仅验证夹具与执行框架，不是模型成绩；SQL正确率采用独立参考结果等价。',
        'query_count':len(queries),'sql_accuracy':mean(queries,'sql_correct'),
        'execution_success_rate':mean(queries,'execution_success'),'table_path_coverage':mean(queries,'path_coverage'),
        'denial_count':len(denies),'denial_accuracy':mean(denies,'action_correct'),'cases':results}


def prepare():
    ROOT.mkdir(exist_ok=True)
    # 标注和SQL均为确定性人工设计；不连接远端数据库。
    from evaluation.package_fixtures import fixtures
    for name,data in fixtures().items():
        out=ROOT/name;out.mkdir(exist_ok=True)
        manifest=PackageManifest(name=name,category=name)
        (out/'init.sql').write_text(data['init'],encoding='utf-8')
        (out/'policy.json').write_text(json.dumps({'tables':data['tables']},ensure_ascii=False,indent=2),encoding='utf-8')
        (out/'semantics.json').write_text(json.dumps(data.get('semantics',{}),ensure_ascii=False,indent=2),encoding='utf-8')
        (out/'cases.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in data['cases']),encoding='utf-8')
        (out/'manifest.json').write_text(manifest.model_dump_json(indent=2),encoding='utf-8')
    # 原42条电商案例复用原夹具；多结果拆成单SQL子案例。
    out=ROOT/'ecommerce';out.mkdir(exist_ok=True)
    shutil.copyfile(ROOT.parent/'fixture.duckdb',out/'fixture.duckdb')
    from datasources.duckdb import DuckDBConnector
    snapshot=DuckDBConnector(DataSourceConfig(source_id='gold',dialect='duckdb',path=str(out/'fixture.duckdb'),demo_catalog=True)).inspect_schema()
    cases=[]
    for file in (ROOT.parent/'datasets').glob('*.jsonl'):
        if file.stem not in ('dev','holdout'):continue
        for original in map(json.loads,file.read_text(encoding='utf-8').splitlines()):
            for i,sql in enumerate(original['reference_sql']):
                tables,edges=paths(sql,'duckdb',snapshot)
                cases.append({'id':original['id']+'-'+str(i),'question':original['question'],'reference_sql':sql,'expected_tables':sorted(tables),'expected_edges':sorted(edges)})
    (out/'cases.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),encoding='utf-8')
    (out/'policy.json').write_text(json.dumps({'tables':{n:None for n in ('customers','orders','products')}}),encoding='utf-8')
    (out/'semantics.json').write_text('{}',encoding='utf-8')
    (out/'manifest.json').write_text(PackageManifest(name='ecommerce',category='ecommerce',fixture='fixture.duckdb').model_dump_json(indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','run'])
    parser.add_argument('--package');parser.add_argument('--predictions');parser.add_argument('--output')
    parser.add_argument('--reference-replay',action='store_true')
    args=parser.parse_args()
    if args.command=='prepare':prepare()
    else:
        predictions=json.loads(Path(args.predictions).read_text(encoding='utf-8')) if args.predictions else None
        result=evaluate(args.package,predictions,args.reference_replay)
        output=json.dumps(result,ensure_ascii=False,indent=2)
        if args.output:Path(args.output).write_text(output,encoding='utf-8')
        else:print(output)
