"""服务端语义定义：同名配置按默认→数据源→部署覆盖，整项替换。"""
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from datasources.models import SchemaSnapshot


class Metric(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tables: list[str]
    expression: str
    description: str = ''
    unit: str = ''
    grain: list[str] = Field(default_factory=list)
    time_field: str | None = None
    filters: list[str] = Field(default_factory=list)


class Join(BaseModel):
    model_config = ConfigDict(extra='forbid')
    left: str
    right: str
    keys: list[tuple[str, str]]
    cardinality: str = 'many_to_one'


class SemanticConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    metrics: dict[str, Metric] = Field(default_factory=dict)
    joins: dict[str, Join] = Field(default_factory=dict)
    time_fields: dict[str, str] = Field(default_factory=dict)

    def validate_snapshot(self, snapshot: SchemaSnapshot):
        import sqlglot
        from sqlglot import exp
        tables = {t.id: t for t in snapshot.tables}
        for name, column in self.time_fields.items():
            if name not in tables or column not in {f.name for f in tables[name].fields}:
                raise ValueError('时间字段引用无效')
        for join in self.joins.values():
            if not join.keys or join.cardinality not in ('one_to_one','many_to_one','one_to_many'):
                raise ValueError('JOIN必须明确键和粒度；不自动允许many-to-many')
            for table, columns in ((join.left,[a for a,b in join.keys]),(join.right,[b for a,b in join.keys])):
                if table not in tables or not set(columns) <= {f.name for f in tables[table].fields}:
                    raise ValueError('JOIN引用无效')
            unique_sides=[]
            if join.cardinality in ('one_to_one','one_to_many'):
                unique_sides.append((join.left,{a for a,b in join.keys}))
            if join.cardinality in ('one_to_one','many_to_one'):
                unique_sides.append((join.right,{b for a,b in join.keys}))
            for table,keys in unique_sides:
                if not tables[table].primary_key or not set(tables[table].primary_key)<=keys:
                    raise ValueError('JOIN的一端必须包含已探查主键以保证粒度')
        for metric in self.metrics.values():
            if not metric.tables or not set(metric.tables) <= tables.keys():
                raise ValueError('指标引用无效')
            if metric.time_field and not any(metric.time_field in {f.name for f in tables[t].fields} for t in metric.tables):
                raise ValueError('指标时间字段无效')
            columns = {f.name for t in metric.tables for f in tables[t].fields}
            if not set(metric.grain)<=columns:
                raise ValueError('指标粒度引用未知字段')
            for expression in [metric.expression, *metric.filters]:
                tree=sqlglot.parse_one(expression,read=snapshot.dialect)
                if tree.find(exp.Query) or tree.find(exp.DML) or tree.find(exp.DDL):
                    raise ValueError('语义表达式不得包含查询或写入')
                if any(c.name not in columns for c in tree.find_all(exp.Column)):
                    raise ValueError('指标表达式引用未知字段')
                for column in tree.find_all(exp.Column):
                    if column.table and not any(tables[t].name==column.table and
                        (not column.db or tables[t].schema_name==column.db) for t in metric.tables):
                        raise ValueError('指标字段限定表不属于依赖表')
        return self


def load_semantics(paths, snapshot):
    merged = {'metrics':{},'joins':{},'time_fields':{}}
    for path in paths:
        layer = SemanticConfig.model_validate_json(Path(path).read_text(encoding='utf-8')).model_dump()
        for section in merged:
            merged[section].update(layer[section])
    return SemanticConfig.model_validate(merged).validate_snapshot(snapshot)


def demo_semantics(snapshot):
    names={t.id for t in snapshot.tables}
    metrics={}
    for name,table,expr,desc in [('sales','orders','SUM(paid_amount)','销售额按实付金额统计'),
        ('order_count','orders','COUNT(*)','订单数量'),('customer_count','customers','COUNT(DISTINCT customer_id)','去重客户数')]:
        if table in names:
            metrics[name]=Metric(tables=[table],expression=expr,description=desc,
                                 time_field='order_date' if table=='orders' else None)
    joins={f'join_{i}':Join(left=r.left_table,right=r.right_table,keys=[(r.left_field,r.right_field)])
           for i,r in enumerate(snapshot.relations)}
    return SemanticConfig(metrics=metrics,joins=joins,
        time_fields={'orders':'order_date'} if 'orders' in names else {}).validate_snapshot(snapshot)
