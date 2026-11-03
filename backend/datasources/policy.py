"""保守的表/列授权：先解析和限定列，再执行；不支持的SQL拒绝而非放行。"""
from pydantic import BaseModel, ConfigDict, Field
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.schema import MappingSchema
from datasources.models import SchemaSnapshot
from sql.executor import SqlExecuteError


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    principal: str = 'service'
    # 空字典拒绝全部；None表示允许该表全部列，空列表表示不允许任何列。
    tables: dict[str, list[str] | None] = Field(default_factory=dict)

    def apply(self, snapshot):
        known={t.id:{f.name for f in t.fields} for t in snapshot.tables}
        if not self.tables.keys()<=known.keys():
            raise ValueError('Policy引用未知表')
        if any(cols is not None and not set(cols)<=known[t] for t,cols in self.tables.items()):
            raise ValueError('Policy引用未知列')
        tables=[]
        for table in snapshot.tables:
            if table.id not in self.tables:
                continue
            allow=self.tables[table.id]
            fields=[f for f in table.fields if allow is None or f.name in allow]
            if not fields:
                continue
            fields_set={f.name for f in fields}
            tables.append(table.model_copy(update={'fields':fields,
                'primary_key':[c for c in table.primary_key if c in fields_set]}))
        allowed={t.id:{f.name for f in t.fields} for t in tables}
        relations=[r for r in snapshot.relations if r.left_field in allowed.get(r.left_table,set())
                   and r.right_field in allowed.get(r.right_table,set())]
        return snapshot.model_copy(update={'tables':tables,'relations':relations})


def authorize_sql(sql, snapshot, semantics=None, enforce_joins=False):
    try:
        statements=sqlglot.parse(sql,read=snapshot.dialect)
        if len(statements)!=1 or not isinstance(statements[0],exp.Query):
            raise ValueError('仅允许单条查询')
        tree=statements[0]
        if any(isinstance(n,(exp.DML,exp.DDL,exp.Command,exp.Into,exp.Lock)) for n in tree.walk()):
            raise ValueError('禁止写入和锁')
        if any(isinstance(n.expression,exp.Func) for n in tree.find_all(exp.Dot)):
            raise ValueError('禁止限定schema的函数，避免同名UDF绕过')
        # 用户SQL不得调用未知函数/系统函数/表函数；内建聚合日期函数由parser分类。
        functions={'AND','OR','NOT','EXISTS','COUNT','SUM','AVG','MIN','MAX','CAST','TRY_CAST','COALESCE','NULLIF','CASE','IF',
            'ROUND','ABS','CEIL','FLOOR','LOWER','UPPER','LENGTH','SUBSTRING','TRIM','CONCAT',
            'TIMESTAMP_TRUNC','DATE_TRUNC','TIME_TO_STR','EXTRACT','YEAR','MONTH','DAY',
            'DATE_ADD','DATE_DIFF','CURRENT_DATE','CURRENT_TIMESTAMP','ARRAY_AGG','JSON_OBJECT',
            'JSON_ARRAY','JSON_ARRAYAGG','ROW_NUMBER','RANK','DENSE_RANK','LAG','LEAD','STRFTIME',
            'DATE_PART','JSON_GROUP_ARRAY'}
        for func in tree.find_all(exp.Func):
            name=func.name.upper() if isinstance(func,exp.Anonymous) else func.sql_name()
            if name not in functions:
                raise ValueError('未授权函数')
        aliases={}
        mapping={}
        for table in snapshot.tables:
            mapping.setdefault(table.schema_name,{})[table.name]={f.name:f.type for f in table.fields}
        for scope in traverse_scope(tree):
            local={}
            for alias, (_, source) in scope.selected_sources.items():
                if not isinstance(source,exp.Table):
                    continue
                if source.catalog or not isinstance(source.this,exp.Identifier):
                    raise ValueError('禁止跨库和表函数')
                entries=[t for t in snapshot.tables if t.name==source.name and
                         (t.schema_name==source.db if source.db else True)]
                if len(entries)!=1:
                    raise ValueError('表未授权或未限定')
                table=entries[0]
                local[alias]=table.id
                source.set('db',exp.to_identifier(table.schema_name,quoted=True))
            aliases[id(scope.expression)]=local
        qualified=qualify(tree,dialect=snapshot.dialect,
            schema=MappingSchema(mapping,dialect=snapshot.dialect,normalize=False),
            validate_qualify_columns=True,quote_identifiers=True,identify=True)
        if enforce_joins:
            validate_joins(qualified,snapshot,semantics)
        return qualified.sql(dialect=snapshot.dialect)
    except sqlglot.errors.ParseError as exc:
        raise SqlExecuteError(sql,'SQL解析失败') from exc
    except Exception as exc:
        raise SqlExecuteError(sql,'安全拦截：SQL超出授权范围、合法JOIN范围或不受支持') from exc


def validate_joins(tree,snapshot,semantics):
    """仅允许可直接追溯物理表的显式等值JOIN；复杂派生JOIN保守拒绝。"""
    for scope in traverse_scope(tree):
        if scope.is_correlated_subquery:
            validate_correlation(scope,snapshot,semantics)
        for node in scope.expression.find_all(exp.In):
            if node.args.get('query') is not None:
                raise ValueError('IN子查询需改写为已批准的显式JOIN')
        joins=scope.expression.args.get('joins') or []
        sources={alias:source for alias,(_,source) in scope.selected_sources.items()}
        for join in joins:
            if join.args.get('method') or join.args.get('using') or not join.args.get('on'):
                raise ValueError('仅支持ON等值JOIN')
            if join.args.get('side') not in (None,'','LEFT','RIGHT'):
                raise ValueError('不支持的JOIN方向')
            # 不允许OR放宽关联键。
            if join.args['on'].find(exp.Or):
                raise ValueError('禁止OR连接条件')
            pairs=[]
            for eq in join.args['on'].find_all(exp.EQ):
                if isinstance(eq.left,exp.Column) and isinstance(eq.right,exp.Column):
                    pair=[]
                    for c in (eq.left,eq.right):
                        t=sources.get(c.table)
                        if not isinstance(t,exp.Table):
                            raise ValueError('派生表JOIN需显式语义支持')
                        identity=next(x.id for x in snapshot.tables if x.schema_name==t.db and x.name==t.name)
                        pair.append((identity,c.name))
                    pairs.append(tuple(pair))
            matched=False
            target_table=sources.get(join.this.alias_or_name)
            if not isinstance(target_table,exp.Table):
                raise ValueError('不支持派生表JOIN')
            target_id=next(t.id for t in snapshot.tables if t.name==target_table.name and t.schema_name==target_table.db)
            # 每一个跨表等值条件都必须来自某条合法关联，且当前JOIN必须真正连上新表。
            allowed={pair for rule in semantics.joins.values() for a,b in rule.keys
                     for pair in [((rule.left,a),(rule.right,b)),((rule.right,b),(rule.left,a))]}
            if any(p not in allowed for p in pairs if p[0][0]!=p[1][0]):
                raise ValueError('关联键未获批准')
            for rule in semantics.joins.values():
                required=[((rule.left,a),(rule.right,b)) for a,b in rule.keys]
                if target_id in (rule.left,rule.right) and all(p in pairs or tuple(reversed(p)) in pairs for p in required):
                    matched=True
                    if matched:
                        break
            if not matched:
                raise ValueError('未配置合法JOIN')


def validate_correlation(scope,snapshot,semantics):
    """允许可完整追溯的等值相关EXISTS，覆盖合法反连接；其他相关表达式拒绝。"""
    if scope.expression.find(exp.Or):
        raise ValueError('相关关联不支持OR')
    sources={}
    current=scope
    while current is not None:
        for alias,(_,source) in current.selected_sources.items():
            sources.setdefault(alias,source)
        current=current.parent
    pairs=[]
    linked=set()
    for eq in scope.expression.find_all(exp.EQ):
        if not isinstance(eq.left,exp.Column) or not isinstance(eq.right,exp.Column):continue
        pair=[]
        for column in (eq.left,eq.right):
            source=sources.get(column.table)
            if not isinstance(source,exp.Table):raise ValueError('关联来源不可追溯')
            identity=next(t.id for t in snapshot.tables if t.name==source.name and t.schema_name==source.db)
            pair.append((identity,column.name));linked.add((column.table,column.name))
        pairs.append(tuple(pair))
    if any((c.table,c.name) not in linked for c in scope.external_columns):
        raise ValueError('外部字段未使用批准的等值关联')
    allowed={p for rule in semantics.joins.values() for a,b in rule.keys
             for p in [((rule.left,a),(rule.right,b)),((rule.right,b),(rule.left,a))]}
    if not pairs or any(p not in allowed for p in pairs if p[0][0]!=p[1][0]):
        raise ValueError('相关关联未经批准')
    if not any(all(((rule.left,a),(rule.right,b)) in pairs or ((rule.right,b),(rule.left,a)) in pairs
                   for a,b in rule.keys) for rule in semantics.joins.values()):
        raise ValueError('相关关联键不完整')
