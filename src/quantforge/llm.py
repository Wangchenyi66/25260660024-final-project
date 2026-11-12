"""LLM 客户端抽象：真实 API 与离线 Mock 双实现。

- 默认 MockLLM：确定性、可复现、零成本、零凭据——期末演示与测试均走此路径；
- OpenAICompatClient：设置环境变量 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 后启用。
  仓库与提交历史中绝不保存任何密钥（作业安全要求）。
"""
from __future__ import annotations

import json
import os
import urllib.request


class Budget:
    """LLM 调用预算（成本控制，超限即停止运行）。"""

    def __init__(self, max_llm_calls: int = 30, max_tokens: int = 30000):
        self.max_calls = max_llm_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self.tokens = 0

    def consume(self, prompt: str, completion: str) -> None:
        self.calls += 1
        self.tokens += (len(prompt) + len(completion)) // 4  # 粗略 token 估算
        if self.calls > self.max_calls or self.tokens > self.max_tokens:
            raise RuntimeError(
                f"LLM 预算超限：calls={self.calls}/{self.max_calls}, "
                f"tokens≈{self.tokens}/{self.max_tokens}")


class LLMClient:
    def __init__(self, budget: Budget | None = None):
        self.budget = budget or Budget()

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class OpenAICompatClient(LLMClient):
    def __init__(self, budget=None, api_key: str | None = None,
                 base_url: str | None = None, model: str | None = None):
        super().__init__(budget)
        self.api_key = api_key or os.environ["LLM_API_KEY"]
        self.base_url = (base_url or os.environ.get(
            "LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4o-mini")

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        out = data["choices"][0]["message"]["content"]
        self.budget.consume(system_prompt + user_prompt, out)
        return out


class MockLLM(LLMClient):
    """离线确定性"LLM"：按规则生成假设卡文本，供无凭据环境完整演示工作流。"""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.budget.consume(system_prompt, user_prompt)
        return user_prompt  # 调用方传入的 user_prompt 已含全部上下文，Mock 原样返回


def get_llm(budget: Budget | None = None) -> LLMClient:
    if os.environ.get("LLM_API_KEY"):
        return OpenAICompatClient(budget)
    return MockLLM(budget)
