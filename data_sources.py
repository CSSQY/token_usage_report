"""全局视图数据层：调用宿主 statistics 能力并归并成报告指标。

本模块只使用宿主通过 ``ctx`` 暴露的能力，不直接访问数据库文件，
也不导入任何 ``src.*`` 模块。
"""

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

import logging
import math

from .config_model import TokenUsageReportConfig, build_model_alias_map, build_module_group_map
from .metrics import (
    CHAT_GROUP_NAMES,
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
    parse_timestamp,
    resolve_module_group,
    sum_series_in_window,
)

logger = logging.getLogger(__name__)

_REPLY_TOOL_NAME = "reply"
_MAX_HOST_LIMIT = 50

_HOURLY_SPAN_LIMIT_HOURS = 168.0
"""单窗口模式下仍用小时桶取数的窗口跨度上限（7 天）。

超过该跨度改用天桶：数据量可控，且「今日 / 本周 / 本月」这类以零点为起点的窗口
用天桶仍然精确，只有「最近 N×24 小时」这类滚动窗口的首个半天会不计入。
"""

_CALENDAR_WINDOW_KEYS = ("today", "this_week", "this_month")
"""按本地日历零点起算的窗口；其余窗口按滚动时长回算。"""

_WINDOW_DEFINITION_NOTE = (
    "「今日 / 本周 / 本月」按本地日历零点起算（今日 = 今天 00:00 至今，不是最近 24 小时）；"
    "「最近 24 / 7×24 / 30×24 小时」按滚动时长回算，两者不可直接比较"
)

_AGGREGATION_LAG_NOTE = "最近 15 分钟内的调用可能还没进表"

_TOTAL_DEFINITION_NOTE = (
    "总计 = 供应商上报的 total_tokens 之和；部分供应商把缓存 / 推理用量也算进 total，"
    "所以总计可能略大于「输入 + 输出」（宿主 WebUI 的合计用的是输入 + 输出，两边天然有零点几个百分点的差）"
)

_ONLINE_TIME_TABLE = "OnlineTime"
"""宿主在线时长明细表（实际表名 ``online_time``）。

宿主还提供 ``statistics.local.online_time_trend`` 能力，但它返回的是
``SUM(duration_minutes)``，而该字段只在建记录时写死为 5、之后心跳只更新
``end_timestamp``，既算不准时长，又按 ``start_timestamp`` 分桶（跨越窗口起点的
记录会被整条裁掉）。因此这里直读明细，按上下线时间差自行计算。
"""

_ONLINE_TIME_ROW_LIMIT = 20000
"""在线时长明细的读取上限；行数与「有记录的在线时段数」同阶，正常远低于该值。"""


def _chat_scope_note(unit_name: str) -> str:
    """聊天链路口径说明：总量含后台流水线，核对「实际用了多少」请看聊天链路那一格。

    Args:
        unit_name: 配置的用量单位名，用于替换文案里的「Token」。

    Returns:
        str: 脚注文本。
    """

    return (
        "总量包含全部模块：聊天链路（计划器 + 回复器）之外的记忆抽取、embedding、图片理解、"
        f"表情向量等后台流水线同样计入 {unit_name}，核对时请以「聊天链路 {unit_name}」为准"
    )


def _module_granularity_note(top_modules: int, unit_name: str) -> str:
    """模块口径说明：用量与花费用的粒度不同，且花费只覆盖前 N 个模块。

    Args:
        top_modules: 模块花费统计覆盖的模块个数。
        unit_name: 配置的用量单位名，用于替换文案里的「Token」。

    Returns:
        str: 脚注文本。
    """

    return (
        f"模块 {unit_name} 占比按完整请求类型（request_type，如 maisaka.replyer）归类；"
        "模块花费受宿主能力限制只能按 module_name（首个「.」之前的部分）归类，"
        "粒度更粗——maisaka.planner / maisaka.replyer / maisaka.mid_term_memory 在花费里会合并成 maisaka；"
        f"模块花费只统计 {unit_name} 前 {top_modules} 个模块，其余归入「其他」"
    )


def _window_definition(window_key: str) -> str:
    """返回窗口的口径说明（日历零点起算 / 滚动时长）。"""

    return "本地日历口径，零点起算" if window_key in _CALENDAR_WINDOW_KEYS else "滚动口径，按当前时间回算时长"


async def collect_global_metrics(
    ctx: Any,
    config: TokenUsageReportConfig,
    window_key: str = "",
) -> ReportMetrics:
    """采集全局（全部会话）统计指标。

    Args:
        ctx: 插件运行时上下文。
        config: 插件配置。
        window_key: 只统计单个时间窗口时传入窗口 key（``today`` / ``this_week`` 等）；
            留空表示统计全部窗口。

    Returns:
        ReportMetrics: 全局指标快照；单项能力失败时对应区块标记为不可用。
    """

    collector = _GlobalCollector(ctx=ctx, config=config, window_key=window_key)
    return await collector.collect()


class _GlobalCollector:
    """全局指标采集器。

    未指定 ``window_key`` 时按「6 个时间窗口 + 最近 365 天总计」采集；
    指定 ``window_key`` 时进入**单窗口模式**：只取覆盖该窗口所需的取数档位，
    并把每条时间序列裁剪到窗口起点之后，因此窗口卡片、趋势图、模型排行、
    占比分布全部只包含该窗口的数据，报告内部口径完全一致。
    """

    def __init__(self, ctx: Any, config: TokenUsageReportConfig, window_key: str = "") -> None:
        self._ctx = ctx
        self._config = config
        self._now = datetime.now()
        self._alias_map = build_model_alias_map(config.model_aliases.aliases)
        self._module_group_map = build_module_group_map(config.module_groups)
        self._predicates = build_window_predicates(self._now)
        self._window_key = window_key if window_key in self._predicates else ""
        self._window = self._predicates[self._window_key] if self._window_key else None
        self._raw_module_tokens: Dict[str, float] = {}
        self._resolve_tiers()
        if self._window is None:
            self._windows: Dict[str, WindowMetrics] = {
                key: WindowMetrics(key=key, label=WINDOW_LABELS[key]) for key in WINDOW_ORDER
            }
            scope_name = build_scope_name("all")
        else:
            # 单窗口模式只保留所选窗口，避免把「被裁剪过的数据」误当成其他窗口的统计
            label = WINDOW_LABELS[self._window_key]
            self._windows = {self._window_key: WindowMetrics(key=self._window_key, label=label)}
            scope_name = f"{build_scope_name('all')} · {label}"
        self._total_window = WindowMetrics(key="total", label="总计")
        self._metrics = ReportMetrics(
            scope="all",
            scope_name=scope_name,
            generated_at=self._now,
            unit_name=config.token_unit.unit_name,
            window_scoped=self._window is not None,
        )

    def _resolve_tiers(self) -> None:
        """解析取数档位（天数 / 桶粒度）与条形图颗粒度。

        普通模式：窗口用「小时 + 32 天」，总计用「天 + 365 天」。
        单窗口模式：两级共用一套档位，且只为覆盖窗口所需的跨度取数。
        """

        if self._window is None:
            self._window_days, self._window_bucket = 32, "hour"
            self._total_days, self._total_bucket = 365, "day"
            self._chart_granularity = self._config.chart.bar_granularity
            return

        span_hours = max((self._now - self._window.start).total_seconds() / 3600.0, 1.0)
        bucket = "hour" if span_hours <= _HOURLY_SPAN_LIMIT_HOURS else "day"
        # 天数要覆盖整个窗口，并留出一点余量，保证裁剪前的序列已经包含窗口起点
        days = int(math.ceil(span_hours / 24.0)) + (1 if bucket == "hour" else 2)
        self._window_days = min(max(days, 1), 365)
        self._window_bucket = bucket
        self._total_days, self._total_bucket = self._window_days, bucket
        self._chart_granularity = self._resolve_window_granularity(span_hours)

    def _resolve_window_granularity(self, span_hours: float) -> str:
        """单窗口模式下条形图的颗粒度：短窗口按小时，长窗口按天（尊重用户配置的周/月）。"""

        configured = self._config.chart.bar_granularity
        if configured in {"week", "month"}:
            return configured
        return "hour" if span_hours <= 48.0 else "day"

    async def collect(self) -> ReportMetrics:
        """执行完整的全局采集流程。"""

        statistics = self._ctx.statistics.local
        unit_name = self._config.token_unit.unit_name
        chart_granularity = self._chart_granularity
        chart_days, chart_bucket = self._resolve_chart_range(chart_granularity)
        self._metrics.chart_granularity = chart_granularity

        token_window = await self._fetch_series(
            f"{unit_name} 趋势（小时）",
            lambda: statistics.token_trend(days=self._window_days, bucket=self._window_bucket),
        )
        cost_window = await self._fetch_series(
            "花费趋势（小时）",
            lambda: statistics.model_trend(
                days=self._window_days,
                bucket=self._window_bucket,
                metric="cost",
                top_models=_MAX_HOST_LIMIT,
            ),
        )
        if self._window is None:
            token_total = await self._fetch_series(
                f"{unit_name} 趋势（天）",
                lambda: statistics.token_trend(days=self._total_days, bucket=self._total_bucket),
            )
            cost_total = await self._fetch_series(
                "花费趋势（天）",
                lambda: statistics.model_trend(
                    days=self._total_days,
                    bucket=self._total_bucket,
                    metric="cost",
                    top_models=_MAX_HOST_LIMIT,
                ),
            )
        else:
            # 单窗口模式下窗口与总计是同一份数据（都已裁剪到窗口内），无需重复取数
            token_total, cost_total = token_window, cost_window

        self._fill_token_windows(token_window)
        self._fill_cost_windows(cost_window)
        self._fill_total(token_total, cost_total)
        if self._window is None:
            await self._fill_models()
        else:
            await self._fill_window_models(cost_series=cost_window)
        await self._fill_messages(chart_days, chart_bucket, chart_granularity)
        await self._fill_replies()
        await self._fill_online_time()
        module_names = await self._fill_module_distribution()
        await self._fill_module_series(
            chart_days=chart_days,
            chart_bucket=chart_bucket,
            chart_granularity=chart_granularity,
            module_names=module_names,
        )
        self._fill_chat_tokens()
        self._fill_bars(
            chart_granularity=chart_granularity,
            token_hourly=token_window,
            token_daily=token_total,
            cost_hourly=cost_window,
            cost_daily=cost_total,
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

        if self._window is not None:
            return self._window_days, self._window_bucket
        if granularity == "hour":
            return 32, "hour"
        return 365, "day"

    def _chart_cutoff(self) -> datetime:
        """返回条形图横轴起点（单窗口模式下即窗口起点）。"""

        if self._window is not None:
            return self._window.start
        return self._now - timedelta(days=max(int(self._config.chart.bar_days), 1))

    def _clip_to_window(self, series: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """把序列裁剪到窗口起点之后（仅单窗口模式生效）。

        宿主的统计能力只能按「最近 N 天」取数，无法直接指定起止时间，
        因此这里在取数后按时间戳裁剪，保证窗口外的数据不会进入报告。

        Args:
            series: 能力返回的 series 结构。

        Returns:
            Optional[Dict[str, Any]]: 裁剪后的 series 结构。
        """

        if series is None or self._window is None:
            return series
        timestamps = [str(item) for item in series.get("timestamps", [])]
        kept_indexes = [
            index
            for index, raw_timestamp in enumerate(timestamps)
            if (parsed := parse_timestamp(raw_timestamp)) is not None and parsed >= self._window.start
        ]
        if len(kept_indexes) == len(timestamps):
            return series
        clipped_values: Dict[str, List[float]] = {}
        for key, values in series.get("values_by_key", {}).items():
            normalized = [float(item or 0) for item in values]
            clipped_values[key] = [
                normalized[index] if index < len(normalized) else 0.0 for index in kept_indexes
            ]
        return {
            **series,
            "timestamps": [timestamps[index] for index in kept_indexes],
            "values_by_key": clipped_values,
        }

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

        单窗口模式下返回值会被裁剪到窗口起点之后，因此调用方的窗口累加、
        图表归并、模型聚合都会自动只覆盖所选窗口。

        Args:
            label: 区块名称，用于日志与「数据不可用」提示。
            call: 能力调用闭包。

        Returns:
            Optional[Dict[str, Any]]: series 结构；失败时返回 None。
        """

        try:
            return self._clip_to_window(self._as_series(await call()))
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
        """填充模型排行、模型占比与加权平均响应（全时段口径）。

        多个内部模型名映射到同一别名时会**合并成一行**：调用次数、Token、费用分别求和，
        平均耗时按调用次数加权平均，别名冲突的内部名会记录一条 info 日志便于核对。
        """

        statistics = self._ctx.statistics.local
        rows = await self._fetch_list("模型统计", lambda: statistics.models(days=365, limit=_MAX_HOST_LIMIT), "models")
        if rows is None:
            self._metrics.models = []
            return
        aggregated: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            self._accumulate_model(
                aggregated,
                internal_name=str(row.get("model_name") or "未知模型"),
                requests=int(row.get("request_count") or 0),
                tokens=int(row.get("total_tokens") or 0),
                cost=float(row.get("total_cost") or 0.0),
                latency_sum=float(row.get("avg_response_time") or 0.0) * int(row.get("request_count") or 0),
            )
        self._finalize_models(aggregated)

    async def _fill_window_models(self, cost_series: Optional[Dict[str, Any]]) -> None:
        """单窗口模式的模型排行：用按模型的序列在窗口内累加。

        宿主``statistics.models`` 只能按「最近 N 天」取数，无法裁剪到窗口，
        因此这里改用 ``model_trend`` 的 Token / 费用 / 次数 / 耗时四条序列，
        它们都已裁剪到窗口起点之后，累加结果与窗口卡片口径一致。
        """

        statistics = self._ctx.statistics.local
        days, bucket = self._total_days, self._total_bucket
        token_series = await self._fetch_series(
            f"模型 {self._config.token_unit.unit_name} 趋势",
            lambda: statistics.model_trend(days=days, bucket=bucket, metric="token", top_models=_MAX_HOST_LIMIT),
        )
        request_series = await self._fetch_series(
            "模型调用次数趋势",
            lambda: statistics.model_trend(days=days, bucket=bucket, metric="request", top_models=_MAX_HOST_LIMIT),
        )
        latency_series = await self._fetch_series(
            "模型耗时趋势",
            lambda: statistics.model_trend(days=days, bucket=bucket, metric="latency", top_models=_MAX_HOST_LIMIT),
        )
        if token_series is None:
            self._metrics.models = []
            return

        requests_by_model = _sum_series_by_label(request_series)
        costs_by_model = _sum_series_by_label(cost_series)
        latency_sum_by_model = _weighted_latency(latency_series, request_series)

        aggregated: Dict[str, Dict[str, Any]] = {}
        for internal_name, tokens in _sum_series_by_label(token_series).items():
            requests = int(requests_by_model.get(internal_name, 0))
            self._accumulate_model(
                aggregated,
                internal_name=internal_name,
                requests=requests,
                tokens=int(tokens),
                cost=costs_by_model.get(internal_name, 0.0),
                latency_sum=latency_sum_by_model.get(internal_name, 0.0),
            )
        self._finalize_models(aggregated)

    def _accumulate_model(
        self,
        aggregated: Dict[str, Dict[str, Any]],
        *,
        internal_name: str,
        requests: int,
        tokens: int,
        cost: float,
        latency_sum: float,
    ) -> None:
        """把单个内部模型的用量累加到显示别名对应的桶中。"""

        display_name = self._alias_map.get(internal_name, internal_name)
        bucket = aggregated.setdefault(
            display_name,
            {"requests": 0, "tokens": 0, "cost": 0.0, "latency_sum": 0.0, "internal_names": []},
        )
        bucket["requests"] += requests
        bucket["tokens"] += tokens
        bucket["cost"] += cost
        bucket["latency_sum"] += latency_sum
        if internal_name not in bucket["internal_names"]:
            bucket["internal_names"].append(internal_name)

    def _finalize_models(self, aggregated: Dict[str, Dict[str, Any]]) -> None:
        """把「显示别名 → 累加桶」整理成模型排行、占比与整体平均响应。"""

        merged_models: List[ModelUsageRow] = []
        total_requests = 0
        total_latency_sum = 0.0
        for display_name, bucket in aggregated.items():
            internal_names = bucket["internal_names"]
            if len(internal_names) > 1:
                logger.info(
                    "[token_usage_report] 以下模型已按别名合并为「%s」：%s",
                    display_name,
                    " / ".join(internal_names),
                )
            request_count = int(bucket["requests"])
            merged_models.append(
                ModelUsageRow(
                    name=display_name,
                    internal_name="=".join(internal_names),
                    requests=request_count,
                    tokens=int(bucket["tokens"]),
                    cost=float(bucket["cost"]),
                    avg_response=float(bucket["latency_sum"]) / request_count if request_count > 0 else None,
                )
            )
            total_requests += request_count
            total_latency_sum += float(bucket["latency_sum"])

        merged_models.sort(key=lambda item: (-item.tokens, item.name))
        self._metrics.models = merged_models[: max(1, min(self._config.limits.top_models, _MAX_HOST_LIMIT))]
        # 占比图使用合并后的完整清单（不受排行显示条数限制），避免小模型被截断而占比之和不等于 100%
        self._metrics.pies["model_tokens"] = [
            PieSlice(name=item.name, value=float(item.tokens)) for item in merged_models if item.tokens > 0
        ]
        self._metrics.pies["model_cost"] = [
            PieSlice(name=item.name, value=item.cost) for item in merged_models if item.cost > 0
        ]
        self._metrics.pies["model_requests"] = [
            PieSlice(name=item.name, value=float(item.requests)) for item in merged_models if item.requests > 0
        ]
        if total_requests > 0:
            self._total_window.avg_response = total_latency_sum / total_requests

    async def _fill_messages(self, chart_days: int, chart_bucket: str, chart_granularity: str) -> None:
        """填充消息数、聊天消息分布与聊天流消息序列。"""

        statistics = self._ctx.statistics.local
        distribution_series = await self._fetch_series(
            "消息趋势",
            lambda: statistics.message_trend(
                days=self._total_days, bucket=self._total_bucket, top_chats=_MAX_HOST_LIMIT
            ),
        )
        if distribution_series is None:
            return

        values_by_key = distribution_series.get("values_by_key", {})
        labels_by_key = distribution_series.get("labels_by_key", {})
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

        if chart_bucket == self._total_bucket:
            # 图表与分布同档位（含单窗口模式），直接复用同一份序列
            chart_series = distribution_series
        else:
            chart_series = await self._fetch_series(
                "消息趋势（图表）",
                lambda: statistics.message_trend(days=chart_days, bucket=chart_bucket, top_chats=_MAX_HOST_LIMIT),
            )
        if chart_series is None:
            return
        chart_labels_by_key = chart_series.get("labels_by_key", {})
        merged = filter_and_merge_series(
            [str(item) for item in chart_series.get("timestamps", [])],
            _rename_series_values(chart_series.get("values_by_key", {}), chart_labels_by_key),
            granularity=chart_granularity,
            cutoff=self._chart_cutoff(),
        )
        merged.unit_label = "条"
        merged.value_formatter = "number"
        # 分布与趋势图可能是两次不同范围的取数，前 N 名名单不一定重合：
        # 把两边的聊天对象名都纳入映射，避免漏改后在图片图例里泄露真名
        name_map = self._chat_display_name_map(
            chat_rows,
            stream_types,
            extra_names=[str(label) for label in chart_labels_by_key.values()],
        )
        self._metrics.pies["chat_messages"] = [
            PieSlice(name=name_map.get(row.name, row.name), value=float(row.messages)) for row in chat_rows
        ]
        self._metrics.bars["chat_messages"] = limit_series_top(
            _rename_series_names(merged, name_map), self._config.chart.series_top
        )

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

    def _chat_display_name_map(
        self,
        chat_rows: List[ChatMessageRow],
        stream_types: Dict[str, bool],
        extra_names: Sequence[str] = (),
    ) -> Dict[str, str]:
        """生成「聊天对象真名 → 展示名」映射（开启匿名化时改为 群聊A / 个人用户A 等）。

        会话类型优先取自聊天流列表（宿主聚合表不含该字段），缺失时用分布里的判定结果；
        仍然拿不到类型的按「会话X」处理。匿名化关闭时返回空映射，调用方按原名展示。

        Args:
            chat_rows: 按消息量降序排列的聊天对象明细。
            stream_types: 「聊天对象名 → 是否群聊」映射。
            extra_names: 额外需要覆盖的名字（例如只出现在趋势图取数范围内的聊天对象）。

        Returns:
            Dict[str, str]: 真名到展示名的映射；未开启匿名化时为空字典。
        """

        if not self._config.render.anonymize:
            return {}

        type_by_name: Dict[str, bool] = dict(stream_types)
        for row in chat_rows:
            if row.is_group is not None:
                type_by_name[row.name] = row.is_group

        ordered_names = [row.name for row in chat_rows]
        known_names = set(ordered_names)
        ordered_names.extend(sorted(name for name in extra_names if name not in known_names))

        name_map: Dict[str, str] = {}
        group_index = 0
        private_index = 0
        for name in ordered_names:
            is_group = type_by_name.get(name)
            if is_group is True:
                name_map[name] = f"群聊{_index_to_letter(group_index)}"
                group_index += 1
            elif is_group is False:
                name_map[name] = f"个人用户{_index_to_letter(private_index)}"
                private_index += 1
            else:
                name_map[name] = f"会话{_index_to_letter(len(name_map))}"
        return name_map

    async def _fill_replies(self) -> None:
        """填充回复数（宿主内置统计口径：``reply`` 工具调用次数）。"""

        statistics = self._ctx.statistics.local
        series = await self._fetch_series(
            "工具调用趋势",
            lambda: statistics.tool_trend(
                days=self._total_days, bucket=self._total_bucket, top_tools=_MAX_HOST_LIMIT
            ),
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
        """填充在线时长（小时）。

        改直读宿主 ``online_time`` 明细，按每条记录的 ``end - start`` 与统计区间求交集后累加，
        与宿主 WebUI 的算法一致（[statistics_service.py] 里同样是 ``max(start, 区间起点)`` /
        ``min(end, 区间终点)``）。宿主的 ``online_time_trend`` 能力用不了：
        ``SUM(duration_minutes)`` 因该字段永不更新而失真，且按 ``start_timestamp`` 分桶，
        会导致「跨越窗口起点的那条记录」整条被裁掉——这正是长跑进程在「今日」窗口显示 0 秒的原因。
        """

        rows = await self._load_online_time_rows()
        if rows is None:
            return
        start, end = self._online_time_range()
        self._metrics.online_hours = _sum_online_hours(rows, start, end)

    def _online_time_range(self) -> Tuple[datetime, datetime]:
        """返回在线时长的统计区间：单窗口模式为窗口起点至今，否则与总计口径一致（最近 N 天）。"""

        if self._window is not None:
            return self._window.start, self._now
        return self._now - timedelta(days=self._total_days), self._now

    async def _load_online_time_rows(self) -> Optional[List[Dict[str, Any]]]:
        """读取宿主在线时长明细；失败或结构异常时标记区块不可用。

        ``database.get`` 只支持等值过滤，拿不到「区间内」的行，因此按 ``end_timestamp``
        倒序取最近若干条后在插件内裁剪；行数与「有记录的在线时段数」同阶
        （持续在线时复用同一条记录、只更新结束时间），不会爆量。
        """

        try:
            rows = await self._ctx.db.get(
                _ONLINE_TIME_TABLE,
                order_by=["-end_timestamp"],
                limit=_ONLINE_TIME_ROW_LIMIT,
            )
        except Exception as exc:
            logger.error("[token_usage_report] 读取在线时长明细失败: %s", exc)
            self._metrics.unavailable.append("在线时长")
            return None
        if not isinstance(rows, list):
            logger.error("[token_usage_report] 在线时长明细返回结构异常：%s", type(rows).__name__)
            self._metrics.unavailable.append("在线时长")
            return None
        if len(rows) >= _ONLINE_TIME_ROW_LIMIT:
            logger.warning(
                "[token_usage_report] 在线时长明细达到读取上限 %d 条，更早的在线时段可能未计入",
                _ONLINE_TIME_ROW_LIMIT,
            )
        return [row for row in rows if isinstance(row, dict)]

    async def _fill_module_distribution(self) -> List[str]:
        """填充模块占比，并返回供「模块花费」取数用的 ``module_name`` 清单。

        宿主聚合表有两个粒度，必须分别使用：

        - ``request_type``（完整请求类型，如 ``maisaka.replyer``）：Token 与请求次数按它归类。
          宿主的 ``module_name`` 只保留首个 ``.`` 之前的部分，会把 ``maisaka.planner`` /
          ``maisaka.replyer`` 一并压成 ``maisaka``，与 ``maisaka.mid_term_memory`` 无法区分，
          导致「聊天链路」被算进「记忆」；只有完整 ``request_type`` 才能正确归类。
        - ``module_name``（首个 ``.`` 之前）：宿主 ``model_trend`` 只支持按它过滤花费，
          所以「模块花费」沿用这个粒度，返回值即该粒度的清单。

        单窗口模式下 ``token_distribution`` 只能按「最近 N 天」取数、无法裁剪到窗口，
        因此这里只借用它的清单，Token 明细改由带时间戳的按请求类型序列统计
        （见 :meth:`_fill_module_series`）；模块占比饼图统一在那边生成。
        """

        statistics = self._ctx.statistics.local
        type_distribution = await self._fetch_distribution(
            "模块分布",
            lambda: statistics.token_distribution(
                days=self._total_days, group_by="type", top_items=_MAX_HOST_LIMIT
            ),
        )
        module_distribution = await self._fetch_distribution(
            "模块花费清单",
            lambda: statistics.token_distribution(
                days=self._total_days, group_by="module", top_items=_MAX_HOST_LIMIT
            ),
        )

        if type_distribution is not None:
            pies = type_distribution.get("pies", [])
            token_items = _extract_pie_items(pies, 0)
            request_map = {name: value for name, value in _extract_pie_items(pies, 1)}
            self._raw_module_tokens = {request_type: token_value for request_type, token_value in token_items}

            if self._window is None:
                grouped_requests: Dict[str, float] = {}
                for request_type, _token_value in token_items:
                    group_name = resolve_module_group(request_type, self._module_group_map)
                    grouped_requests[group_name] = grouped_requests.get(group_name, 0.0) + request_map.get(
                        request_type, 0.0
                    )

                self._metrics.pies["module_requests"] = [
                    PieSlice(name=name, value=value) for name, value in ordered_group_items(grouped_requests)
                ]

            unmapped = sorted(
                request_type
                for request_type, _value in token_items
                if resolve_module_group(request_type, self._module_group_map) == UNKNOWN_GROUP_NAME
            )
            if unmapped:
                logger.info(
                    "[token_usage_report] 未被模块分组覆盖的请求类型（已归入「其他」）：%s", ", ".join(unmapped)
                )

        if module_distribution is None:
            return []
        module_items = _extract_pie_items(module_distribution.get("pies", []), 0)
        return [module_name for module_name, _value in sorted(module_items, key=lambda item: -item[1])]

    async def _fill_module_series(
        self,
        *,
        chart_days: int,
        chart_bucket: str,
        chart_granularity: str,
        module_names: List[str],
    ) -> None:
        """逐个模块获取花费序列（单窗口模式下同时获取 Token 序列）。

        宿主没有「按模块聚合」的时间序列能力，因此这里对 Token 排名前 N 的模块逐个调用
        ``model_trend(module_name=M)``（N 由 ``limits.top_modules`` 控制）。
        传入的 ``module_names`` 是 ``module_name``（首个「.」之前）粒度——宿主花费能力只支持该粒度。
        单窗口模式额外取一份 Token 序列，让模块 Token 占比也收敛到窗口内。

        Args:
            chart_days: 条形图取数天数。
            chart_bucket: 条形图取数桶粒度。
            chart_granularity: 条形图颗粒度。
            module_names: 按 Token 降序的 ``module_name`` 清单。
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

        if self._window is not None:
            # 单窗口模式：用带时间戳的「按请求类型 Token 序列」精确统计窗口内各模块，
            # 一次调用覆盖全部请求类型（不受 limits.top_modules 限制），同时避免逐模块取数
            series = await self._fetch_series(
                f"模块 {self._config.token_unit.unit_name} 趋势",
                lambda: statistics.token_trend(
                    days=self._total_days,
                    bucket=self._total_bucket,
                    group_by="type",
                    top_items=_MAX_HOST_LIMIT,
                ),
            )
            if series is not None:
                self._raw_module_tokens = _sum_series_by_label(series)

        if self._raw_module_tokens:
            grouped = _group_module_tokens(self._raw_module_tokens, self._module_group_map)
            self._metrics.pies["module_tokens"] = [
                PieSlice(name=name, value=value) for name, value in ordered_group_items(grouped)
            ]
        if not labels or not module_series:
            return
        self._metrics.pies["module_cost"] = [
            PieSlice(name=name, value=value) for name, value in ordered_group_items(module_cost_totals)
        ]
        bars = SeriesData(labels=labels, series=module_series, unit_label="¥", value_formatter="cost")
        self._metrics.bars["module_cost"] = limit_series_top(bars, self._config.chart.series_top)

    def _fill_chat_tokens(self) -> None:
        """填充聊天链路（计划器 + 回复器）的 Token 合计。

        报告的总量包含记忆抽取、embedding、视觉理解等后台流水线，它们不随聊天量变化，
        单独标出聊天链路才能和「我到底聊了多少」的直觉对上。
        """

        grouped = _group_module_tokens(self._raw_module_tokens, self._module_group_map)
        if not grouped:
            return
        chat_total = sum(value for name, value in grouped.items() if name in CHAT_GROUP_NAMES)
        self._metrics.chat_tokens = int(chat_total)

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
                    self._config.token_unit.unit_name: [
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

        top_modules = max(1, min(self._config.limits.top_modules, _MAX_HOST_LIMIT))
        unit_name = self._config.token_unit.unit_name
        if self._window is None:
            self._metrics.total_scope_note = "最近 365 天"
            self._metrics.notes = [
                "总计口径：最近 365 天（宿主统计能力上限）",
                f"窗口口径：{_WINDOW_DEFINITION_NOTE}",
                _chat_scope_note(unit_name),
                _TOTAL_DEFINITION_NOTE,
                _module_granularity_note(top_modules, unit_name),
                "消息数 / 回复数只覆盖消息量前 50 会话与调用量前 50 工具",
                f"数据来自宿主的按小时聚合表，宿主每 15 分钟刷新一次：{_AGGREGATION_LAG_NOTE}",
            ]
            return

        label = WINDOW_LABELS[self._window_key]
        self._metrics.total_scope_note = label
        notes = [
            f"本次报告只统计「{label}」（{_window_definition(self._window_key)}）："
            "时间窗口、趋势图、模型排行与占比分布均为该窗口数据",
            _chat_scope_note(unit_name),
            _TOTAL_DEFINITION_NOTE,
            _module_granularity_note(top_modules, unit_name),
            "消息数 / 回复数只覆盖消息量前 50 会话与调用量前 50 工具",
            f"数据来自宿主的按小时聚合表，宿主每 15 分钟刷新一次：{_AGGREGATION_LAG_NOTE}",
        ]
        if self._window_bucket == "day":
            notes.append("该窗口跨度较长，按天聚合，最早不足一天的部分不计入")
        self._metrics.notes = notes


def _rename_series_names(series_data: SeriesData, name_map: Dict[str, str]) -> SeriesData:
    """按「真名 → 展示名」重命名多序列图里的序列名。

    用于聊天对象匿名化：趋势图图例与扇形图必须用同一套展示名，否则真名会从
    图例里泄露出去。多个真名映射到同一展示名时按位置累加。

    Args:
        series_data: 原始多序列数据。
        name_map: 真名到展示名的映射；为空时原样返回。

    Returns:
        SeriesData: 重命名后的多序列数据。
    """

    if not name_map:
        return series_data
    renamed: Dict[str, List[float]] = {}
    for name, values in series_data.series.items():
        display_name = name_map.get(name, name)
        accumulated = renamed.get(display_name)
        if accumulated is None:
            renamed[display_name] = list(values)
            continue
        for index, value in enumerate(values):
            if index < len(accumulated):
                accumulated[index] += float(value or 0)
    return SeriesData(
        labels=list(series_data.labels),
        series=renamed,
        unit_label=series_data.unit_label,
        value_formatter=series_data.value_formatter,
    )


def _sum_key(values_by_key: Dict[str, List[float]], key: str) -> float:
    """累加某个序列键的全部数值。"""

    return float(sum(float(item or 0) for item in values_by_key.get(key, [])))


def _sum_online_hours(rows: Sequence[Dict[str, Any]], start: datetime, end: datetime) -> float:
    """把在线时长明细累加成小时数（只计入与 ``[start, end]`` 的交集）。

    每条记录取 ``max(起始, 区间起点)`` 与 ``min(结束, 区间终点)``，只有结束晚于开始时才计入，
    这样跨越区间起点的长记录能正确算出「区间内的那一段」，与宿主 WebUI 的口径一致。

    Args:
        rows: ``OnlineTime`` 明细行。
        start: 统计区间起点。
        end: 统计区间终点。

    Returns:
        float: 区间内的在线小时数。
    """

    total_seconds = 0.0
    for row in rows:
        record_start = parse_timestamp(row.get("start_timestamp"))
        if record_start is None:
            # 历史记录可能没有上线时间，与宿主 SQL 的 COALESCE(start_timestamp, timestamp) 保持一致
            record_start = parse_timestamp(row.get("timestamp"))
        record_end = parse_timestamp(row.get("end_timestamp"))
        if record_start is None or record_end is None:
            logger.warning("[token_usage_report] 在线时长记录缺少起止时间，已跳过：%s", row)
            continue
        overlap_start = max(record_start, start)
        overlap_end = min(record_end, end)
        if overlap_end > overlap_start:
            total_seconds += (overlap_end - overlap_start).total_seconds()
    return total_seconds / 3600.0


def _group_module_tokens(
    raw_module_tokens: Dict[str, float],
    group_map: Dict[str, str],
) -> Dict[str, float]:
    """把「原始模块名 → Token」按配置的模块分组汇总。"""

    grouped: Dict[str, float] = {}
    for module_name, tokens in raw_module_tokens.items():
        group_name = resolve_module_group(module_name, group_map)
        grouped[group_name] = grouped.get(group_name, 0.0) + float(tokens or 0)
    return grouped


def _sum_series_by_label(series: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """把 series 结构按序列标签累加成「标签 → 合计值」。

    宿主给每个序列名做了安全化处理，这里用 ``labels_by_key`` 还原成可读名称。

    Args:
        series: 能力返回的 series 结构；为空时返回空字典。

    Returns:
        Dict[str, float]: 标签到合计值的映射。
    """

    if series is None:
        return {}
    labels_by_key = series.get("labels_by_key", {})
    totals: Dict[str, float] = {}
    for key, values in series.get("values_by_key", {}).items():
        label = str(labels_by_key.get(key, key))
        totals[label] = totals.get(label, 0.0) + sum(float(item or 0) for item in values)
    return totals


def _weighted_latency(
    latency_series: Optional[Dict[str, Any]],
    request_series: Optional[Dict[str, Any]],
) -> Dict[str, float]:
    """把「每桶平均耗时」按桶内调用次数加权，得到各模型的耗时总和。

    宿主 ``model_trend(metric="latency")`` 每个桶返回的是该桶的平均耗时，
    直接相加没有意义，必须乘以同一桶的调用次数才能还原整体平均。

    Args:
        latency_series: 耗时序列。
        request_series: 同一档位的调用次数序列，用于取权重。

    Returns:
        Dict[str, float]: 标签到「平均耗时 × 调用次数」合计的映射。
    """

    if latency_series is None:
        return {}
    request_by_label = _series_values_by_label(request_series)
    latency_labels = latency_series.get("labels_by_key", {})
    totals: Dict[str, float] = {}
    for key, values in latency_series.get("values_by_key", {}).items():
        label = str(latency_labels.get(key, key))
        requests = request_by_label.get(label, [])
        weighted = 0.0
        for index, value in enumerate(values):
            count = float(requests[index]) if index < len(requests) else 0.0
            weighted += float(value or 0) * count
        totals[label] = totals.get(label, 0.0) + weighted
    return totals


def _series_values_by_label(series: Optional[Dict[str, Any]]) -> Dict[str, List[float]]:
    """把 series 结构转成「标签 → 数值列表」，便于按标签对齐两条序列。"""

    if series is None:
        return {}
    labels_by_key = series.get("labels_by_key", {})
    return {
        str(labels_by_key.get(key, key)): [float(item or 0) for item in values]
        for key, values in series.get("values_by_key", {}).items()
    }


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