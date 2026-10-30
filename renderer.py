"""图片渲染管线：字体服务组轮换、两轮超时、格式转换与失败回退。"""

from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import base64
import logging
import time

from .config_model import TokenUsageReportConfig
from .metrics import ReportMetrics
from .templates import (
    FontService,
    build_html,
    parse_font_services,
    resolve_theme,
)

logger = logging.getLogger(__name__)

_TOTAL_RENDER_BUDGET_MS = 45000
_SYSTEM_FALLBACK_RESERVE_MS = 15000
_RENDER_SELECTOR = "#report-card"


class RenderFailed(RuntimeError):
    """渲染整体失败时抛出的异常。"""


async def render_report_image(ctx: Any, metrics: ReportMetrics, config: TokenUsageReportConfig) -> str:
    """把报告渲染为图片（base64）。

    渲染顺序：启用外部字体且允许联网时，先按字体服务优先级做两轮尝试；
    外部字体全部不可用（或未启用外部字体）后，再用系统字体渲染一次作为兜底。
    总耗时受 ``_TOTAL_RENDER_BUDGET_MS`` 限制，其中为系统字体回退预留
    ``_SYSTEM_FALLBACK_RESERVE_MS``。

    Args:
        ctx: 插件运行时上下文。
        metrics: 指标快照。
        config: 插件配置。

    Returns:
        str: 图片 base64（PNG 或 JPEG）。

    Raises:
        RenderFailed: 外部字体与系统字体回退均失败时抛出。
    """

    started = time.monotonic()
    last_error = ""

    if config.render.use_external_fonts and config.render.allow_network:
        services = parse_font_services(config.render.font_services)
        if services:
            deadline = started + (_TOTAL_RENDER_BUDGET_MS - _SYSTEM_FALLBACK_RESERVE_MS) / 1000
            image_base64, last_error = await _render_external_rounds(
                ctx,
                metrics=metrics,
                config=config,
                services=services,
                deadline=deadline,
            )
            if image_base64:
                return image_base64
        else:
            logger.warning(
                "[token_usage_report] 字体服务组为空或全部非法，跳过外部字体，直接使用系统字体渲染"
            )
    elif config.render.use_external_fonts:
        logger.info("[token_usage_report] allow_network=false，外部字体无法加载，直接使用系统字体渲染")

    remaining_ms = max(
        _SYSTEM_FALLBACK_RESERVE_MS,
        _TOTAL_RENDER_BUDGET_MS - int((time.monotonic() - started) * 1000),
    )
    image_base64, system_error = await _render_once(
        ctx,
        metrics=metrics,
        config=config,
        service=None,
        round_index=3,
        timeout_ms=min(config.render.second_round_timeout_ms, remaining_ms),
        viewport_width=config.render.fallback_viewport_width,
        viewport_height=config.render.fallback_viewport_height,
    )
    if image_base64:
        return image_base64
    raise RenderFailed(
        "渲染失败（外部字体与系统字体回退均失败）："
        f"{system_error or last_error or '未执行任何渲染尝试'}"
    )


async def _render_external_rounds(
    ctx: Any,
    *,
    metrics: ReportMetrics,
    config: TokenUsageReportConfig,
    services: List[FontService],
    deadline: float,
) -> Tuple[Optional[str], str]:
    """按字体服务优先级执行两轮外部字体渲染。

    Args:
        ctx: 插件运行时上下文。
        metrics: 指标快照。
        config: 插件配置。
        services: 可用的外部字体服务组。
        deadline: 外部字体尝试的截止时间（``time.monotonic()`` 秒）。

    Returns:
        Tuple[Optional[str], str]: ``(图片 base64 或 None, 最后一次错误)``。
    """

    render_config = config.render
    rounds: List[Tuple[int, int, int]] = [
        (render_config.first_round_timeout_ms, render_config.viewport_width, render_config.viewport_height),
        (
            render_config.second_round_timeout_ms,
            render_config.fallback_viewport_width,
            render_config.fallback_viewport_height,
        ),
    ]
    last_error = "未执行任何渲染尝试"

    for round_index, (timeout_ms, viewport_width, viewport_height) in enumerate(rounds, start=1):
        for service in services:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms < 1000:
                return None, f"外部字体渲染预算已耗尽，最后一次错误：{last_error}"
            image_base64, error = await _render_once(
                ctx,
                metrics=metrics,
                config=config,
                service=service,
                round_index=round_index,
                timeout_ms=min(timeout_ms, remaining_ms),
                viewport_width=viewport_width,
                viewport_height=viewport_height,
            )
            if image_base64:
                return image_base64, ""
            last_error = error

    return None, last_error


async def _render_once(
    ctx: Any,
    *,
    metrics: ReportMetrics,
    config: TokenUsageReportConfig,
    service: Optional[FontService],
    round_index: int,
    timeout_ms: int,
    viewport_width: int,
    viewport_height: int,
) -> Tuple[Optional[str], str]:
    """执行一次渲染尝试并转换图片格式。

    Args:
        ctx: 插件运行时上下文。
        metrics: 指标快照。
        config: 插件配置。
        service: 本次使用的字体服务组；为 ``None`` 时表示系统字体回退（不联网）。
        round_index: 当前轮次（从 1 开始）。
        timeout_ms: 本轮渲染超时。
        viewport_width: 本轮视口宽度。
        viewport_height: 本轮视口高度。

    Returns:
        Tuple[Optional[str], str]: ``(图片 base64 或 None, 错误描述)``。
    """

    service_label = service.name if service is not None else "系统字体（回退）"
    html = build_html(metrics, config, service)
    render_kwargs: Dict[str, Any] = {
        "selector": _RENDER_SELECTOR,
        "viewport": {"width": viewport_width, "height": viewport_height},
        "device_scale_factor": config.render.resolution_scale,
        "full_page": False,
        "wait_until": "load",
        "allow_network": config.render.allow_network if service is not None else False,
    }
    try:
        result = await ctx.render.html2png(html, render_timeout_ms=timeout_ms, **render_kwargs)
    except TypeError as exc:
        # 新版 SDK 用 render_timeout_ms 表示渲染业务超时，旧版用 timeout_ms，按参数名兼容
        if "render_timeout_ms" not in str(exc):
            logger.warning("[token_usage_report] 渲染失败（第%d轮 / 字体服务 %s）：%s", round_index, service_label, exc)
            return None, str(exc)
        logger.info("[token_usage_report] 当前插件 SDK 使用旧参数名 timeout_ms，已自动按旧版重试")
        try:
            result = await ctx.render.html2png(html, timeout_ms=timeout_ms, **render_kwargs)
        except Exception as inner_exc:
            logger.warning(
                "[token_usage_report] 渲染失败（第%d轮 / 字体服务 %s）：%s", round_index, service_label, inner_exc
            )
            return None, str(inner_exc)
    except Exception as exc:
        logger.warning("[token_usage_report] 渲染失败（第%d轮 / 字体服务 %s）：%s", round_index, service_label, exc)
        return None, str(exc)

    image_base64 = _extract_image_base64(result)
    if not image_base64:
        logger.warning("[token_usage_report] 渲染返回缺少 image_base64（第%d轮 / 字体服务 %s）", round_index, service_label)
        return None, "渲染返回缺少 image_base64"

    try:
        converted = _convert_image_format(image_base64, config)
    except Exception as exc:
        logger.error("[token_usage_report] 图片格式转换失败（目标格式 %s）：%s", config.render.image_format, exc)
        return None, f"图片格式转换失败：{exc}"

    render_ms = result.get("render_ms") if isinstance(result, dict) else None
    resolved_theme, resolved_name = resolve_theme(config)
    del resolved_theme
    logger.info(
        "[token_usage_report] 报告图片渲染成功：模板=%s（配置值 %s），字体服务=%s，第%d轮，格式=%s，耗时=%s",
        resolved_name,
        config.render.template_name,
        service_label,
        round_index,
        config.render.image_format,
        f"{render_ms}ms" if render_ms is not None else "未知",
    )
    return converted, ""


def _extract_image_base64(payload: Any) -> str:
    """从渲染返回值中提取图片 base64。"""

    if isinstance(payload, dict):
        image_base64 = str(payload.get("image_base64") or "").strip()
        if image_base64:
            return image_base64
        inner = payload.get("result")
        if isinstance(inner, dict):
            return str(inner.get("image_base64") or "").strip()
    return ""


def _convert_image_format(image_base64: str, config: TokenUsageReportConfig) -> str:
    """按配置转换图片格式（PNG 原样返回，JPEG 使用 Pillow 转码）。

    Args:
        image_base64: 宿主返回的 PNG base64。
        config: 插件配置。

    Returns:
        str: 转换后的图片 base64。

    Raises:
        ValueError: base64 无法解码或图片无法处理时抛出。
        ImportError: 环境中缺少 Pillow 时抛出。
    """

    if config.render.image_format != "jpeg":
        return image_base64

    from PIL import Image

    try:
        png_bytes = base64.b64decode(image_base64)
    except Exception as exc:
        raise ValueError(f"图片 base64 解码失败：{exc}") from exc

    buffer = BytesIO()
    with Image.open(BytesIO(png_bytes)) as image:
        image.convert("RGB").save(buffer, format="JPEG", quality=int(config.render.jpeg_quality))
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


async def build_report_image(
    ctx: Any,
    metrics: ReportMetrics,
    config: TokenUsageReportConfig,
) -> Tuple[Optional[str], Optional[str]]:
    """渲染报告图片并返回 ``(图片 base64, 失败原因)``；本函数不会抛出异常。

    调用方在图片渲染失败时应记录一次 error 日志并回退为文字版本。

    Args:
        ctx: 插件运行时上下文。
        metrics: 指标快照。
        config: 插件配置。

    Returns:
        Tuple[Optional[str], Optional[str]]: 成功时为 ``(base64, None)``，失败时为 ``(None, 原因)``。
    """

    try:
        image_base64 = await render_report_image(ctx, metrics, config)
    except RenderFailed as exc:
        return None, str(exc)
    if not image_base64:
        return None, "渲染未返回图片数据"
    return image_base64, None


def describe_render_device(config: TokenUsageReportConfig) -> Dict[str, Any]:
    """返回本次渲染的关键参数说明（用于日志与排查）。"""

    return {
        "template": config.render.template_name,
        "format": config.render.image_format,
        "quality": config.render.jpeg_quality,
        "scale": config.render.resolution_scale,
        "rounds": [config.render.first_round_timeout_ms, config.render.second_round_timeout_ms],
        "budget_ms": _TOTAL_RENDER_BUDGET_MS,
        "external_fonts": config.render.use_external_fonts,
    }