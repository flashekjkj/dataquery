from __future__ import annotations
from dataclasses import dataclass
from typing import List

@dataclass
class FieldDocument:
    """
    字段级Schema文档，每一个数据库字段对应一条文档
    用于后续Embedding向量化 + 向量检索
    """
    doc_id: str          # 唯一标识：table_name.field_name
    table_name: str      # 表id，如 customers
    table_desc: str      # 表的描述
    field_name: str      # 字段名，如 order_amount
    field_desc: str      # 字段含义描述
    field_type: str      # 字段类型
    field_label: str     # 字段中文标签
    aliases: List[str]   # 别名列表
    role: str            # 字段角色 identifier / metric / dimension
    semantic_text: str   # 语义检索文本（丢给embedding）
    keyword_text: str    # 关键词检索文本（后续混合检索BM25用）


class SchemaStore:
    """Schema存储器：读取表结构，批量生成字段文档列表"""
    def __init__(self, schema_tables: List[dict]):
        """
        :param schema_tables: 从data.database导入的SCHEMA全局变量
        """
        self.schema_tables = schema_tables

    def build_field_documents(self) -> List[FieldDocument]:
        """
        核心函数：遍历所有表、所有字段，扁平化生成每个字段对应的FieldDocument
        return: 全部字段文档列表，一条文档对应一个字段
        """
        field_docs: List[FieldDocument] = []

        for table in self.schema_tables:
            table_id = table["id"]
            table_desc = table["description"]
            fields = table["fields"]

            for field in fields:
                # 构造唯一id，例：orders.order_amount
                doc_id = f"{table_id}.{field['name']}"

                # 拼接语义文本，给embedding做向量检索
                semantic_text = (
                    f"表：{table_id}，表描述：{table_desc}，"
                    f"字段：{field['name']}，中文标签：{field['label']}，"
                    f"字段描述：{field['description']}，字段类型：{field['type']}，"
                    f"字段角色：{field['role']}，别名：{','.join(field['aliases'])}"
                )
                # 关键词文本，用于后续BM25检索
                keyword_text = f"{table_id} {field['name']} {field['label']} {field['description']} {' '.join(field['aliases'])}"

                doc = FieldDocument(
                    doc_id=doc_id,
                    table_name=table_id,
                    table_desc=table_desc,
                    field_name=field["name"],
                    field_desc=field["description"],
                    field_type=field["type"],
                    field_label=field["label"],
                    aliases=field["aliases"],
                    role=field["role"],
                    semantic_text=semantic_text,
                    keyword_text=keyword_text
                )
                field_docs.append(doc)
        return field_docs