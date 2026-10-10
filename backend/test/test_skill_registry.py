from __future__ import annotations
import sys
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from skill.registry import SkillRegistry

SKILLS_DIR = BASE_BACKEND / "data" / "skills"

def test_load_all():
    registry = SkillRegistry(SKILLS_DIR)
    names = {s.name for s in registry.list_all()}
    assert names == {"sales_analysis", "customer_profile"}, f"加载结果不符：{names}"
    print(f"✅ test_load_all 通过！加载到 {len(registry.skills)} 个技能")

def test_match():
    registry = SkillRegistry(SKILLS_DIR)
    assert registry.match("华东地区上月销售额多少").name == "sales_analysis"
    assert registry.match("各等级客户数量").name == "customer_profile"
    assert registry.match("今天天气怎么样") is None
    print("✅ test_match 通过！关键词匹配与空匹配正确")

def test_get():
    registry = SkillRegistry(SKILLS_DIR)
    skill = registry.get("sales_analysis")
    assert skill.table_scope == ["orders", "customers"]
    assert registry.get("not_exist") is None
    print("✅ test_get 通过！按名获取正确")

if __name__ == "__main__":
    test_load_all()
    test_match()
    test_get()
    print("\n🎉 全部Skill注册表测试通过！")
