from __future__ import annotations
import json
import warnings
from pathlib import Path
from typing import List,Dict,Optional,Any

class LongTermMemoryStore:
    """
    长期记忆：存储用户偏好事实陈述句，跨会话持久化
    存储文件：backend/data/user_memory.json
    只保存事实，不保存对话历史、一次性查询结果
    结构：{"users": {"default": {"facts": [{"fact": str, "created_at": str}], "updated_at": str}}}
    """
    def __init__(self,data_root:Path):  
        self.data_root = data_root
        self.memory_file = self.data_root / "user_memory.json"
        self.data_root.mkdir(parents=True,exist_ok=True)

    def _load_raw(self) -> Dict[str,Any]:
        """加载完整用户记忆文件，不存在返回初始空结构"""
        if not self.memory_file.exists():
            return {
                "users":{
                    "default":{
                        "facts":[],
                        "updated_at":""
                    }
                }
            }
        try:
            with self.memory_file.open("r",encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            warnings.warn(f"读取长期记忆文件失败，使用空记忆: {e}")
            return {
                "users": {
                    "default": {
                        "facts": [],
                        "updated_at": ""
                    }
                }
            }

    def _save_raw(self,data:Dict[str,Any]) -> None:
        """写入完整记忆文件，异常仅告警不阻断流程"""
        try:
            import datetime
            data["users"]["default"]["updated_at"] = datetime.datetime.utcnow().isoformat()
            with self.memory_file.open("w",encoding="utf-8") as f:
                json.dump(data,f,ensure_ascii=False,indent=2)
        except Exception as e:
            warnings.warn(f"保存长期记忆失败: {e}")

    def load(self,user_id:str = "default") -> str:
        """
        读取指定用户的全部事实，拼接成一段文本，直接注入prompt
        无事实时返回 "无用户偏好"
        """
        data = self._load_raw()
        user_section = data["users"].get(user_id,{"facts":[]})
        facts:List[Dict[str,str]] = user_section.get("facts",[])
        if not facts:
            return "无用户偏好"       

        lines = []
        for item in facts:
            fact_text = item.get("fact","").strip()
            if fact_text:
                lines.append(f"- {fact_text}")
        return "\n".join(lines)

    def add(self,user_id:str,fact:str) -> None:
        """
        添加一条事实；简单去重：
        新fact被已有fact包含 或者 新fact包含已有fact，则不新增，直接更新旧记录时间
        控制fact数量上限20条，超过仅日志提醒，不自动删除
        fact必须是陈述句，一次性查询结果不要存入
        """        
        fact = fact.strip()
        if not fact:
            return 
        data = self._load_raw()
        # user_id不存在时先初始化空结构，避免KeyError
        user_section = data["users"].setdefault(user_id, {"facts": [], "updated_at": ""})
        facts_list = user_section["facts"]

        exist_idx:Optional[int] = None
        for idx,existed in enumerate(facts_list):
            e_text = existed["fact"].strip()
            if fact in e_text or e_text in fact:
                exist_idx = idx
                break

        import datetime
        now = datetime.datetime.utcnow().isoformat()

        if exist_idx is not None:
            # 已有相似事实
            facts_list[exist_idx]["fact"] = fact
            facts_list[exist_idx]["created_at"] = now
        else:
            # 新增事实
            if len(facts_list) >= 20:
                warnings.warn(f"用户{user_id}长期事实已达到20条上限，不再新增")
                return
            facts_list.append({
                "fact": fact,
                "created_at": now
            })
        self._save_raw(data)

    def clear(self, user_id: str = "default") -> None:
        """清空指定用户的全部长期事实"""
        data = self._load_raw()
        if user_id in data["users"]:
            data["users"][user_id]["facts"] = []
        self._save_raw(data)                