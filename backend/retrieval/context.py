"""SQL上下文的结构补全，由在线Agent和检索评测共同调用。"""
import re

from retrieval.store import SchemaStore


def assemble_context(ranked, context, question, field_budget=12, semantic_k=8, table_scope=None):
    allowed = {t.id for t in context.snapshot.tables}
    if table_scope is not None:
        allowed.intersection_update(table_scope)
    ranked = [(doc,score) for doc,score in ranked if doc.table_name in allowed]
    selected = {doc.doc_id:doc for doc,_ in ranked[:semantic_k]}
    hit_tables = {doc.table_name for doc in selected.values()}
    catalog = {doc.doc_id:doc for doc in SchemaStore(context.snapshot.schema_tables).build_field_documents()}
    additions = []
    if re.search(r'\d{4}[-年]|\d{1,2}月|上月|本月|去年|今年|日期|时间', question):
        for doc in catalog.values():
            if doc.table_name in hit_tables and doc.role == 'time' and doc.doc_id not in selected:
                if len(selected) < field_budget:
                    selected[doc.doc_id] = doc
                    additions.append(doc.doc_id)

    joins = context.semantics.get('joins')
    if joins is None:
        # 兼容仅有已授权外键快照的旧调用方。
        joins = {str(i):{'left':r.left_table,'right':r.right_table,
                        'keys':[(r.left_field,r.right_field)]}
                 for i,r in enumerate(context.snapshot.relations)}
    relations = []
    skipped = []
    for name,join in joins.items():
        left,right = join['left'],join['right']
        if not {left,right} <= hit_tables:
            continue
        keys = [key for a,b in join['keys'] for key in (f'{left}.{a}',f'{right}.{b}')]
        missing = [key for key in keys if key not in selected]
        if not set(keys) <= catalog.keys() or len(selected)+len(set(missing)) > field_budget:
            skipped.append(name)
            continue
        for key in missing:
            selected[key] = catalog[key]
            additions.append(key)
        relations.append(' AND '.join(f'{left}.{a} = {right}.{b}' for a,b in join['keys']))
    diagnostics = {'semantic_fields':[doc.doc_id for doc,_ in ranked[:semantic_k]],
                   'added_fields':additions,'final_fields':list(selected),
                   'field_budget':field_budget,'skipped_joins':skipped}
    return list(selected.values()), relations, diagnostics
