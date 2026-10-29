"""
SQL 响应解析测试：结构化输出（JSON）优先 + 正则兜底的逐级降级策略。

运行：python -m unittest test.test_sql_json -v 或 python test/test_sql_json.py
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from sql.generator import parse_sql_response, extract_sql


class ParseSqlResponseTests(unittest.TestCase):

    def test_plain_json(self):
        """json_object 模式的标准输出"""
        sql = parse_sql_response('{"sql": "SELECT COUNT(*) FROM orders"}')
        self.assertEqual(sql, "SELECT COUNT(*) FROM orders;")

    def test_fenced_json(self):
        """模型不听话包了 ```json 围栏也要能解析"""
        sql = parse_sql_response('```json\n{"sql": "SELECT 1"}\n```')
        self.assertEqual(sql, "SELECT 1;")

    def test_markdown_sql_fallback(self):
        """纯文本 ```sql 代码块（旧格式/降级模式）走正则兜底"""
        sql = parse_sql_response("好的，查询如下：\n```sql\nSELECT paid_amount FROM orders\n```")
        self.assertEqual(sql, "SELECT paid_amount FROM orders;")

    def test_mock_format_fallback(self):
        """mock 模式的「模拟SQL: xxx」格式"""
        sql = parse_sql_response("【Mock LLM】用户提问：xx\n模拟SQL：SELECT * FROM customers LIMIT 10;")
        self.assertEqual(sql, "SELECT * FROM customers LIMIT 10;")

    def test_plain_select_fallback(self):
        sql = parse_sql_response("SELECT customer_id FROM customers")
        self.assertEqual(sql, "SELECT customer_id FROM customers;")

    def test_json_without_sql_key_falls_back(self):
        """JSON 合法但缺 sql 键 → 继续走正则兜底"""
        sql = parse_sql_response('{"answer": "我不知道"}')
        self.assertEqual(sql, "")

    def test_garbage_returns_empty(self):
        self.assertEqual(parse_sql_response("你好，今天天气不错"), "")
        self.assertEqual(parse_sql_response(""), "")

    def test_normalize_semicolon_and_space(self):
        sql = parse_sql_response('{"sql": "SELECT   a,\\n  b  FROM t"}')
        self.assertEqual(sql, "SELECT   a,\n  b  FROM t;")


class ExtractSqlTests(unittest.TestCase):

    def test_fence_priority_over_select(self):
        """围栏内容优先于裸 SELECT"""
        text = "说明：这里没有SQL\n```sql\nSELECT 1\n```\nSELECT 2"
        self.assertEqual(extract_sql(text), "SELECT 1;")


if __name__ == "__main__":
    unittest.main()
