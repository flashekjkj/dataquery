from __future__ import annotations
import random
import warnings
from typing import List,Optional
from app.schemas import LLMResponse,EmbeddingResponse,RerankItem,RerankResponse

from openai import OpenAI
from app.config import settings
import json

class ModelClient:
    def __init__(self,mock:bool=True):
        """
        模型统一客户端
        :param mock:True=模拟端口；False=真实API
        """
        self.mock = mock
        self._client = None
        self._reranker = None   # 本地CrossEncoder精排模型，首次调用时懒加载
        if not self.mock:
            self._client = OpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIMEOUT
            )

    def llm_invoke(self,prompt:str,system_prompt:Optional[str]=None) -> LLMResponse:
        """LLM调用入口"""
        if self.mock:
            mock_answer = f"【Mock LLM】用户提问：{prompt}\n模拟SQL：SELECT * FROM customers LIMIT 10;"
            return LLMResponse(
                content=mock_answer,
                usage={"input_tokens":100,"output_tokens":50}
            )
        else:
            messages = []
            if system_prompt:
                messages.append({"role":"system","content":system_prompt})
            messages.append({"role":"user","content":prompt})

            resp = self._client.chat.completions.create(
                model=settings.LLM_MODEL_ID,
                messages=messages
                )

            return LLMResponse(
                content=resp.choices[0].message.content,
                usage={
                    "input_tokens":resp.usage.prompt_tokens,
                    "output_tokens":resp.usage.completion_tokens
                }
            )

    def embedding(self,text:str) -> EmbeddingResponse:
        """文本转向量"""
        if self.mock:
            mock_vector = [random.random() for _ in range(1024)]
            return EmbeddingResponse(vector=mock_vector)
        else:
            resp = self._client.embeddings.create(
                model=settings.LLM_EMBEDDING_MODEL,
                input=text
            )

            return EmbeddingResponse(
                vector=resp.data[0].embedding
            )

    def _get_reranker(self):
        """
        懒加载本地CrossEncoder精排模型
        torch/sentence-transformers的import和模型加载都很慢（数秒~十几秒），
        所以延迟到首次真正rerank时才加载，不影响服务启动速度
        """
        if self._reranker is None:
            from sentence_transformers import CrossEncoder
            print(f"⏳ 首次加载本地rerank模型：{settings.RERANK_MODEL_ID} ...")
            self._reranker = CrossEncoder(settings.RERANK_MODEL_ID, max_length=512)
            print("✅ rerank模型加载完成")
        return self._reranker

    def rerank(self,query:str,documents:List[str]) -> RerankResponse:
        """
        搜索结果重排
        真实模式：本地CrossEncoder（bge-reranker）对(query, doc)对做相关性打分，
        index保持原输入列表位置（调用方靠index回映射原始文档）
        加载/推理失败时降级为按原顺序返回，不阻塞检索主链路
        """
        if self.mock:
            rerank_items = []
            for idx,doc in enumerate(documents):
                score = 0.9 - idx * 0.05
                rerank_items.append(RerankItem(index=idx,text=doc,score=score))
            return RerankResponse(results=rerank_items)

        if not documents:
            return RerankResponse(results=[])

        try:
            model = self._get_reranker()
            pairs = [[query,doc] for doc in documents]
            scores = model.predict(pairs)
            rerank_items = [
                RerankItem(index=i,text=doc,score=float(s))
                for i,(doc,s) in enumerate(zip(documents,scores))
            ]
            # 按分数降序；index仍是原位置，排序只影响返回顺序
            rerank_items.sort(key=lambda x: x.score,reverse=True)
            return RerankResponse(results=rerank_items)
        except Exception as e:
            warnings.warn(f"本地rerank失败，降级为按原顺序返回：{e}")
            return RerankResponse(results=[
                RerankItem(index=i,text=doc,score=0.0)
                for i,doc in enumerate(documents)
            ])

    def chat_with_tools(self,prompt:str,system_prompt:Optional[str] = None,tools:Optional[List[dict]] = None) -> dict:
        """
        带工具调用的LLM入口（function calling）
        返回 {"content": str, "tool_calls": [{"name": str, "arguments": dict}]}
        mock模式：问题含"计算"时返回calculator工具调用，用于离线联调        
        """
        if self.mock:
            tool_calls = []
            if "计算" in prompt:
                tool_calls.append({
                    "name":"calculator__calculate",
                    "arguments":{"expression":prompt.split("计算")[-1].strip()},
                })
            return {"content": "", "tool_calls": tool_calls}
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        resp = self._client.chat.completions.create(
            model=settings.LLM_MODEL_ID,
            messages=messages,
            tools=tools or [],
            tool_choice="auto"
        )
        msg = resp.choices[0].message
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {"expression": tc.function.arguments}
            tool_calls.append({"name": tc.function.name, "arguments": args})
        return {"content": msg.content or "", "tool_calls": tool_calls}            

