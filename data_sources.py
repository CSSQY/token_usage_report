"""全局视图数据层：调用宿主 statistics 能力并归并成报告指标。

本模块只使用宿主通过 ``ctx`` 暴露的能力，不直接访问数据库文件，
也不导入任何 ``src.*`` 模块。
"""

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import logging

from .config_model import TokenUsageReportConfig, build_model_alias_map, build_module_group_map
from .metrics import (
    UNKNOWN_GROUP_NAME,
    WINDOW_LABELS,
    WINDOW_ORDER,
    ChatMessageRow,
    ModelUsageRow,
    PieSlice,
    ReportMetrics,
    SeriesData,
    WindowMetrics,
    build_scope_name,
    build_window_predicates,
    filter_and_merge_series,
    limit_series_top,
    ordered_group_items,
    resolve_module_group,
    sum_series_in_window,
)

logger = logging.getLogger(__name__)

_REPLY_TOOL_NAME = "reply"
_MAX_HOST_LIMIT = 50


async def collect_global_metrics(ctx: Any, config: TokenUsageReportConfig) -> ReportMetrics:
    """采集全局（全部会话）统计指标。

    Args:
        ctx: 插件运行时上下文。
        config: 插件配置。

    Returns:
        ReportMetrics: 全局指标快照；单项能力失败时对应区块标记为不可用。
    """

    collector = _GlobalCollector(ctx=ctx, config=config)
    return await collector.collect()


class _GlobalCollector:
    """全局指标采集器。"""

    def __init__(self, ctx: Any, config: TokenUsageReportConfig) -> None:
        self._ctx = ctx
        self._config = config
        self._now = datetime.now()
        self._alias_map = build_model_alias_map(config.model_aliases.aliases)
        self._module_group_map = build_module_group_map(config.module_groups)
        self._predicates = build_window_predicates(self._now)
        self._windows: Dict[str, WindowMetrics] = {
            key: WindowMetrics(key=key, label=WINDOW_LABELS[key]) for key in WINDOW_ORDER
        }
        self._total_window = WindowMetrics(key="total", label="总计")
        self._metrics = ReportMetrics(
            scope="all",
            scope_name=build_scope_name("all"),
            generated_at=self._now,
            unit_name=config.token_unit.unit_name,
        )

    async def collect(self) -> ReportMetrics:
        """执行完整的全局采集流程。"""

        statistics = self._ctx.statistics.local
        chart_granularity = self._config.chart.bar_granularity
        chart_days, chart_bucket = self._resolve_chart_range(chart_granularity)

        token_hourly = await self._fetch_series("Token 趋势（小时）", lambda: statistics.token_trend(days=32, bucket="hour"))
        token_daily = await self._fetch_series("Token 趋势（天）", lambda: statistics.token_trend(days=365, bucket="day"))
        cost_hourly = await self._fetch_series(
            "花费趋势（小时）",
            lambda: statistics.model_trend(days=32, bucket="hour", metric="cost", top_models=_MAX_HOST_LIMIT),
        )
        cost_daily = await self._fetch_series(
            "花费趋势（天）",
            lambda: statistics.model_trend(days=365, bucket="day", metric="cost", top_models=_MAX_HOST_LIMIT),
        )

        self._fill_token_windows(token_hourly)
        self._fill_cost_windows(cost_hourly)
        self._fill_total(token_daily, cost_daily)
        await self._fill_models()
        await self._fill_messages(chart_days, chart_bucket, chart_granularity)
        await self._fill_replies()
        await self._fill_online_time()
        module_names = await self._fill_module_distribution()
        await self._fill_module_cost(chart_days, chart_bucket, chart_granularity, module_names)
        self._fill_bars(
            chart_granularity=chart_granularity,
            token_hourly=token_hourly,
            token_daily=token_daily,
            cost_hourly=cost_hourly,
            cost_daily=cost_daily,
        )
        self._fill_notes()

        self._metrics.windows = self._windows
        self._metrics.total = self._total_window
        return self._metrics

    # ──── 图表档位与通用取数 ────

    def _resolve_chart_range(self, granularity: str) -> Tuple[int, str]:
        """解析条形图所需的宿主取数档位。

        Args:
            granularity: 配置的图表颗粒度。

        Returns:
            Tuple[int, str]: 天数与宿主桶粒度。
        """

        if granularity == "hour":
            return 32, "hour"
        return 365, "day"

    def _chart_cutoff(self) -> datetime:
        """返回条形图横轴起点。"""

        return self._now - timedelta(days=max(int(self._config.chart.bar_days), 1))

    @staticmethod
    def _as_series(payload: Any) -> Dict[str, Any]:
        """把能力返回值规范化为 series 结构。

        Args:
            payload: 能力返回值。

        Returns:
            Dict[str, Any]: series 结构。

        Raises:
            ValueError: 返回结构不符合预期时抛出。
        """

        if isinstance(payload, dict):
            if "timestamps" in payload and "values_by_key" in payload:
                return payload
            series = payload.get("series")
            if isinstance(series, dict) and "timestamps" in series:
                return series
            if payload.get("success") is False:
                raise ValueError(str(payload.get("error") or "能力返回失败"))
        raise ValueError(f"能力返回结构异常：{type(payload).__name__}")

    async def _fetch_series(self, label: str, call: Callable[[], Awaitable[Any]]) -> Optional[Dict[str, Any]]:
        """调用能力并返回 series 结构；失败时记录不可用区块。

        Args:
            label: 区块名称，用于日志与「数据不可用」提示。
            call: 能力调用闭包。

        Returns:
            Optional[Dict[str, Any]]: series 结构；失败时返回 None。
        """

        try:
            return self._as_series(await call())
        except Exception as exc:
            logger.error("[token_usage_report] %s 获取失败: %s", label, exc)
            self._metrics.unavailable.append(label)
            return None

    async def _fetch_list(self, label: str, call: Callable[[], Awaitable[Any]], key: str) -> Optional[List[Any]]:
        """调用能力并返回列表结构；失败时记录不可用区块。

        Args:
            label: 区块名称。
            call: 能力调用闭包。
            key: 列表所在的字段名。

        Returns:
            Optional[List[Any]]: 列表结果；失败时返回 None。
        """

        try:
            payload = await call()
        except Exception as exc:
            logger.error("[token_usage_report] %s 获取失败: %s", label, exc)
            self._metrics.unavailable.append(label)
            return None
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            items = payload.get(key)
            if isinstance(items, list):
                return items
            if payload.get("success") is False:
                logger.error("[token_usage_report] %s 返回失败: %s", label, payload.get("error"))
            else:
                logger.error("[token_usage_report] %s 返回结构异常: %s", label, sorted(payload.keys()))
        else:
            logger.error("[token_usage_report] %s 返回结构异常：%s", label, type(payload).__name__)
        self._metrics.unavailable.append(label)
        return None

    async def _fetch_distribution(self, label: str, call: Callable[[], Awaitable[Any]]) -> Optional[Dict[str, Any]]:
        """调用 token_distribution 能力并返回 distribution 结构。

        Args:
            label: 区块名称。
            call: 能力调用闭包。

        Returns:
            Optional[Dict[str, Any]]: distribution 结构；失败时返回 None。
        """

        try:
            payload = await call()
        except Exception as exc:
            logger.error("[token_usage_report] %s 获取失败: %s", label, exc)
            self._metrics.unavailable.append(label)
            return None
        if isinstance(payload, dict):
            distribution = payload.get("distribution", payload)
            if isinstance(distribution, dict) and "pies" in distribution:
                return distribution
            if payload.get("success") is False:
                logger.error("[token_usage_report] %s 返回失败: %s", label, payload.get("error"))
            else:
                logger.error("[token_usage_report] %s 返回结构异常: %s", label, sorted(payload.keys()))
        else:
            logger.error("[token_usage_report] %s 返回结构异常：%s", label, type(payload).__name__)
        self._metrics.unavailable.append(label)
        return None

    # ──── 窗口与总计 ────

    def _fill_token_windows(self, series: Optional[Dict[str, Any]]) -> None:
        """用小时序列填充 6 个时间窗口的 Token 指标。"""

        if series is None:
            return
        timestamps = [str(item) for item in series.get("timestamps", [])]
        values_by_key = series.get("values_by_key", {})
        for key, window in self._windows.items():
            predicate = self._predicates[key]
            window.tokens = int(sum_series_in_window(timestamps, values_by_key.get("total_tokens", []), predicate))
            window.prompt_tokens = int(
                sum_series_in_window(timestamps, values_by_key.get("prompt_tokens", []), predicate)
            )
            window.completion_tokens = int(
                sum_series_in_window(timestamps, values_by_key.get("completion_tokens", []), predicate)
            )
            window.requests = int(sum_series_in_window(timestamps, values_by_key.get("request_count", []), predicate))

    def _fill_cost_windows(self, series: Optional[Dict[str, Any]]) -> None:
        """用小时花费序列填充 6 个时间窗口的花费。"""

        if series is None:
            return
        timestamps = [str(item) for item in series.get("timestamps", [])]
        values_by_key = series.get("values_by_key", {})
        for key, window in self._windows.items():
            window.cost = _sum_all_values_in_window(timestamps, values_by_key, self._predicates[key])

    def _fill_total(self, token_daily: Optional[Dict[str, Any]], cost_daily: Optional[Dict[str, Any]]) -> None:
        """填充总计区块（宿主上限 365 天）。"""

        if token_daily is not None:
            values_by_key = token_daily.get("values_by_key", {})
            self._total_window.tokens = int(_sum_key(values_by_key, "total_tokens"))
            self._total_window.prompt_tokens = int(_sum_key(values_by_key, "prompt_tokens"))
            self._total_window.completion_tokens = int(_sum_key(values_by_key, "completion_tokens"))
            self._total_window.requests = int(_sum_key(values_by_key, "request_count"))
        if cost_daily is not None:
            self._total_window.cost = _sum_all_values(cost_daily.get("values_by_key", {}))

    # ──── 模型、消息、回复、在线时长 ────

    async def _fill_models(self) -> None:
        """填充模型排行、模型占比与加权平均响应。"""

        statistics = self._ctx.statistics.local
        rows = await self._fetch_list("模型统计", lambda: statistics.models(days=365, limit=_MAX_HOST_LIMIT), "models")
        self._metrics.models = []
        if rows is None:
            return

        total_requests = 0
        total_response_weight = 0.0
        model_tokens: List[PieSlice] = []
        model_cost: List[PieSlice] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            internal_name = str(row.get("model_name") or "未知模型")
            display_name = self._alias_map.get(internal_name, internal_name)
            request_count = int(row.get("request_count") or 0)
            tokens = int(row.get("total_tokens") or 0)
            cost = float(row.get("total_cost") or 0.0)
            avg_response = float(row.get("avg_response_time") or 0.0)
            self._metrics.models.append(
                ModelUsageRow(
                    name=display_name,
                    internal_name=internal_name,
                    requests=request_count,
                    tokens=tokens,
                    cost=cost,
                    avg_response=avg_response if request_count > 0 else None,
                )
            )
            total_requests += request_count
            total_response_weight += avg_response * request_count
            if tokens > 0:
                model_tokens.append(PieSlice(name=display_name, value=float(tokens)))
            if cost > 0:
                model_cost.append(PieSlice(name=display_name, value=cost))

        self._metrics.models.sort(key=lambda item: (-item.tokens, item.name))
        display_limit = max(1, min(self._config.limits.top_models, _MAX_HOST_LIMIT))
        self._metrics.models = self._metrics.models[:display_limit]
        self._metrics.pies["model_tokens"] = model_tokens
        self._metrics.pies["model_cost"] = model_cost
        if total_requests > 0:
            self._total_window.avg_response = total_response_weight / total_requests

    async def _fill_messages(self, chart_days: int, chart_bucket: str, chart_granularity: str) -> None:
        """填充消息数、聊天消息分布与聊天流消息序列。"""

        statistics = self._ctx.statistics.local
        daily = await self._fetch_series(
            "消息趋势",
            lambda: statistics.message_trend(days=365, bucket="day", top_chats=_MAX_HOST_LIMIT),
        )
        if daily is None:
            return

        values_by_key = daily.get("values_by_key", {})
        labels_by_key = daily.get("labels_by_key", {})
        self._metrics.messages = int(sum(_sum_key(values_by_key, key) for key in values_by_key))

        stream_types = await self._fetch_stream_types()
        chat_rows: List[ChatMessageRow] = []
        for key, values in values_by_key.items():
            label = str(labels_by_key.get(key, key))
            chat_rows.append(
                ChatMessageRow(
                    name=label,
                    messages=int(sum(float(item or 0) for item in values)),
                    is_group=stream_types.get(label),
                )
            )
        chat_rows.sort(key=lambda item: (-item.messages, item.name))
        self._metrics.chats = chat_rows
        self._metrics.pies["chat_messages"] = self._build_chat_pie_slices(chat_rows)

        if chart_granularity == "hour":
            chart_series = await self._fetch_series(
                "消息趋势（图表）",
                lambda: statistics.message_trend(days=chart_days, bucket=chart_bucket, top_chats=_MAX_HOST_LIMIT),
            )
        else:
            chart_series = daily
        if chart_series is None:
            return
        merged = filter_and_merge_series(
            [str(item) for item in chart_series.get("timestamps", [])],
            _rename_series_values(chart_series.get("values_by_key", {}), chart_series.get("labels_by_key", {})),
            granularity=chart_granularity,
            cutoff=self._chart_cutoff(),
        )
        merged.unit_label = "条"
        merged.value_formatter = "number"
        self._metrics.bars["chat_messages"] = limit_series_top(merged, self._config.chart.series_top)

    async def _fetch_stream_types(self) -> Dict[str, bool]:
        """获取「聊天对象名称 → 是否群聊」的映射，用于匿名化与展示。"""

        payload = await self._fetch_list(
            "聊天流列表",
            lambda: self._ctx.chat.get_all_streams(platform="all_platforms"),
            "streams",
        )
        type_map: Dict[str, bool] = {}
        if payload is None:
            return type_map
        for stream in payload:
            if not isinstance(stream, dict):
                continue
            is_group = bool(stream.get("is_group_session"))
            for name_key in ("group_name", "user_nickname", "user_cardname"):
                name = str(stream.get(name_key) or "").strip()
                if name:
                    type_map.setdefault(name, is_group)
        return type_map

    def _build_chat_pie_slices(self, chat_rows: List[ChatMessageRow]) -> List[PieSlice]:
        """按配置生成聊天消息分布扇形图数据（可匿名化）。"""

        if not self._config.render.anonymize:
            return [PieSlice(name=row.name, value=float(row.messages)) for row in chat_rows]

        slices: List[PieSlice] = []
        group_index = 0
        private_index = 0
        for row in chat_rows:
            if row.is_group is True:
                display_name = f"群聊{_index_to_letter(group_index)}"
                group_index += 1
            elif row.is_group is False:
                display_name = f"个人用户{_index_to_letter(private_index)}"
                private_index += 1
            else:
                display_name = f"会话{_index_to_letter(len(slices))}"
            slices.append(PieSlice(name=display_name, value=float(row.messages)))
        return slices

    async def _fill_replies(self) -> None:
        """填充回复数（宿主内置统计口径：``reply`` 工具调用次数）。"""

        statistics = self._ctx.statistics.local
        series = await self._fetch_series(
            "工具调用趋势",
            lambda: statistics.tool_trend(days=365, bucket="day", top_tools=_MAX_HOST_LIMIT),
        )
        if series is None:
            return
        labels_by_key = series.get("labels_by_key", {})
        values_by_key = series.get("values_by_key", {})
        for key, values in values_by_key.items():
            if str(labels_by_key.get(key, key)).strip().lower() != _REPLY_TOOL_NAME:
                continue
            self._metrics.replies = int(sum(float(item or 0) for item in values))
            return
        logger.info("[token_usage_report] 前 50 工具中未找到 reply 工具，回复数按 0 处理")
        self._metrics.replies = 0

    async def _fill_online_time(self) -> None:
        """填充在线时长（小时）。"""

        statistics = self._ctx.statistics.local
        series = await self._fetch_series("在线时长趋势", lambda: statistics.online_time_trend(days=365, bucket="day"))
        if series is None:
            return
        online_values = series.get("values_by_key", {}).get("online_hours", [])
        self._metrics.online_hours = float(sum(float(item or 0) for item in online_values))

    async def _fill_module_distribution(self) -> List[str]:
        """填充模块 Token / 请求占比，并返回按 Token 降序的模块名清单。"""

        statistics = self._ctx.statistics.local
        distribution = await self._fetch_distribution(
            "模块分布",
            lambda: statistics.token_distribution(days=365, group_by="module", top_items=_MAX_HOST_LIMIT),
        )
        if distribution is None:
            return []

        pies = distribution.get("pies", [])
        token_items = _extract_pie_items(pies, 0)
        request_map = {name: value for name, value in _extract_pie_items(pies, 1)}

        grouped_tokens: Dict[str, float] = {}
        grouped_requests: Dict[str, float] = {}
        for module_name, token_value in token_items:
            group_name = resolve_module_group(module_name, self._module_group_map)
            grouped_tokens[group_name] = grouped_tokens.get(group_name, 0.0) + token_value
            grouped_requests[group_name] = grouped_requests.get(group_name, 0.0) + request_map.get(module_name, 0.0)

        self._metrics.pies["module_tokens"] = [
            PieSlice(name=name, value=value) for name, value in ordered_group_items(grouped_tokens)
        ]
        self._metrics.pies["module_requests"] = [
            PieSlice(name=name, value=value) for name, value in ordered_group_items(grouped_requests)
        ]

        unmapped = sorted(
            module_name
            for module_name, _value in token_items
            if resolve_module_group(module_name, self._module_group_map) == UNKNOWN_GROUP_NAME
        )
        if unmapped:
            logger.info("[token_usage_report] 未被模块分组覆盖的模块名（已归入「其他」）：%s", ", ".join(unmapped))

        return [module_name for module_name, _value in sorted(token_items, key=lambda item: -item[1])]

    async def _fill_module_cost(
        self,
        chart_days: int,
        chart_bucket: str,
        chart_granularity: str,
        module_names: List[str],
    ) -> None:
        """逐个模块获取花费序列，生成模块花费分布与趋势。

        宿主没有「按模块聚合花费」的能力，因此这里对 Token 排名前 N 的模块逐个调用
        ``model_trend(metric=cost, module_name=M)``（N 由 ``limits.top_modules`` 控制）。
        """

        statistics = self._ctx.statistics.local
        top_modules = max(1, min(self._config.limits.top_modules, _MAX_HOST_LIMIT))
        selected_modules = module_names[:top_modules]
        if not selected_modules:
            return

        labels: List[str] = []
        module_series: Dict[str, List[float]] = {}
        module_cost_totals: Dict[str, float] = {}
        for module_name in selected_modules:
            series = await self._fetch_series(
                f"模块花费（{module_name}）",
                lambda module_name=module_name: statistics.model_trend(
                    days=chart_days,
                    bucket=chart_bucket,
                    metric="cost",
                    top_models=_MAX_HOST_LIMIT,
                    module_name=module_name,
                ),
            )
            if series is None:
                continue
            merged = filter_and_merge_series(
                [str(item) for item in series.get("timestamps", [])],
                {
                    str(key): [float(item or 0) for item in values]
                    for key, values in series.get("values_by_key", {}).items()
                },
                granularity=chart_granularity,
                cutoff=self._chart_cutoff(),
            )
            if not labels:
                labels = merged.labels
            totals = [sum(merged.series[key][index] for key in merged.series) for index in range(len(merged.labels))]
            display_name = resolve_module_group(module_name, self._module_group_map)
            accumulated = module_series.get(display_name)
            if accumulated is None or len(accumulated) != len(totals):
                accumulated = [0.0] * len(totals)
                module_series[display_name] = accumulated
            for index, value in enumerate(totals):
                accumulated[index] += value
            module_cost_totals[display_name] = module_cost_totals.get(display_name, 0.0) + sum(totals)

        if not labels or not module_series:
            return
        self._metrics.pies["module_cost"] = [
            PieSlice(name=name, value=value) for name, value in ordered_group_items(module_cost_totals)
        ]
        bars = SeriesData(labels=labels, series=module_series, unit_label="¥", value_formatter="cost")
        self._metrics.bars["module_cost"] = limit_series_top(bars, self._config.chart.series_top)

    # ──── 条形图 ────

    def _fill_bars(
        self,
        *,
        chart_granularity: str,
        token_hourly: Optional[Dict[str, Any]],
        token_daily: Optional[Dict[str, Any]],
        cost_hourly: Optional[Dict[str, Any]],
        cost_daily: Optional[Dict[str, Any]],
    ) -> None:
        """填充 Token 与花费相关的条形图数据。"""

        chart_config = self._config.chart
        cutoff = self._chart_cutoff()
        chart_token_series = token_hourly if chart_granularity == "hour" else token_daily
        chart_cost_series = cost_hourly if chart_granularity == "hour" else cost_daily

        if chart_config.bar_tokens and chart_token_series is not None:
            merged = filter_and_merge_series(
                [str(item) for item in chart_token_series.get("timestamps", [])],
                {
                    "Token": [
                        float(item or 0)
                        for item in chart_token_series.get("values_by_key", {}).get("total_tokens", [])
                    ]
                },
                granularity=chart_granularity,
                cutoff=cutoff,
            )
            merged.unit_label = self._config.token_unit.unit_name
            merged.value_formatter = "number"
            self._metrics.bars["tokens"] = merged

        if chart_cost_series is None:
            return
        model_cost_series = _rename_series_values(
            chart_cost_series.get("values_by_key", {}),
            chart_cost_series.get("labels_by_key", {}),
            alias_map=self._alias_map,
        )
        merged = filter_and_merge_series(
            [str(item) for item in chart_cost_series.get("timestamps", [])],
            model_cost_series,
            granularity=chart_granularity,
            cutoff=cutoff,
        )
        total_values = [
            sum(merged.series[key][index] for key in merged.series) for index in range(len(merged.labels))
        ]
        if chart_config.bar_cost:
            self._metrics.bars["cost"] = SeriesData(
                labels=merged.labels,
                series={"花费": total_values},
                unit_label="¥",
                value_formatter="cost",
            )
        if chart_config.bar_model_cost and merged.series:
            model_series = SeriesData(
                labels=merged.labels,
                series={key: list(values) for key, values in merged.series.items()},
                unit_label="¥",
                value_formatter="cost",
            )
            self._metrics.bars["model_cost"] = limit_series_top(model_series, chart_config.series_top)

    def _fill_notes(self) -> None:
        """填充报告脚注（统计口径说明）。"""

        self._metrics.total_scope_note = "最近 365 天"
        self._metrics.notes = [
            "总计口径：最近 365 天（宿主统计能力上限）",
            "消息数 / 回复数只覆盖消息量前 50 会话与调用量前 50 工具",
            f"模块花费只统计 Token 前 {max(1, min(self._config.limits.top_modules, _MAX_HOST_LIMIT))} 个模块，其余归入「其他」",
            "宿主统计为小时级聚合，最新数据最长滞后约 15 分钟",
        ]


def _sum_key(values_by_key: Dict[str, List[float]], key: str) -> float:
    """累加某个序列键的全部数值。"""

    return float(sum(float(item or 0) for item in values_by_key.get(key, [])))


def _sum_all_values(values_by_key: Dict[str, List[float]]) -> float:
    """累加全部序列的所有数值。"""

    return float(sum(_sum_key(values_by_key, key) for key in values_by_key))


def _sum_all_values_in_window(
    timestamps: List[str],
    values_by_key: Dict[str, List[float]],
    predicate: Any,
) -> float:
    """累加窗口内所有序列的数值（用于按模型分组的花费序列）。"""

    total = 0.0
    for values in values_by_key.values():
        total += sum_series_in_window(timestamps, [float(item or 0) for item in values], predicate)
    return total


def _rename_series_values(
    values_by_key: Dict[str, List[float]],
    labels_by_key: Dict[str, str],
    alias_map: Optional[Dict[str, str]] = None,
) -> Dict[str, List[float]]:
    """把序列键替换为可读标签（并应用模型别名）。

    若多个序列映射到同一显示名，则按位置累加，避免数据丢失。
    """

    renamed: Dict[str, List[float]] = {}
    for key, values in values_by_key.items():
        label = str(labels_by_key.get(key, key))
        if alias_map:
            label = alias_map.get(label, label)
        normalized_values = [float(item or 0) for item in values]
        accumulated = renamed.get(label)
        if accumulated is None or len(accumulated) != len(normalized_values):
            renamed[label] = normalized_values
            continue
        for index, value in enumerate(normalized_values):
            accumulated[index] += value
    return renamed


def _extract_pie_items(pies: Any, index: int) -> List[Tuple[str, float]]:
    """从 distribution.pies 中提取指定饼图的数据项。"""

    if not isinstance(pies, list) or index >= len(pies):
        return []
    pie = pies[index]
    if not isinstance(pie, dict):
        return []
    items: List[Tuple[str, float]] = []
    for item in pie.get("data", []):
        if not isinstance(item, dict):
            continue
        items.append((str(item.get("name") or UNKNOWN_GROUP_NAME), float(item.get("value") or 0)))
    return items


def _index_to_letter(index: int) -> str:
    """把序号转换为 A、B、…、Z、A2 形式的匿名标签。"""

    if index < 26:
        return chr(ord("A") + index)
    return f"A{index - 25}"