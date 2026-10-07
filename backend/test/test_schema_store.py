from __future__ import annotations
import sys
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE_BACKEND))

from retrieval.store import SchemaStore
from data.database import SCHEMA

def test_schema_store_build_docs():
    tables = SCHEMA
    store = SchemaStore(tables)
    docs = store.build_field_documents()

    total_field_count = 0
    for table in tables:
        total_field_count += len(table["fields"])

    assert len(docs) == total_field_count,\
    f"文档数量{len(docs)}不等于总字段数{total_field_count}"

    print(f"✅ test_schema_store 测试通过！总共生成 {len(docs)} 条字段文档")
    # 打印第一条文档查看
    if docs:
        print(f"示例文档doc_id: {docs[0].doc_id}")
        print(f"semantic_text:\n{docs[0].semantic_text}")

if __name__ == "__main__":
    test_schema_store_build_docs()