from __future__ import annotations
import sys
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE_BACKEND))

from retrieval.service import SchemaIndex
from app.model_client import ModelClient

def test_schema_index_retrieval():
    client = ModelClient(mock=False)
    index = SchemaIndex(model_client=client)
    index.build()

    query = "销售额"
    result = index.retrieve(query,top_k=3)
    assert len(result) > 0

    top1_doc,top1_score = result[0]
    assert top1_doc.doc_id == "orders.paid_amount",f"top1匹配错误，得到{top1_doc.doc_id}"

    print(f"✅ test_schema_index 通过！")
    print(f"查询：{query}")
    print(f"Top1 doc_id: {top1_doc.doc_id}, 相似度分数:{top1_score:.4f}")
    print(f"文档语义文本：{top1_doc.semantic_text}")


if __name__ == "__main__":
    test_schema_index_retrieval()