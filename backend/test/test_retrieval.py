from __future__ import annotations
import sys
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from app.model_client import ModelClient
from retrieval.service import build_hybrid_index

if __name__ == "__main__":
    client = ModelClient(mock=False)  
    hybrid = build_hybrid_index(client)
    res = hybrid.retrieve("销售额是多少", top_k=3)
    print("====混合检索RRF结果====")
    for doc, score in res:
        print(f"doc_id: {doc.doc_id}, 字段:{doc.field_name}, RRF分数:{score:.4f}")

    # 单独查看两路召回
    print("\n===== BM25单独召回 =====")
    bm25_res = hybrid.bm25_index.retrieve("销售额是多少", top_k=5)
    for d, s in bm25_res:
        print(f"{d.doc_id}, score:{s:.4f}")

    print("\n===== Vector单独召回 =====")
    vec_res = hybrid.vector_index.retrieve("销售额是多少", top_k=5)
    for d, s in vec_res:
        print(f"{d.doc_id}, score:{s:.4f}")
