"""图片渲染管线：字体服务组轮换、两轮超时、格式转换与失败回退。"""

from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import base64
import logging
import time

from .config_model import TokenUsageReportConfig
from .metrics import ReportMetrics
from .templates import FontService, build_html, parse_font_services

logger = logging.getLogger(__name__)

_TOTAL_RENDER_BUDGET_MS = 45000
_RENDER_SELECTOR = "#report-card"


class RenderFailed(RuntimeError):
    """渲染整体失败时抛出的异常。"""


async def render_report_image(ctx: Any, metrics: ReportMetrics, config: TokenUsageReportConfig) -> str:
    """把报告渲染为图片（base64）。

    渲染顺序：第一轮（常规视口 / 超时）按字体服务优先级逐个尝试，
    全部失败后进入第二轮（兜底视口 / 超时）；总耗时受 ``_TOTAL_RENDER_BUDGET_MS`` 限制。

    Args:
        ctx: 插件运行时上下文。
        metrics: 指标快照。
        config: 插件配置。

    Returns:
        str: 图片 base64（PNG 或 JPEG）。

    Raises:
        RenderFailed: 全部尝试都失败时抛出。
    """

    services = parse_font_services(config.render.font_services)
    if not services:
        raise RenderFailed("字体服务组为空或全部非法，无法渲染（请检查 render.font_services 配置）")

    render_config = config.render
    rounds: List[Tuple[int, int, int]] = [
        (render_config.first_round_timeout_ms, render_config.viewport_width, render_config.viewport_height),
        (
            render_config.second_round_timeout_ms,
            render_config.fallback_viewport_width,
            render_config.fallback_viewport_height,
        ),
    ]
    deadline = time.monotonic() + _TOTAL_RENDER_BUDGET_MS / 1000
    last_error = "未执行任何渲染尝试"

    for round_index, (timeout_ms, viewport_width, viewport_height) in enumerate(rounds, start=1):
        for service in services:
            if time.monotonic() > deadline:
                raise RenderFailed(
                    f"渲染总预算（{_TOTAL_RENDER_BUDGET_MS}ms）已耗尽，最后一次错误：{last_error}"
                )
            image_base64, error = await _render_once(
                ctx,
                metrics=metrics,
                config=config,
                service=service,
                round_index=round_index,
                timeout_ms=timeout_ms,
                viewport_width=viewport_width,
                viewport_height=viewport_height,
            )
            if image_base64:
                return image_base64
            last_error = error

    raise RenderFailed(f"渲染失败（已尝试 {len(rounds)} 轮 × {len(services)} 个字体服务）：{last_error}")


async def _render_once(
    ctx: Any,
    *,
    metrics: ReportMetrics,
    config: TokenUsageReportConfig,
    service: FontService,
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
        service: 本次使用的字体服务组。
        round_index: 当前轮次（从 1 开始）。
        timeout_ms: 本轮渲染超时。
        viewport_width: 本轮视口宽度。
        viewport_height: 本轮视口高度。

    Returns:
        Tuple[Optional[str], str]: ``(图片 base64 或 None, 错误描述)``。
    """

    html = build_html(metrics, config, service)
    try:
        result = await ctx.render.html2png(
            html,
            selector=_RENDER_SELECTOR,
            viewport={"width": viewport_width, "height": viewport_height},
            device_scale_factor=config.render.resolution_scale,
            full_page=False,
            wait_until="load",
            allow_network=config.render.allow_network,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:
        logger.warning("[token_usage_report] 渲染失败（第%d轮 / 字体服务 %s）：%s", round_index, service.name, exc)
        return None, str(exc)

    image_base64 = _extract_image_base64(result)
    if not image_base64:
        logger.warning("[token_usage_report] 渲染返回缺少 image_base64（第%d轮 / 字体服务 %s）", round_index, service.name)
        return None, "渲染返回缺少 image_base64"

    try:
        converted = _convert_image_format(image_base64, config)
    except Exception as exc:
        logger.error("[token_usage_report] 图片格式转换失败（目标格式 %s）：%s", config.render.image_format, exc)
        return None, f"图片格式转换失败：{exc}"

    render_ms = result.get("render_ms") if isinstance(result, dict) else None
    logger.info(
        "[token_usage_report] 报告图片渲染成功：模板=%s，字体服务=%s，第%d轮，格式=%s，耗时=%s",
        config.render.template_name,
        service.name,
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
    }