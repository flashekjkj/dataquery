from __future__ import annotations
import json
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any,Dict,List,Optional,Callable

# Turn类型定义
Turn = Dict[str,Any]

class ConversationMemory:
    """
    短期会话记忆：单会话多轮记录+滚动摘要+JSON文件落盘
    存储路径：data/conversations/{thread_id}.json
    """
    def __init__(self,data_root:Path):
        self.data_root = data_root
        self.conv_dir = self.data_root / "conversations"
        self.conv_dir.mkdir(parents=True,exist_ok=True)

    def _get_file_path(self,thread_id:str) -> Path:
        return self.conv_dir / f"{thread_id}.json"

    def load(self,thread_id:str) -> Dict[str,Any]:
        """
        加载会话，不存在则返回空会话结构，
        返回结构：{"turns":List[Turn],"summary":str}
        """
        fp = self._get_file_path(thread_id)
        if not fp.exists():
            return {"turns": [],"summary":""}
        try:
            with fp.open("r",encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            warnings.warn(f"加载会话{thread_id}失败，将使用空会话:{e}")
            return {"turns":[],"summary":""}

    def append_turn(self,thread_id:str,turn:Turn) -> None:
        """
        追加一轮对话并立刻落盘，
        turn字段：role,content,sql,answer,timestamp
        """
        data = self.load(thread_id)
        # 补时间戳
        if "timestamp" not in turn:
            turn["timestamp"] = datetime.utcnow().isoformat()
        data["turns"].append(turn)
        self._save(thread_id,data)

    def _save(self,thread_id:str,data:Dict[str,Any]) -> None:
        fp = self._get_file_path(thread_id)
        try:
            with fp.open("w",encoding="utf-8") as f:
                json.dump(data,f,ensure_ascii=False,indent=2)
        except Exception as e:
            warnings.warn(f"保存会话 {thread_id} 失败：{e}")

    def recent_context(self,thread_id:str,n:int = 4) -> str:
        """
        读取会话，拼接成喂给指代消解prompt的文本
        - 如果有summary，文本最前面放summary
        - 后面拼接最近n轮对话
        返回字符串，格式：
        【会话摘要】: xxx
        用户：xxx
        助手：xxx（SQL：xxx）        
        """
        data = self.load(thread_id)
        summary = data.get("summary","").strip()
        turns = data["turns"][-n:]
        lines = []
        if summary:
            lines.append(f"【会话摘要】: {summary}")
        for turn in turns:
            role = turn["role"]
            content = turn.get("content") or ""
            sql = turn.get("sql")
            # assistant记录约定存answer字段，兜底兼容存content的写法
            answer = turn.get("answer") or content
            if role == "user":
                lines.append(f"用户：{content}")
            else:
                sql_part = f"（SQL：{sql}）" if sql else ""
                lines.append(f"助手：{answer}{sql_part}")
        return "\n".join(lines)

    def maybe_summarize(
            self,
            thread_id:str,
            llm_func:Callable[[str,str],str],
            keep:int = 4,
            threshold:int = 8,
            summary_system_prompt:str = "",
            summary_user_tpl:str = "",
    ) -> None:
        """
        滚动摘要逻辑：
        当总轮数 > threshold：
            1. 取出最老的 (总轮数 - keep) 轮交给LLM生成摘要
            2. 更新summary字段
            3. 截断turns，只保留最近keep轮
        摘要失败时降级：直接截断，不更新summary，不抛出异常        
        """
        data = self.load(thread_id)
        turns = data["turns"]
        total = len(turns)
        if total <= threshold:
            return

        # 需要压缩的旧轮次
        old_turns = turns[:-keep]
        new_turns = turns[-keep:]

        # 把旧turns拼成文本
        old_text = []
        for t in old_turns:
            r = t["role"]
            c = t.get("content") or ""
            s = t.get("sql")
            a = t.get("answer") or c
            if r == "user":
                old_text.append(f"用户：{c}")
            else:
                sql_part = f" SQL:{s}" if s else ""
                old_text.append(f"助手：{a}{sql_part}")
        old_text_str = "\n".join(old_text)

        try:
            user_prompt = summary_user_tpl.format(history=old_text_str)
            summary = llm_func(summary_system_prompt, user_prompt).strip()
            data["summary"] = summary
        except Exception as e:
            warnings.warn(f"会话摘要LLM调用失败，降级直接截断: {e}")
            # 摘要失败，保留原有summary，不覆盖
            pass            
        data["turns"] = new_turns
        self._save(thread_id, data)

    def clear(self, thread_id: str) -> None:
        """删除该会话文件"""
        fp = self._get_file_path(thread_id)
        if fp.exists():
            fp.unlink()