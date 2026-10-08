"""LLM 风格化转述的模型调用：宿主模型组 或 自定义 [OI] Chat Completions 兼容服务。

两条路径的失败都统一抛 :class:`LlmCallError`，由调用方（文本报告层）回退为模板文本。
自定义服务支持地址 / 密钥 / 模型 / 温度 / 额外请求参数 / 重试次数 / 超时 / 重试间隔。
"""

from typing import Any, Dict, List, Optional

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_CHAT_COMPLETIONS_PATH = "/chat/completions"

_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429})
"""需要重试的 4xx：请求超时、冲突、过早、限流；其余 4xx（鉴权/参数错误）重试没有意义。"""

_ERROR_BODY_LIMIT = 300


class LlmCallError(RuntimeError):
    """LLM 转述调用失败（配置缺失、网络异常、服务返回错误等）。"""


class _HttpStatusError(Exception):
    """自定义服务返回非 2xx 状态码。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}：{detail}")
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        """4xx 中只有少数（超时/限流等）值得重试，5xx 一律重试。"""

        return self.status_code >= 500 or self.status_code in _RETRYABLE_STATUS_CODES


async def generate_rewrite(ctx: Any, config: Any, prompt: str) -> str:
    """按配置调用 LLM 生成转述文本。

    Args:
        ctx: 插件运行时上下文（宿主模型组路径需要）。
        config: 插件配置。
        prompt: 完整提示词（人格 + 任务说明 + 统计数据）。

    Returns:
        str: 模型返回的文本。

    Raises:
        LlmCallError: 配置缺失或调用失败时抛出。
    """

    rewrite_config = config.llm_rewrite
    provider = str(getattr(rewrite_config, "provider", "host") or "host").strip().lower()
    if provider == "custom":
        return await _generate_via_custom(rewrite_config, prompt)
    return await _generate_via_host(ctx, rewrite_config, prompt)


async def _generate_via_host(ctx: Any, rewrite_config: Any, prompt: str) -> str:
    """复用宿主模型组生成文本。"""

    task_name = str(getattr(rewrite_config, "llm_task_name", "") or "").strip()
    try:
        result = await ctx.llm.generate(prompt=prompt, task_name=task_name)
    except Exception as exc:
        raise LlmCallError(f"宿主模型组调用异常：{exc}") from exc

    if isinstance(result, dict):
        if result.get("success") and result.get("response"):
            return str(result["response"]).strip()
        if result.get("success") is False:
            raise LlmCallError(f"宿主模型组返回失败：{result.get('error') or '未给出原因'}")
    raise LlmCallError(f"宿主模型组返回结构异常：{type(result).__name__}")


async def _generate_via_custom(rewrite_config: Any, prompt: str) -> str:
    """调用自定义 [OI] Chat Completions 兼容服务生成文本（含重试）。"""

    base_url = str(getattr(rewrite_config, "api_base_url", "") or "").strip().rstrip("/")
    api_key = str(getattr(rewrite_config, "api_key", "") or "").strip()
    model = str(getattr(rewrite_config, "model", "") or "").strip()
    if not base_url:
        raise LlmCallError("未配置自定义服务地址（llm_rewrite.api_base_url）")
    if not model:
        raise LlmCallError("未配置自定义服务模型名（llm_rewrite.model）")

    url = f"{base_url}{_CHAT_COMPLETIONS_PATH}"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    temperature = getattr(rewrite_config, "temperature", None)
    if temperature is not None:
        payload["temperature"] = float(temperature)
    payload.update(_parse_extra_body(getattr(rewrite_config, "extra_body", "")))

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout_seconds = float(getattr(rewrite_config, "timeout_seconds", 60.0) or 60.0)
    retry_interval = max(float(getattr(rewrite_config, "retry_interval_seconds", 1.0) or 0.0), 0.0)
    attempts = max(int(getattr(rewrite_config, "max_retries", 0) or 0), 0) + 1

    try:
        import httpx
    except ImportError as exc:
        raise LlmCallError(f"缺少 httpx 依赖，无法调用自定义服务：{exc}") from exc

    last_error = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
        for attempt in range(1, attempts + 1):
            try:
                return await _post_once(client, url, payload, headers)
            except _HttpStatusError as exc:
                last_error = str(exc)
                if not exc.retryable:
                    raise LlmCallError(f"自定义服务返回错误：{exc}") from exc
            except Exception as exc:
                last_error = str(exc)
            if attempt < attempts:
                logger.warning(
                    "[token_usage_report] 自定义 LLM 调用失败（第 %d/%d 次）：%s；%s 秒后重试",
                    attempt,
                    attempts,
                    last_error,
                    retry_interval,
                )
                if retry_interval > 0:
                    await asyncio.sleep(retry_interval)

    raise LlmCallError(f"自定义 LLM 调用失败（共尝试 {attempts} 次）：{last_error}")


async def _post_once(client: Any, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> str:
    """发起一次请求并解析出文本内容。"""

    response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise _HttpStatusError(response.status_code, _summarize_body(response))
    try:
        data = response.json()
    except Exception as exc:
        raise LlmCallError(f"自定义服务返回的不是合法 JSON：{exc}") from exc

    content = _extract_content(data)
    if not content:
        raise LlmCallError("自定义服务返回内容为空或结构不符合 Chat Completions 约定")
    return content


def _summarize_body(response: Any) -> str:
    """截断错误响应体，避免日志被超长内容淹没。"""

    try:
        text = str(response.text or "").strip()
    except Exception:
        return "（无法读取响应体）"
    if not text:
        return "（响应体为空）"
    if len(text) <= _ERROR_BODY_LIMIT:
        return text
    return f"{text[:_ERROR_BODY_LIMIT]}…（已截断）"


def _extract_content(data: Any) -> str:
    """从 Chat Completions 响应中提取文本内容。

    兼容三种常见形态：``choices[0].message.content`` 为字符串、
    为内容片段数组（多模态/推理模型）、以及旧的 ``choices[0].text``。
    """

    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""

    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                else:
                    text = item
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            if parts:
                return "\n".join(parts)

    legacy_text = first.get("text")
    if isinstance(legacy_text, str) and legacy_text.strip():
        return legacy_text.strip()
    return ""


def _parse_extra_body(raw_extra_body: Any) -> Dict[str, Any]:
    """解析「额外请求参数」配置项（JSON 对象文本）。

    留空返回空字典；不是合法 JSON 对象时记一条 warning 并忽略，
    避免因为一个笔误让整次转述失败。

    Args:
        raw_extra_body: 配置里的 JSON 文本。

    Returns:
        Dict[str, Any]: 需要合并进请求体的额外参数。
    """

    text = str(raw_extra_body or "").strip()
    if not text:
        return {}
    try:
        parsed: Optional[Any] = json.loads(text)
    except Exception as exc:
        logger.warning("[token_usage_report] 额外请求参数不是合法 JSON，已忽略：%s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("[token_usage_report] 额外请求参数必须是 JSON 对象，已忽略：%s", type(parsed).__name__)
        return {}
    return parsed
