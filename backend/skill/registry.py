from __future__ import annotations
import json
from pathlib import Path
from typing import List,Optional

from pydantic import BaseModel

class Skill(BaseModel):
    """单个技能定义：对应 data/skills/{name}/skill.json"""
    name:str
    display_name:str
    description:str
    trigger_keywords:List[str]
    table_scope:List[str]
    system_instructions:str = ""
    example_questions:List[str] = []
    mcp_tools:List[str] = []

class SkillRegistry:
    """
    技能注册表：扫描data/skills目录加载全部skill.json
    提供关键词匹配与按名获取
    """
    def __init__(self,skill_dir:Path):
        self.skills_dir = skill_dir
        self.skills:List[Skill] = []
        self.load_all()

    def load_all(self) -> None:
        self.skills = []
        for skill_file in sorted(self.skills_dir.glob("*/skill.json")):
            payload = json.loads(skill_file.read_text(encoding="utf-8"))
            self.skills.append(Skill(**payload))

    def get(self,name:str) -> Optional[Skill]:
        for s in self.skills:
            if s.name == name:
                return s
        return None

    def match(self,question:str) -> Optional[Skill]:
        """关键词打分：命中关键词最多的skill胜出：无命中返回None"""
        best:Optional[Skill] = None
        best_score = 0
        for skill in self.skills:
            score = sum(1 for kw in skill.trigger_keywords if kw in question)
            if score > best_score:
                best,best_score = skill,score
        return best if best_score > 0 else None

    def list_all(self) -> List[Skill]:
        return list(self.skills)


