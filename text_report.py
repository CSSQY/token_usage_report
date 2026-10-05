"""文本报告：模板渲染、占位符构造与 LLM 风格化转述。"""

from typing import Any, Dict, List, Optional

import logging

from .config_model import TokenUsageReportConfig
from .metrics import (
    WINDOW_ORDER,
    ReportMetrics,
    WindowMetrics,
    format_cost_zh,
    format_duration_zh,
    format_number_zh,
    format_per_100,
    format_per_hour,
    format_response_zh,
    format_tokens_per_hour,
)

logger = logging.getLogger(__name__)

_UNAVAILABLE_TEXT = "数据不可用"
_HOST_NOT_PROVIDED = "宿主不提供（仅会话维度可用）"


def build_placeholders(metrics: ReportMetrics, config: TokenUsageReportConfig) -> Dict[str, str]:
    """构造文本模板所需的全部占位符。

    Args:
        metrics: 指标快照。
        config: 插件配置。

    Returns:
        Dict[str, str]: 占位符名到文本的映射。
    """

    unit_name = metrics.unit_name
    placeholders: Dict[str, str] = {
        "date": metrics.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "unit_name": unit_name,
        "scope": metrics.scope,
        "scope_name": metrics.scope_name,
        "total_scope": metrics.total_scope_note or "最近 365 天",
    }

    for key in WINDOW_ORDER:
        window = metrics.windows.get(key)
        if window is None:
            continue
        placeholders[key] = f"{format_number_zh(window.tokens)} {unit_name}"
        placeholders[f"{key}_raw"] = str(window.tokens)
        placeholders[f"{key}_requests"] = format_number_zh(window.requests)
        placeholders[f"{key}_cost"] = format_cost_zh(window.cost)

    total = metrics.total
    if total is not None:
        placeholders.update(
            {
                "total_tokens": f"{format_number_zh(total.tokens)} {unit_name}",
                "total_raw": str(total.tokens),
                "total_prompt": format_number_zh(total.prompt_tokens),
                "total_completion": format_number_zh(total.completion_tokens),
                "total_requests": format_number_zh(total.requests),
                "total_cost": format_cost_zh(total.cost),
                "total_avg_response": format_response_zh(total.avg_response),
            }
        )
    else:
        placeholders.update(
            {
                "total_tokens": _UNAVAILABLE_TEXT,
                "total_raw": "0",
                "total_prompt": _UNAVAILABLE_TEXT,
                "total_completion": _UNAVAILABLE_TEXT,
                "total_requests": _UNAVAILABLE_TEXT,
                "total_cost": _UNAVAILABLE_TEXT,
                "total_avg_response": _UNAVAILABLE_TEXT,
            }
        )

    total_cost = total.cost if total is not None else None
    placeholders.update(
        {
            "messages": format_number_zh(metrics.messages) if metrics.messages is not None else _UNAVAILABLE_TEXT,
            "replies": format_number_zh(metrics.replies) if metrics.replies is not None else _UNAVAILABLE_TEXT,
            "received_messages": (
                format_number_zh(metrics.received_messages)
                if metrics.received_messages is not None
                else _UNAVAILABLE_TEXT
            ),
            "online_duration": (
                format_duration_zh(metrics.online_hours * 3600)
                if metrics.online_hours is not None
                else _UNAVAILABLE_TEXT
            ),
            "cost_per_100_messages": format_per_100(total_cost, metrics.messages),
            "cost_per_100_received": format_per_100(total_cost, metrics.received_messages),
            "cost_per_100_replies": format_per_100(total_cost, metrics.replies),
            "cost_per_hour": format_per_hour(total_cost, metrics.online_hours),
            "tokens_per_hour": format_tokens_per_hour(
                total.tokens if total is not None else None,
                metrics.online_hours,
                unit_name,
            ),
        }
    )

    cache_rate = total.cache_hit_rate if total is not None else None
    placeholders.update(
        {
            "cache_hit_rate": (
                f"{cache_rate * 100:.2f}%" if cache_rate is not None else _HOST_NOT_PROVIDED
            ),
            "cache_hit_tokens": (
                format_number_zh(total.cache_hit_tokens)
                if total is not None and total.cache_hit_tokens is not None
                else _HOST_NOT_PROVIDED
            ),
            "cache_miss_tokens": (
                format_number_zh(total.cache_miss_tokens)
                if total is not None and total.cache_miss_tokens is not None
                else _HOST_NOT_PROVIDED
            ),
            "model_ranking": build_model_ranking_text(metrics),
            "module_breakdown": build_module_breakdown_text(metrics, config),
            "chat_message_share": build_chat_share_text(metrics),
            "details": build_details_text(metrics, config),
        }
    )

    if metrics.unavailable:
        placeholders["unavailable"] = "、".join(sorted(set(metrics.unavailable)))
    else:
        placeholders["unavailable"] = "无"
    placeholders["notes"] = "\n".join(f"· {note}" for note in metrics.notes)
    return placeholders


def build_model_ranking_text(metrics: ReportMetrics) -> str:
    """构造模型排行文本区块。"""

    if not metrics.models:
        return ""
    lines = ["模型排行（按 Token 排序）："]
    for index, row in enumerate(metrics.models, start=1):
        avg_text = format_response_zh(row.avg_response) if row.avg_response is not None else "N/A"
        lines.append(
            f"{index}. {row.name}｜{format_number_zh(row.tokens)} {metrics.unit_name}"
            f"｜{format_number_zh(row.requests)} 次｜{format_cost_zh(row.cost)}｜平均 {avg_text}"
        )
    return "\n".join(lines)


def build_module_breakdown_text(metrics: ReportMetrics, config: TokenUsageReportConfig) -> str:
    """构造模块占比文本区块。"""

    slices = metrics.pies.get("module_tokens") or []
    if not slices:
        return ""
    total_value = sum(item.value for item in slices) or 1.0
    items = " · ".join(f"{item.name} {item.value / total_value * 100:.1f}%" for item in slices)
    cost_slices = metrics.pies.get("module_cost") or []
    cost_total = sum(item.value for item in cost_slices)
    cost_text = ""
    if cost_slices and cost_total > 0:
        cost_items = " · ".join(
            f"{item.name} {item.value / cost_total * 100:.1f}%" for item in cost_slices
        )
        cost_text = f"\n模块花费占比：{cost_items}"
    return f"模块 Token 占比：{items}{cost_text}"


def build_chat_share_text(metrics: ReportMetrics) -> str:
    """构造聊天消息分布文本区块。"""

    slices = metrics.pies.get("chat_messages") or []
    if not slices:
        return ""
    total_value = sum(item.value for item in slices) or 1.0
    items = " · ".join(
        f"{item.name} {item.value / total_value * 100:.1f}%（{format_number_zh(item.value)} 条）"
        for item in slices[:10]
    )
    return f"聊天消息分布（前 10）：{items}"


def build_details_text(metrics: ReportMetrics, config: TokenUsageReportConfig) -> str:
    """构造详细数据表文本区块（受 ``render.show_details`` 控制）。"""

    if not config.render.show_details or not metrics.models:
        return ""
    lines = [
        "详细数据（模型明细）：",
        "名称｜调用次数｜Token｜费用｜平均耗时｜Token 占比",
    ]
    token_total = sum(row.tokens for row in metrics.models) or 1
    for row in metrics.models:
        avg_text = format_response_zh(row.avg_response) if row.avg_response is not None else "N/A"
        lines.append(
            f"{row.name}｜{row.requests}｜{row.tokens}｜{row.cost:.4f}｜{avg_text}"
            f"｜{row.tokens / token_total * 100:.1f}%"
        )
    return "\n".join(lines)


def build_data_block(metrics: ReportMetrics, config: TokenUsageReportConfig) -> str:
    """构造给 LLM 阅读的纯数据块（不含模板排版）。"""

    placeholders = build_placeholders(metrics, config)
    lines: List[str] = [
        f"统计范围：{placeholders['scope_name']}",
        f"生成时间：{placeholders['date']}",
        f"Token 单位名称：{metrics.unit_name}",
    ]
    for key in WINDOW_ORDER:
        window = metrics.windows.get(key)
        if window is None:
            continue
        lines.append(
            f"{window.label}：{format_number_zh(window.tokens)} {metrics.unit_name}"
            f"（输入 {format_number_zh(window.prompt_tokens)} / 输出 {format_number_zh(window.completion_tokens)}）"
            f"，请求 {window.requests} 次，花费 {format_cost_zh(window.cost)}"
        )
    total = metrics.total
    if total is not None:
        lines.append(
            f"总计（{metrics.total_scope_note or '最近 365 天'}）：{format_number_zh(total.tokens)} {metrics.unit_name}"
            f"，输入 {format_number_zh(total.prompt_tokens)} / 输出 {format_number_zh(total.completion_tokens)}"
            f"，请求 {format_number_zh(total.requests)} 次，花费 {format_cost_zh(total.cost)}"
        )
        if total.avg_response is not None:
            lines.append(f"平均响应：{format_response_zh(total.avg_response)}")
    lines.append(
        f"消息数：{placeholders['messages']}，回复数：{placeholders['replies']}，"
        f"接收消息数：{placeholders['received_messages']}，在线时长：{placeholders['online_duration']}"
    )
    lines.append(
        f"缓存命中率：{placeholders['cache_hit_rate']}，缓存命中 Token：{placeholders['cache_hit_tokens']}，"
        f"缓存未命中 Token：{placeholders['cache_miss_tokens']}"
    )
    for block_key in ("model_ranking", "module_breakdown", "chat_message_share"):
        block_text = placeholders.get(block_key, "")
        if block_text:
            lines.append(block_text)
    if metrics.unavailable:
        lines.append(f"以下数据本次不可用：{placeholders['unavailable']}")
    return "\n".join(lines)


class _SafeFormatDict(dict):
    """缺失占位符时返回空串并记录一次警告的格式化字典。"""

    def __missing__(self, key: str) -> str:
        logger.warning("[token_usage_report] 文本模板使用了未知占位符：{%s}", key)
        return ""


def _tidy_text(text: str) -> str:
    """压缩多余空行并去除首尾空白。

    仅用部分占位符时，未被使用的区块占位符会渲染成空串并留下空行，
    这里把连续空行压缩为最多一个，让自定义模板的排版更干净。
    """

    tidied: List[str] = []
    for line in text.splitlines():
        stripped_line = line.rstrip()
        if not stripped_line:
            if not tidied or not tidied[-1]:
                continue
            tidied.append("")
            continue
        tidied.append(stripped_line)
    return "\n".join(tidied).strip()


def render_text_report(metrics: ReportMetrics, config: TokenUsageReportConfig) -> str:
    """按模板渲染文本报告。

    Args:
        metrics: 指标快照。
        config: 插件配置。

    Returns:
        str: 渲染后的文本；模板语法错误时回退为内置默认模板。
    """

    placeholders = build_placeholders(metrics, config)
    template = config.report.template
    try:
        return _tidy_text(template.format_map(_SafeFormatDict(placeholders)))
    except Exception as exc:
        logger.error("[token_usage_report] 文本模板渲染失败，已回退默认模板: %s", exc)
        default_template = type(config.report).model_fields["template"].default
        return _tidy_text(str(default_template).format_map(_SafeFormatDict(placeholders)))


async def read_persona_prompt(ctx: Any, config: TokenUsageReportConfig) -> str:
    """读取宿主人格配置并按模板拼装为提示词前缀。

    读取的是宿主全局配置（``config.get``）：``bot.nickname``、
    ``personality.personality``、``personality.reply_style``。
    若配置了 ``report.persona_template``，则用它渲染，支持占位符
    ``{bot_name}`` / ``{personality}`` / ``{reply_style}``；留空时使用内置拼装。
    ``report.persona_extra`` 会追加在最末尾。

    Args:
        ctx: 插件运行时上下文。
        config: 插件配置。

    Returns:
        str: 人格提示词前缀；未启用或全部为空时返回空串。
    """

    persona_extra = str(config.report.persona_extra or "").strip()
    if not config.report.use_persona:
        return f"{persona_extra}\n" if persona_extra else ""

    persona_values = {"bot_name": "", "personality": "", "reply_style": ""}
    persona_fields = (
        ("bot.nickname", "bot_name"),
        ("personality.personality", "personality"),
        ("personality.reply_style", "reply_style"),
    )
    for config_key, value_key in persona_fields:
        try:
            value = await ctx.config.get(config_key, "")
        except Exception as exc:
            logger.warning("[token_usage_report] 读取宿主人格字段 %s 失败：%s", config_key, exc)
            continue
        persona_values[value_key] = str(value or "").strip()

    template = str(config.report.persona_template or "").strip()
    if template:
        try:
            persona_text = template.format_map(_SafeFormatDict(persona_values)).strip()
        except Exception as exc:
            logger.error("[token_usage_report] 人格模板渲染失败，已回退内置拼装：%s", exc)
            persona_text = _build_persona_text(persona_values)
    else:
        persona_text = _build_persona_text(persona_values)

    parts = [part for part in (persona_text, persona_extra) if part]
    if not parts:
        logger.info("[token_usage_report] 未读取到可用人格配置，本次按默认提示词生成")
        return ""
    return "\n".join(parts) + "\n"


def _build_persona_text(persona_values: Dict[str, str]) -> str:
    """内置人格拼装：跳过为空的字段。"""

    lines: List[str] = []
    if persona_values.get("bot_name"):
        lines.append(f"你的名字是：{persona_values['bot_name']}")
    if persona_values.get("personality"):
        lines.append(f"你的人格设定是：{persona_values['personality']}")
    if persona_values.get("reply_style"):
        lines.append(f"你的表达风格是：{persona_values['reply_style']}")
    return "。".join(lines) + "。" if lines else ""


async def render_llm_report(
    ctx: Any,
    metrics: ReportMetrics,
    config: TokenUsageReportConfig,
) -> str:
    """调用宿主模型组对统计结果做风格化转述；失败时回退为模板文本。

    提示词结构：``宿主人格`` + ``report.llm_prompt`` + ``统计数据``。

    Args:
        ctx: 插件运行时上下文。
        metrics: 指标快照。
        config: 插件配置。

    Returns:
        str: LLM 转述文本或模板文本。
    """

    persona_prefix = await read_persona_prompt(ctx, config)
    prompt = f"{persona_prefix}{config.report.llm_prompt}\n\n{build_data_block(metrics, config)}"
    try:
        result = await ctx.llm.generate(prompt=prompt, task_name=config.report.llm_task_name)
    except Exception as exc:
        logger.error("[token_usage_report] LLM 转述调用失败，已回退模板文本: %s", exc)
        return render_text_report(metrics, config)

    response: Optional[str] = None
    if isinstance(result, dict):
        if result.get("success") and result.get("response"):
            response = str(result["response"]).strip()
        elif not result.get("success"):
            logger.error("[token_usage_report] LLM 转述返回失败，已回退模板文本: %s", result.get("error"))
    if not response:
        logger.error("[token_usage_report] LLM 转述未返回有效文本，已回退模板文本")
        return render_text_report(metrics, config)
    return response


async def build_report_text(ctx: Any, metrics: ReportMetrics, config: TokenUsageReportConfig) -> str:
    """按配置选择模板或 LLM 转述，生成最终文本报告。

    Args:
        ctx: 插件运行时上下文。
        metrics: 指标快照。
        config: 插件配置。

    Returns:
        str: 最终文本报告。
    """

    if config.report.mode == "llm":
        return await render_llm_report(ctx, metrics, config)
    return render_text_report(metrics, config)


def build_short_window_text(metrics: ReportMetrics, window_key: str) -> str:
    """为工具的单窗口查询构造简短文本。

    Args:
        metrics: 指标快照。
        window_key: 窗口 key，或 ``all`` 表示输出全部窗口。

    Returns:
        str: 简短统计文本。
    """

    lines: List[str] = [f"统计范围：{metrics.scope_name}"]
    if window_key == "all":
        targets = [metrics.windows.get(key) for key in WINDOW_ORDER]
        total = metrics.total
        if total is not None:
            targets.append(total)
    else:
        window = metrics.windows.get(window_key)
        if window is None:
            return f"不支持的时间范围：{window_key}"
        targets = [window]
    for window in targets:
        if window is None:
            continue
        lines.append(
            f"{window.label}：{format_number_zh(window.tokens)} {metrics.unit_name}"
            f"，请求 {window.requests} 次，花费 {format_cost_zh(window.cost)}"
        )
    return "\n".join(lines)


def build_window_summary(metrics: ReportMetrics, window_key: str) -> str:
    """生成单窗口的一句话摘要（用于错误提示与日志）。"""

    window: Optional[WindowMetrics] = metrics.windows.get(window_key)
    if window is None:
        return _UNAVAILABLE_TEXT
    return f"{window.label}：{format_number_zh(window.tokens)} {metrics.unit_name}"