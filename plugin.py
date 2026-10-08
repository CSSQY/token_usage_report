"""Token 用量统计与播报插件入口。

提供三块能力：
1. ``query_token_usage`` 工具：供 LLM 查询全局或会话维度的 Token 消耗；
2. ``/token`` 指令：在指令来源处回复统计结果（支持 all / 群 / 用户）；
3. 每日定时播报：按配置时刻向指定 QQ 群 / QQ 号推送统计报告。
"""

from datetime import datetime, time as datetime_time, timedelta
from typing import Any, Dict, List, Optional, Tuple

import asyncio
import contextlib
import logging

from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, Command, MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .config_model import TokenUsageReportConfig
from .data_sources import collect_global_metrics
from .delivery import broadcast_report, describe_targets, send_report
from .metrics import (
    WINDOW_USAGE_HINT,
    ReportMetrics,
    normalize_window_key,
)
from .permissions import (
    SCOPE_LABELS,
    SCOPE_VALUES,
    check_conversation_access,
    normalize_scope,
    resolve_allowed,
    scope_labels,
    window_labels,
)
from .renderer import build_report_image
from .session_stats import SessionStatsError, collect_session_metrics
from .text_report import build_short_window_text, render_llm_report, render_text_report

logger = logging.getLogger(__name__)

_USAGE_TEXT = (
    "用法：/token [范围] [时间]\n"
    "范围：留空=当前对话，all=全部会话，群 <群号>，用户 <QQ号>\n"
    f"时间：留空=全部窗口，可填 {WINDOW_USAGE_HINT}\n"
    "示例：/token、/token 今日、/token all 本周、/token 群 123456 最近7天、/token 用户 10001 30天"
)
_IDLE_WAIT_SECONDS = 60.0


class TokenUsageReportPlugin(MaiBotPlugin):
    """Token 用量统计与播报插件。"""

    config_model = TokenUsageReportConfig

    def __init__(self) -> None:
        """初始化插件实例与内部任务状态。"""

        super().__init__()
        self._scheduler_task: Optional[asyncio.Task[None]] = None
        self._reload_event: Optional[asyncio.Event] = None
        self._logged_invalid_times: set[str] = set()

    # ──── 生命周期 ────

    async def on_load(self) -> None:
        """插件加载：启动每日定时播报循环。"""

        self._reload_event = asyncio.Event()
        self._scheduler_task = asyncio.create_task(self._schedule_loop())
        logger.info("[token_usage_report] 插件已加载，定时播报循环已启动（enabled=%s）", self.config.report.enabled)

    async def on_unload(self) -> None:
        """插件卸载：停止定时播报循环。"""

        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scheduler_task
            self._scheduler_task = None
        logger.info("[token_usage_report] 插件已卸载，定时播报循环已停止")

    async def on_config_update(self, scope: str, config_data: Dict[str, object], version: str) -> None:
        """处理配置热更新。

        Args:
            scope: 配置变更范围。
            config_data: 最新配置数据。
            version: 配置版本号。
        """

        del config_data
        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        self._logged_invalid_times.clear()
        if self._reload_event is not None:
            self._reload_event.set()
        logger.info("[token_usage_report] 插件配置已更新（version=%s），定时任务将按新配置重算", version)

    # ──── LLM 工具 ────

    @Tool(
        "query_token_usage",
        description=(
            "查询 Bot 自身消耗的用量统计（单位名可在插件配置里自定义，见返回的 unit_name；"
            "受与 /token 指令相同的黑白名单限制，且只能查询「工具可查询范围」"
            "里允许的范围，默认只允许当前对话）。scope 可选：current=当前对话（默认，取本次调用的会话上下文）、"
            "all=全部会话、group=指定群聊（需传 target_id 群号）、user=指定用户（需传 target_id QQ 号）；"
            f"window 可选 {WINDOW_USAGE_HINT}（也支持 today/this_week/this_month/last_24h/last_7d/last_30d），"
            "留空返回全部窗口。"
        ),
        parameters=[
            ToolParameterInfo(
                name="scope",
                param_type=ToolParamType.STRING,
                description="统计范围：current / all / group / user",
                required=False,
                default="current",
            ),
            ToolParameterInfo(
                name="target_id",
                param_type=ToolParamType.STRING,
                description="scope=group 时的群号，scope=user 时的 QQ 号",
                required=False,
                default="",
            ),
            ToolParameterInfo(
                name="window",
                param_type=ToolParamType.STRING,
                description="时间窗口，可填 今日/本周/本月/最近24小时/最近7天/最近30天 或对应英文名，留空返回全部窗口",
                required=False,
                default="",
            ),
        ],
    )
    async def handle_query_token_usage(
        self,
        scope: str = "current",
        target_id: str = "",
        stream_id: str = "",
        window: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """查询 Token 用量并返回给 LLM。

        权限与范围收口（工具由模型调用、调用者不会看到指令帮助，因此这里必须自己拦）：
        1. 只认「LLM 工具查询权限」这一组配置（与 ``/token`` 的「指令查询权限」互相独立）；
        2. 先过对话流黑白名单，再过该对话的「范围 / 窗口」放行列表
           （每对话范围覆盖优先于全局默认开关）。
        """

        tool_permission = self.config.llm_tool_permission
        normalized_scope = normalize_scope(scope or "current") or ""
        if normalized_scope not in SCOPE_VALUES:
            return {"success": False, "content": f"不支持的统计范围：{scope}（可选 {'/'.join(SCOPE_VALUES)}）"}

        requested_group = str(kwargs.get("group_id") or "")
        requested_user = str(kwargs.get("user_id") or "")
        if not check_conversation_access(
            tool_permission,
            group_id=requested_group,
            user_id=requested_user,
            is_local_operator=False,
        ):
            logger.info(
                "[token_usage_report] 工具调用被对话流名单拦下：group=%s user=%s scope=%s",
                requested_group,
                requested_user,
                normalized_scope,
            )
            return {
                "success": False,
                "content": (
                    f"当前会话没有查询 {self.config.token_unit.unit_name} 用量的权限"
                    "（由配置「LLM 工具查询权限」决定）"
                ),
            }

        allowed_scopes, allowed_windows = resolve_allowed(
            tool_permission, group_id=requested_group, user_id=requested_user
        )
        if normalized_scope not in allowed_scopes:
            logger.info(
                "[token_usage_report] 工具调用超出放行范围：scope=%s，本对话放行=%s",
                normalized_scope,
                scope_labels(sorted(allowed_scopes)),
            )
            return {
                "success": False,
                "content": (
                    f"不允许通过工具查询「{SCOPE_LABELS.get(normalized_scope, normalized_scope)}」范围"
                    "（可查询范围由配置「LLM 工具查询权限」决定；需要全局统计请让管理员使用 /token 指令）"
                ),
            }

        raw_window = str(window or "").strip().lower()
        if raw_window in {"", "all"}:
            requested_window = "all"
        else:
            matched_window = normalize_window_key(raw_window)
            if matched_window is None:
                return {
                    "success": False,
                    "content": f"不支持的时间范围：{window}（可填 {WINDOW_USAGE_HINT}）",
                }
            requested_window = matched_window
        if requested_window not in allowed_windows:
            logger.info(
                "[token_usage_report] 工具调用超出放行窗口：window=%s，本对话放行=%s",
                requested_window,
                window_labels(sorted(allowed_windows)),
            )
            return {
                "success": False,
                "content": (
                    f"不允许通过工具查询「{window_labels([requested_window])}」时间范围"
                    "（可查询窗口由配置「LLM 工具查询权限」决定）"
                ),
            }

        window_key = "" if requested_window == "all" else requested_window

        try:
            metrics = await self._collect_metrics(
                scope=normalized_scope,
                stream_id=stream_id,
                target_id=target_id,
                window_key=window_key,
            )
        except SessionStatsError as exc:
            return {"success": False, "content": str(exc)}
        except Exception as exc:
            logger.error("[token_usage_report] 工具统计失败：%s", exc, exc_info=True)
            return {"success": False, "content": f"统计失败：{exc}"}

        return {
            "success": True,
            "content": build_short_window_text(metrics, window_key or "all"),
            "scope": metrics.scope,
            "scope_name": metrics.scope_name,
            "unit_name": metrics.unit_name,
            "windows": {
                window_item.key: {"tokens": window_item.tokens, "requests": window_item.requests, "cost": window_item.cost}
                for window_item in metrics.ordered_windows()
            },
            "total": None
            if metrics.total is None or metrics.window_scoped
            else {
                "tokens": metrics.total.tokens,
                "requests": metrics.total.requests,
                "cost": metrics.total.cost,
                "cache_hit_tokens": metrics.total.cache_hit_tokens,
                "cache_miss_tokens": metrics.total.cache_miss_tokens,
            },
            "unavailable": sorted(set(metrics.unavailable)),
        }

    # ──── 指令 ────

    @Command(
        "token_usage",
        description="统计 Bot 的模型用量消耗（可指定范围与时间，例如 /token all 今日、/token 群 123456 本周）",
        pattern=(
            r"^/(?:token|tokens)"
            r"(?:\s+(?P<arg1>\S+))?"
            r"(?:\s+(?P<arg2>\S+))?"
            r"(?:\s+(?P<arg3>\S+))?$"
        ),
    )
    async def handle_token_command(
        self,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
        is_local_operator: bool = False,
        **kwargs: Any,
    ) -> Tuple[bool, str, bool]:
        """处理 ``/token`` 指令。"""

        if not self.config.command.enabled:
            return False, "统计指令已禁用", True

        command_permission = self.config.command_permission
        if not check_conversation_access(
            command_permission,
            group_id=group_id,
            user_id=user_id,
            is_local_operator=bool(is_local_operator),
        ):
            await self._notify_no_permission(stream_id)
            return True, "无权限", True

        scope, target_id, window_key, error_message = self._parse_command_args(kwargs)
        if error_message:
            await self.ctx.send.text(error_message, stream_id)
            return False, error_message, True

        # 覆盖项按「发起指令的这个对话」取，与查询目标（群号/QQ 号）无关
        allowed_scopes, allowed_windows = resolve_allowed(
            command_permission, group_id=group_id, user_id=user_id
        )
        requested_scope = normalize_scope(scope) or scope
        requested_window = window_key or "all"
        if requested_scope not in allowed_scopes or requested_window not in allowed_windows:
            logger.info(
                "[token_usage_report] 指令请求超出放行列表：group=%s user=%s scope=%s（放行 %s）window=%s（放行 %s）",
                group_id,
                user_id,
                requested_scope,
                scope_labels(sorted(allowed_scopes)),
                requested_window,
                window_labels(sorted(allowed_windows)),
            )
            await self._notify_no_permission(stream_id)
            return False, "范围或时间超出权限", True

        try:
            metrics = await self._collect_metrics(
                scope=scope, stream_id=stream_id, target_id=target_id, window_key=window_key
            )
        except SessionStatsError as exc:
            await self.ctx.send.text(str(exc), stream_id)
            return False, str(exc), True
        except Exception as exc:
            logger.error("[token_usage_report] 指令统计失败：%s", exc, exc_info=True)
            failure_text = f"统计失败：{exc}"
            await self.ctx.send.text(failure_text, stream_id)
            return False, failure_text, True

        text_report, image_base64 = await self._build_delivery(metrics, self.config.command.send_mode)
        sent = await send_report(self.ctx, stream_id, text_report, image_base64)
        if not sent:
            return False, "统计结果发送失败", True
        return True, f"已发送 {self.config.token_unit.unit_name} 统计", True

    async def _build_delivery(
        self, metrics: ReportMetrics, send_mode: str
    ) -> Tuple[str, Optional[str]]:
        """按发送方式准备要发出的内容，返回 ``(文本, 图片 base64)``。

        三种发送方式互斥（由指令 / 定时播报各自的分节配置）：
        - ``template``：按「模板文本」分节的模板渲染文本；
        - ``llm``：LLM 风格化转述（失败自动回退模板文本）；
        - ``image``：图片报告（渲染失败自动回退模板文本）。

        Args:
            metrics: 指标快照。
            send_mode: 发送方式。

        Returns:
            Tuple[str, Optional[str]]: 要发送的文本与图片 base64（二者只会有其一非空）。
        """

        if send_mode == "image":
            image_base64, render_error = await build_report_image(self.ctx, metrics, self.config)
            if image_base64 is not None:
                return "", image_base64
            logger.error("[token_usage_report] 图片渲染失败，已回退为模板文本：%s", render_error)
            return render_text_report(metrics, self.config), None
        if send_mode == "llm":
            return await render_llm_report(self.ctx, metrics, self.config), None
        return render_text_report(metrics, self.config), None

    def _parse_command_args(self, kwargs: Dict[str, Any]) -> Tuple[str, str, str, str]:
        """解析指令参数，返回 ``(scope, target_id, window_key, 错误提示)``。

        参数顺序不限，时间窗口可与范围参数混用，例如：``/token``、
        ``/token all``、``/token 今日``、``/token all 今日``、
        ``/token 群 123456 本周``、``/token 用户 10001 7天``。
        ``群`` / ``用户`` 后面的第一个参数一律当作目标值，不会被误判成时间窗口。
        """

        scope = "current"
        target_id = ""
        window_key = ""
        expect_target = False
        for arg in self._collect_command_args(kwargs):
            if expect_target:
                target_id = arg
                expect_target = False
                continue

            normalized_scope = normalize_scope(arg)
            if normalized_scope in {"group", "user"}:
                if scope in {"group", "user"}:
                    return "", "", "", f"重复指定了范围。{_USAGE_TEXT}"
                scope = normalized_scope
                expect_target = True
                continue
            if normalized_scope in {"all", "current"}:
                if scope != "current" or target_id:
                    return "", "", "", f"重复指定了范围。{_USAGE_TEXT}"
                scope = normalized_scope
                continue

            matched_window = normalize_window_key(arg)
            if matched_window:
                if window_key:
                    return "", "", "", f"只能指定一个时间范围。{_USAGE_TEXT}"
                window_key = matched_window
                continue

            return "", "", "", f"无法识别的参数「{arg}」。{_USAGE_TEXT}"

        if expect_target:
            return "", "", "", f"缺少目标：{_USAGE_TEXT}"
        return scope, target_id, window_key, ""

    @staticmethod
    def _collect_command_args(kwargs: Dict[str, Any]) -> List[str]:
        """从指令回调参数中取出位置参数列表（优先取正则捕获组）。"""

        matched_groups = kwargs.get("matched_groups")
        if isinstance(matched_groups, dict):
            collected = [
                str(matched_groups.get(name) or "").strip()
                for name in ("arg1", "arg2", "arg3")
                if str(matched_groups.get(name) or "").strip()
            ]
            if collected:
                return collected

        raw_text = str(kwargs.get("text") or "").strip()
        return raw_text.split()[1:] if raw_text.split() else []

    async def _notify_no_permission(self, stream_id: str) -> None:
        """按配置决定是否回复「无权限」提示。

        ``command.notify_no_permission`` 关闭时完全静默；
        开启但 ``deny_message`` 为空时同样不回复（把提示文案留空即静默拒绝）。
        """

        if not self.config.command.notify_no_permission:
            return
        deny_message = str(self.config.command.deny_message or "").strip()
        if not deny_message:
            return
        await self.ctx.send.text(deny_message, stream_id)

    # ──── 定时播报 ────

    async def _schedule_loop(self) -> None:
        """每日定时播报循环：漏过的时刻不补发，失败不重试。"""

        while True:
            try:
                wait_seconds = self._seconds_until_next_run()
                if wait_seconds is None:
                    await self._wait_or_reload(_IDLE_WAIT_SECONDS)
                    continue
                await self._wait_or_reload(wait_seconds)
                if self._reload_event is not None and self._reload_event.is_set():
                    self._reload_event.clear()
                    continue
                await self._run_scheduled_report()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[token_usage_report] 定时播报执行失败（本次不重试）：%s", exc, exc_info=True)
                await self._wait_or_reload(_IDLE_WAIT_SECONDS)

    async def _wait_or_reload(self, seconds: float) -> None:
        """等待指定秒数，或被配置更新事件提前唤醒。"""

        if self._reload_event is None:
            await asyncio.sleep(max(seconds, 0.0))
            return
        reload_task = asyncio.create_task(self._reload_event.wait())
        try:
            await asyncio.wait({reload_task}, timeout=max(seconds, 0.0))
        finally:
            reload_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reload_task

    def _seconds_until_next_run(self) -> Optional[float]:
        """计算距离下一个播报时刻的秒数；未启用或时刻非法时返回 None。"""

        report_config = self.config.report
        if not report_config.enabled:
            return None

        now = datetime.now()
        valid_times: List[datetime_time] = []
        for raw_time in report_config.schedule_times:
            parsed_time = _parse_schedule_time(raw_time)
            if parsed_time is None:
                if raw_time not in self._logged_invalid_times:
                    self._logged_invalid_times.add(raw_time)
                    logger.warning("[token_usage_report] 播报时刻格式非法，已忽略：%s（应为 HH:MM）", raw_time)
                continue
            valid_times.append(parsed_time)
        if not valid_times:
            if not self._logged_invalid_times:
                self._logged_invalid_times.add("<empty>")
                logger.warning("[token_usage_report] 定时播报已启用，但没有可用的发送时刻，已跳过")
            return None

        today_candidates = [
            now.replace(hour=item.hour, minute=item.minute, second=0, microsecond=0)
            for item in valid_times
            if now.replace(hour=item.hour, minute=item.minute, second=0, microsecond=0) > now
        ]
        next_run = min(today_candidates) if today_candidates else (
            datetime.combine(now.date() + timedelta(days=1), min(valid_times))
        )
        return max((next_run - now).total_seconds(), 0.0)

    async def _run_scheduled_report(self) -> None:
        """执行一次定时播报。"""

        if not describe_targets(self.config):
            logger.warning("[token_usage_report] 定时播报已启用但未配置任何目标群/用户，本次跳过")
            return

        metrics = await collect_global_metrics(self.ctx, self.config)
        text_report, image_base64 = await self._build_delivery(metrics, self.config.report.send_mode)
        success_count, failure_count = await broadcast_report(self.ctx, self.config, text_report, image_base64)
        logger.info("[token_usage_report] 定时播报完成：成功 %d 个目标，失败 %d 个目标", success_count, failure_count)

    # ──── 共用 ────

    async def _collect_metrics(
        self,
        *,
        scope: str,
        stream_id: str = "",
        target_id: str = "",
        window_key: str = "",
    ) -> ReportMetrics:
        """按统计范围采集指标；``window_key`` 非空时整份报告只覆盖该窗口。"""

        if scope == "all":
            return await collect_global_metrics(self.ctx, self.config, window_key)
        return await collect_session_metrics(
            self.ctx,
            self.config,
            scope=scope,
            stream_id=stream_id,
            target_id=target_id,
            platform=self.config.report.platform,
            window_key=window_key,
        )


def _parse_schedule_time(raw_value: object) -> Optional[datetime_time]:
    """解析 ``HH:MM`` 格式的播报时刻。

    Args:
        raw_value: 配置中的时刻文本。

    Returns:
        Optional[datetime_time]: 解析成功时返回时间对象，否则返回 None。
    """

    text = str(raw_value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return datetime_time(hour=hour, minute=minute)


def create_plugin() -> TokenUsageReportPlugin:
    """创建插件实例。

    Returns:
        TokenUsageReportPlugin: 新的插件实例。
    """

    return TokenUsageReportPlugin()