"""报告投递：目标会话解析与发送。"""

from typing import Any, Dict, List, Optional, Tuple

import logging

from .config_model import TokenUsageReportConfig

logger = logging.getLogger(__name__)


async def resolve_target_streams(ctx: Any, config: TokenUsageReportConfig) -> Tuple[List[Tuple[str, str]], List[str]]:
    """解析定时播报的目标会话。

    Args:
        ctx: 插件运行时上下文。
        config: 插件配置。

    Returns:
        Tuple[List[Tuple[str, str]], List[str]]: ``(目标列表, 错误列表)``，
        目标列表元素为 ``(stream_id, 展示名)``。
    """

    targets: List[Tuple[str, str]] = []
    errors: List[str] = []
    platform = config.report.platform

    for raw_group_id in config.report.target_groups:
        group_id = str(raw_group_id or "").strip()
        if not group_id:
            continue
        stream_id = await _resolve_stream_id(
            ctx,
            platform=platform,
            chat_type="group",
            target_id=group_id,
            label=f"群 {group_id}",
            errors=errors,
        )
        if stream_id:
            targets.append((stream_id, f"群 {group_id}"))

    for raw_user_id in config.report.target_users:
        user_id = str(raw_user_id or "").strip()
        if not user_id:
            continue
        stream_id = await _resolve_stream_id(
            ctx,
            platform=platform,
            chat_type="private",
            target_id=user_id,
            label=f"用户 {user_id}",
            errors=errors,
        )
        if stream_id:
            targets.append((stream_id, f"用户 {user_id}"))

    return targets, errors


async def _resolve_stream_id(
    ctx: Any,
    *,
    platform: str,
    chat_type: str,
    target_id: str,
    label: str,
    errors: List[str],
) -> Optional[str]:
    """解析单个目标的聊天流 ID，找不到时尝试创建会话。"""

    try:
        if chat_type == "group":
            stream = await ctx.chat.get_stream_by_group_id(target_id, platform=platform)
        else:
            stream = await ctx.chat.get_stream_by_user_id(target_id, platform=platform)
        stream_id = _extract_stream_id(stream)
        if stream_id:
            return stream_id

        if chat_type == "group":
            opened = await ctx.chat.open_session(platform=platform, chat_type="group", group_id=target_id)
        else:
            opened = await ctx.chat.open_session(platform=platform, chat_type="private", user_id=target_id)
        stream_id = _extract_stream_id(opened)
        if stream_id:
            return stream_id
        errors.append(f"{label} 无法解析聊天流")
        logger.warning("[token_usage_report] %s 无法解析聊天流，已跳过", label)
    except Exception as exc:
        errors.append(f"{label} 解析失败：{exc}")
        logger.warning("[token_usage_report] %s 解析失败：%s", label, exc)
    return None


def _extract_stream_id(payload: Any) -> str:
    """从聊天流相关的返回值中提取 stream_id。"""

    if not isinstance(payload, dict):
        return ""
    for key in ("session_id", "stream_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    stream = payload.get("stream")
    if isinstance(stream, dict):
        for key in ("session_id", "stream_id"):
            value = str(stream.get(key) or "").strip()
            if value:
                return value
    return ""


def _extract_send_outcome(payload: Any) -> Tuple[bool, str]:
    """解析宿主发送能力的返回值，返回 ``(是否成功, 失败原因)``。

    宿主的 ``send.*`` 能力在发送失败时**不抛异常**，而是返回 ``{"success": False, "error": ...}``
    （见宿主 ``_build_send_result``），外层再由能力服务包一层 ``success=True`` 的传输信封。
    SDK 交给插件的是内层结果，因此必须读 ``success`` 字段：``bool(payload)`` 对失败字典
    同样为 True，会把发送失败误判成成功，导致播报静默丢消息而日志显示一切正常。

    返回结构不符合预期时直接报错，不用兜底把契约变化掩盖成「发送成功」。

    Args:
        payload: 宿主发送能力的返回值。

    Returns:
        Tuple[bool, str]: 是否发送成功，以及失败原因（成功时为空串）。

    Raises:
        ValueError: 返回值不是带 ``success`` 字段的字典时抛出。
    """

    if not isinstance(payload, dict) or "success" not in payload:
        raise ValueError(f"宿主发送能力返回结构异常：{type(payload).__name__}")
    if payload.get("success"):
        return True, ""
    return False, str(payload.get("error") or "宿主未给出失败原因")


async def send_report(
    ctx: Any,
    stream_id: str,
    text: str,
    image_base64: Optional[str] = None,
) -> bool:
    """向指定聊天流发送报告（先图片后文本）。

    调用方通过 ``text`` 是否为空来决定要不要同时发文字：图片渲染成功且文字与图片内容
    重复时传空串即可只发图片；``image_base64`` 为空时则只发文本。

    Args:
        ctx: 插件运行时上下文。
        stream_id: 目标聊天流 ID。
        text: 文本内容；为空时只发图片。
        image_base64: 图片 base64；为空时只发文本。

    Returns:
        bool: 是否至少成功发送了一项内容。
    """

    sent_any = False
    if image_base64:
        try:
            sent_image, image_error = _extract_send_outcome(await ctx.send.image(image_base64, stream_id))
            if sent_image:
                sent_any = True
            else:
                logger.warning("[token_usage_report] 图片发送失败：stream_id=%s，%s", stream_id, image_error)
        except Exception as exc:
            logger.warning("[token_usage_report] 图片发送异常：stream_id=%s，%s", stream_id, exc)

    if text:
        try:
            sent_text, text_error = _extract_send_outcome(await ctx.send.text(text, stream_id))
            if sent_text:
                sent_any = True
            else:
                logger.warning("[token_usage_report] 文本发送失败：stream_id=%s，%s", stream_id, text_error)
        except Exception as exc:
            logger.warning("[token_usage_report] 文本发送异常：stream_id=%s，%s", stream_id, exc)
    return sent_any


async def broadcast_report(
    ctx: Any,
    config: TokenUsageReportConfig,
    text: str,
    image_base64: Optional[str] = None,
) -> Tuple[int, int]:
    """向全部配置目标发送报告。

    Args:
        ctx: 插件运行时上下文。
        config: 插件配置。
        text: 文本内容。
        image_base64: 图片 base64。

    Returns:
        Tuple[int, int]: ``(成功数, 失败数)``。
    """

    targets, errors = await resolve_target_streams(ctx, config)
    for error in errors:
        logger.warning("[token_usage_report] 播报目标解析问题：%s", error)
    success_count = 0
    failure_count = len(errors)
    for stream_id, label in targets:
        if await send_report(ctx, stream_id, text, image_base64):
            success_count += 1
        else:
            failure_count += 1
            logger.warning("[token_usage_report] 播报发送失败：%s", label)
    return success_count, failure_count


def describe_targets(config: TokenUsageReportConfig) -> List[Dict[str, str]]:
    """返回配置中的目标清单（用于日志与 WebUI 展示）。"""

    targets: List[Dict[str, str]] = []
    for group_id in config.report.target_groups:
        if str(group_id or "").strip():
            targets.append({"type": "group", "id": str(group_id).strip()})
    for user_id in config.report.target_users:
        if str(user_id or "").strip():
            targets.append({"type": "user", "id": str(user_id).strip()})
    return targets