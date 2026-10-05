"""会话维度数据层：按 ``session_id`` 读取 ``ModelUsage`` 明细并在插件内聚合。

宿主聚合表没有会话维度，因此这里使用 ``database.get`` 的等值过滤读取明细；
读取行数有硬上限，超限直接报错，不做截断取样。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import logging

from .config_model import TokenUsageReportConfig, build_model_alias_map, build_module_group_map
from .metrics import (
    WINDOW_LABELS,
    WINDOW_ORDER,
    ModelUsageRow,
    ModuleUsageRow,
    PieSlice,
    ReportMetrics,
    SeriesData,
    WindowMetrics,
    build_scope_name,
    build_window_predicates,
    filter_and_merge_series,
    limit_series_top,
    parse_timestamp,
    resolve_module_group,
)

logger = logging.getLogger(__name__)

_MODEL_USAGE_TABLE = "ModelUsage"


class SessionStatsError(RuntimeError):
    """会话维度统计无法完成时抛出的异常（会话不存在、行数超限等）。"""


async def collect_session_metrics(
    ctx: Any,
    config: TokenUsageReportConfig,
    *,
    scope: str,
    stream_id: str = "",
    target_id: str = "",
    platform: str = "",
) -> ReportMetrics:
    """采集会话维度（当前对话 / 指定群聊 / 指定用户）的统计指标。

    Args:
        ctx: 插件运行时上下文。
        config: 插件配置。
        scope: 统计范围，取值为 ``current`` / ``group`` / ``user``。
        stream_id: 当前对话的聊天流 ID（``scope=current`` 时使用）。
        target_id: 指定群号或 QQ 号。
        platform: 平台标识。

    Returns:
        ReportMetrics: 会话维度指标快照。

    Raises:
        SessionStatsError: 会话无法解析或明细行数超过上限时抛出。
    """

    collector = _SessionCollector(ctx=ctx, config=config, platform=platform or config.report.platform)
    return await collector.collect(scope=scope, stream_id=stream_id, target_id=target_id)


class _SessionCollector:
    """会话维度指标采集器。"""

    def __init__(self, ctx: Any, config: TokenUsageReportConfig, platform: str) -> None:
        self._ctx = ctx
        self._config = config
        self._platform = platform
        self._now = datetime.now()
        self._alias_map = build_model_alias_map(config.model_aliases.aliases)
        self._module_group_map = build_module_group_map(config.module_groups)

    async def collect(self, *, scope: str, stream_id: str, target_id: str) -> ReportMetrics:
        """解析会话并完成明细聚合。"""

        session_id, target_label = await self._resolve_session(scope=scope, stream_id=stream_id, target_id=target_id)
        rows = await self._load_rows(session_id)

        metrics = ReportMetrics(
            scope=scope,
            scope_name=build_scope_name(scope, target_label),
            generated_at=self._now,
            unit_name=self._config.token_unit.unit_name,
        )
        metrics.notes = [
            "总计口径：该会话的全部历史记录（受 limits.max_session_rows 行数上限约束）",
            "缓存命中 / 未命中 Token 来自会话调用明细（全局视图的宿主聚合表不提供该字段）",
            "会话视图不包含消息数 / 回复数 / 在线时长（宿主无按会话过滤这些指标的能力）",
        ]
        metrics.total_scope_note = "该会话全部历史记录"
        self._fill_metrics(metrics, rows)
        return metrics

    # ──── 会话解析与明细读取 ────

    async def _resolve_session(self, *, scope: str, stream_id: str, target_id: str) -> Tuple[str, str]:
        """解析目标会话的 ``session_id`` 与展示名。

        Args:
            scope: 统计范围。
            stream_id: 当前对话的聊天流 ID。
            target_id: 指定群号或 QQ 号。

        Returns:
            Tuple[str, str]: ``(session_id, 展示名)``。

        Raises:
            SessionStatsError: 缺少必要参数或未找到会话时抛出。
        """

        normalized_target = str(target_id or "").strip()
        if scope == "current":
            normalized_stream = str(stream_id or "").strip()
            if not normalized_stream:
                raise SessionStatsError("当前对话统计缺少 stream_id")
            stream = await self._lookup_stream_by_session(normalized_stream)
            label = _stream_display_name(stream) if stream else "当前对话"
            return normalized_stream, label

        if not normalized_target:
            raise SessionStatsError("缺少目标参数：请提供群号或 QQ 号")

        chat = self._ctx.chat
        if scope == "group":
            stream = _as_stream(await chat.get_stream_by_group_id(normalized_target, platform=self._platform))
            if stream is None:
                raise SessionStatsError(f"未找到群 {normalized_target} 的会话记录（该群可能尚未与 Bot 交互）")
        elif scope == "user":
            stream = _as_stream(await chat.get_stream_by_user_id(normalized_target, platform=self._platform))
            if stream is None:
                raise SessionStatsError(f"未找到用户 {normalized_target} 的会话记录（该用户可能尚未与 Bot 交互）")
        else:
            raise SessionStatsError(f"不支持的统计范围：{scope}")

        session_id = str(stream.get("session_id") or "").strip()
        if not session_id:
            raise SessionStatsError(f"会话记录缺少 session_id：{stream}")
        return session_id, _stream_display_name(stream) or normalized_target

    async def _lookup_stream_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """在全部聊天流中按 session_id 查找会话信息。"""

        try:
            streams = await self._ctx.chat.get_all_streams(platform="all_platforms")
        except Exception as exc:
            logger.warning("[token_usage_report] 查询聊天流列表失败: %s", exc)
            return None
        if not isinstance(streams, list):
            return None
        for stream in streams:
            if isinstance(stream, dict) and str(stream.get("session_id") or "") == session_id:
                return stream
        return None

    async def _load_rows(self, session_id: str) -> List[Dict[str, Any]]:
        """读取指定会话的模型调用明细，并在超限时直接报错。"""

        row_limit = max(int(self._config.limits.max_session_rows), 1)
        try:
            row_count = await self._ctx.db.count(_MODEL_USAGE_TABLE, {"session_id": session_id})
        except Exception as exc:
            raise SessionStatsError(f"统计会话调用条数失败：{exc}") from exc
        if int(row_count or 0) > row_limit:
            raise SessionStatsError(
                f"该会话共 {int(row_count)} 条调用记录，超过读取上限 {row_limit} 条，已放弃统计"
                "（可调大 limits.max_session_rows）"
            )
        try:
            rows = await self._ctx.db.get(
                _MODEL_USAGE_TABLE,
                filters={"session_id": session_id},
                order_by=["-timestamp"],
                limit=row_limit,
            )
        except Exception as exc:
            raise SessionStatsError(f"读取会话调用明细失败：{exc}") from exc
        if not isinstance(rows, list):
            raise SessionStatsError(f"会话调用明细返回结构异常：{type(rows).__name__}")
        return [row for row in rows if isinstance(row, dict)]

    # ──── 明细聚合 ────

    def _fill_metrics(self, metrics: ReportMetrics, rows: List[Dict[str, Any]]) -> None:
        """把明细行聚合为窗口指标、图表数据与模型/模块占比。"""

        predicates = build_window_predicates(self._now)
        windows: Dict[str, WindowMetrics] = {
            key: WindowMetrics(key=key, label=WINDOW_LABELS[key]) for key in WINDOW_ORDER
        }
        window_cache_hit = {key: 0 for key in WINDOW_ORDER}
        window_cache_miss = {key: 0 for key in WINDOW_ORDER}
        window_latency_sum = {key: 0.0 for key in WINDOW_ORDER}
        window_latency_count = {key: 0 for key in WINDOW_ORDER}

        total = WindowMetrics(key="total", label="总计")
        total_cache_hit = 0
        total_cache_miss = 0
        total_latency_sum = 0.0
        module_values: Dict[str, ModuleUsageRow] = {}
        model_values: Dict[str, ModelUsageRow] = {}
        timestamps: List[str] = []
        token_values: List[float] = []
        cost_values: List[float] = []
        model_cost_values: Dict[str, List[float]] = {}

        for row in rows:
            parsed_timestamp = parse_timestamp(row.get("timestamp"))
            timestamp_text = parsed_timestamp.isoformat() if parsed_timestamp else ""
            tokens = int(row.get("total_tokens") or 0)
            prompt_tokens = int(row.get("prompt_tokens") or 0)
            completion_tokens = int(row.get("completion_tokens") or 0)
            cost = float(row.get("cost") or 0.0)
            time_cost = float(row.get("time_cost") or 0.0)
            cache_hit = int(row.get("prompt_cache_hit_tokens") or 0)
            cache_miss = int(row.get("prompt_cache_miss_tokens") or 0)

            self._accumulate_window(total, tokens, prompt_tokens, completion_tokens, cost)
            total_cache_hit += cache_hit
            total_cache_miss += cache_miss
            total_latency_sum += time_cost

            if parsed_timestamp is not None:
                for key, predicate in predicates.items():
                    if not predicate.contains(parsed_timestamp):
                        continue
                    self._accumulate_window(windows[key], tokens, prompt_tokens, completion_tokens, cost)
                    window_cache_hit[key] += cache_hit
                    window_cache_miss[key] += cache_miss
                    window_latency_sum[key] += time_cost
                    window_latency_count[key] += 1

            module_name = resolve_module_group(_module_name_of(row), self._module_group_map)
            module_row = module_values.get(module_name)
            if module_row is None:
                module_values[module_name] = ModuleUsageRow(name=module_name, tokens=tokens, requests=1, cost=cost)
            else:
                module_row.tokens += tokens
                module_row.requests += 1
                module_row.cost += cost

            internal_model_name = _model_name_of(row)
            display_model_name = self._alias_map.get(internal_model_name, internal_model_name)
            model_row = model_values.get(display_model_name)
            if model_row is None:
                model_values[display_model_name] = ModelUsageRow(
                    name=display_model_name,
                    internal_name=internal_model_name,
                    requests=1,
                    tokens=tokens,
                    cost=cost,
                    avg_response=time_cost,
                )
            else:
                model_row.requests += 1
                model_row.tokens += tokens
                model_row.cost += cost
                model_row.avg_response = (model_row.avg_response or 0.0) + time_cost

            if timestamp_text:
                timestamps.append(timestamp_text)
                token_values.append(float(tokens))
                cost_values.append(cost)
                series_values = model_cost_values.setdefault(display_model_name, [])
                while len(series_values) < len(timestamps) - 1:
                    series_values.append(0.0)
                series_values.append(cost)

        total.cache_hit_tokens = total_cache_hit
        total.cache_miss_tokens = total_cache_miss
        if rows:
            total.avg_response = total_latency_sum / len(rows)
        for key, window in windows.items():
            window.cache_hit_tokens = window_cache_hit[key]
            window.cache_miss_tokens = window_cache_miss[key]
            if window_latency_count[key] > 0:
                window.avg_response = window_latency_sum[key] / window_latency_count[key]

        metrics.windows = windows
        metrics.total = total
        metrics.modules = sorted(module_values.values(), key=lambda item: (-item.tokens, item.name))
        all_model_rows = sorted(model_values.values(), key=lambda item: (-item.tokens, item.name))
        for model_row in all_model_rows:
            if model_row.requests > 0 and model_row.avg_response is not None:
                model_row.avg_response = model_row.avg_response / model_row.requests
        display_limit = max(1, min(self._config.limits.top_models, 50))
        metrics.models = all_model_rows[:display_limit]

        metrics.pies["module_tokens"] = [
            PieSlice(name=item.name, value=float(item.tokens)) for item in metrics.modules if item.tokens > 0
        ]
        metrics.pies["module_cost"] = [
            PieSlice(name=item.name, value=item.cost) for item in metrics.modules if item.cost > 0
        ]
        metrics.pies["model_tokens"] = [
            PieSlice(name=item.name, value=float(item.tokens)) for item in all_model_rows if item.tokens > 0
        ]
        metrics.pies["model_cost"] = [
            PieSlice(name=item.name, value=item.cost) for item in all_model_rows if item.cost > 0
        ]
        self._fill_bars(metrics, timestamps, token_values, cost_values, model_cost_values)

    @staticmethod
    def _accumulate_window(
        window: WindowMetrics,
        tokens: int,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
    ) -> None:
        """把单条明细累加到窗口统计中。"""

        window.tokens += tokens
        window.prompt_tokens += prompt_tokens
        window.completion_tokens += completion_tokens
        window.requests += 1
        window.cost += cost

    def _fill_bars(
        self,
        metrics: ReportMetrics,
        timestamps: List[str],
        token_values: List[float],
        cost_values: List[float],
        model_cost_values: Dict[str, List[float]],
    ) -> None:
        """用明细行生成会话维度的条形图数据。"""

        if not timestamps:
            return
        chart_config = self._config.chart
        granularity = chart_config.bar_granularity
        cutoff = self._now - timedelta(days=max(int(chart_config.bar_days), 1))

        if chart_config.bar_tokens:
            merged = filter_and_merge_series(
                timestamps,
                {"Token": token_values},
                granularity=granularity,
                cutoff=cutoff,
            )
            merged.unit_label = self._config.token_unit.unit_name
            merged.value_formatter = "number"
            metrics.bars["tokens"] = merged

        if chart_config.bar_cost:
            merged_cost = filter_and_merge_series(
                timestamps,
                {"花费": cost_values},
                granularity=granularity,
                cutoff=cutoff,
            )
            metrics.bars["cost"] = SeriesData(
                labels=merged_cost.labels,
                series=merged_cost.series,
                unit_label="¥",
                value_formatter="cost",
            )

        if chart_config.bar_model_cost and model_cost_values:
            merged_models = filter_and_merge_series(
                timestamps,
                model_cost_values,
                granularity=granularity,
                cutoff=cutoff,
            )
            metrics.bars["model_cost"] = limit_series_top(merged_models, chart_config.series_top)


def _as_stream(payload: Any) -> Optional[Dict[str, Any]]:
    """把 ``chat.get_stream_by_*`` 的返回值规范化为聊天流字典。"""

    if isinstance(payload, dict):
        if isinstance(payload.get("stream"), dict):
            return payload["stream"]
        if payload.get("session_id"):
            return payload
    return None


def _stream_display_name(stream: Dict[str, Any]) -> str:
    """生成聊天流的展示名称（优先群名，其次昵称，最后群号/QQ 号）。"""

    group_name = str(stream.get("group_name") or "").strip()
    if group_name:
        return group_name
    for key in ("user_cardname", "user_nickname"):
        name = str(stream.get(key) or "").strip()
        if name:
            return name
    group_id = str(stream.get("group_id") or "").strip()
    if group_id:
        return f"群 {group_id}"
    user_id = str(stream.get("user_id") or "").strip()
    if user_id:
        return f"用户 {user_id}"
    return ""


def _module_name_of(row: Dict[str, Any]) -> str:
    """从明细行中提取模块名（``request_type`` 首个 ``.`` 之前的部分）。"""

    request_type = str(row.get("request_type") or "").strip()
    if not request_type:
        return "unknown"
    if "." in request_type:
        return request_type.split(".", 1)[0]
    return request_type


def _model_name_of(row: Dict[str, Any]) -> str:
    """从明细行中提取模型显示用名称（优先自定义名）。"""

    assign_name = str(row.get("model_assign_name") or "").strip()
    if assign_name:
        return assign_name
    return str(row.get("model_name") or "").strip() or "未知模型"