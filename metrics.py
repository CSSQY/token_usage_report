"""统计指标数据模型、派生指标与中文单位格式化。

本模块不依赖任何宿主能力，只做纯数据处理，便于单元自测与复用。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

WINDOW_ORDER: Tuple[str, ...] = ("today", "this_week", "this_month", "last_24h", "last_7d", "last_30d")

WINDOW_LABELS: Dict[str, str] = {
    "today": "今日",
    "this_week": "本周",
    "this_month": "本月",
    "last_24h": "最近24小时",
    "last_7d": "最近7×24小时",
    "last_30d": "最近30×24小时",
}

TOTAL_LABEL = "总计"

UNKNOWN_GROUP_NAME = "其他"

MODULE_GROUP_ORDER: Tuple[str, ...] = ("计划器", "回复器", "图片", "记忆", "表情", "插件")

CHAT_GROUP_NAMES: Tuple[str, ...] = ("计划器", "回复器")
"""聊天链路的模块分组（对应宿主 ``request_type`` 里的 ``planner`` / ``replyer`` 系列，
包括 Maisaka 主链路的 ``maisaka.planner`` / ``maisaka.replyer``）。

其余分组（记忆 / 图片 / 表情 / 插件）是后台流水线（记忆抽取、embedding、视觉理解等），
不随聊天量走，因此报告会把「聊天链路」单独标出来，便于与直觉或宿主页面口径对齐。
"""

WINDOW_ALIASES: Dict[str, str] = {
    "today": "today",
    "今日": "today",
    "今天": "today",
    "本日": "today",
    "this_week": "this_week",
    "本周": "this_week",
    "这周": "this_week",
    "this_month": "this_month",
    "本月": "this_month",
    "这个月": "this_month",
    "last_24h": "last_24h",
    "24h": "last_24h",
    "24小时": "last_24h",
    "最近24小时": "last_24h",
    "一天": "last_24h",
    "last_7d": "last_7d",
    "7d": "last_7d",
    "7天": "last_7d",
    "7×24": "last_7d",
    "最近7天": "last_7d",
    "一周": "last_7d",
    "last_30d": "last_30d",
    "30d": "last_30d",
    "30天": "last_30d",
    "最近30天": "last_30d",
    "一个月": "last_30d",
}
"""时间窗口别名表：指令与工具里都可使用中文或英文写法。"""

WINDOW_USAGE_HINT = "今日 / 本周 / 本月 / 最近24小时 / 最近7天 / 最近30天"

SCOPE_LABELS: Dict[str, str] = {
    "all": "全部会话",
    "current": "当前对话",
    "group": "指定群聊",
    "user": "指定用户",
}


@dataclass(slots=True)
class WindowMetrics:
    """单个时间窗口的统计结果。"""

    key: str
    label: str
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    requests: int = 0
    cost: float = 0.0
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    avg_response: Optional[float] = None

    @property
    def cache_hit_rate(self) -> Optional[float]:
        """返回缓存命中率；无缓存数据时返回 None。"""

        if self.cache_hit_tokens is None or self.cache_miss_tokens is None:
            return None
        total = self.cache_hit_tokens + self.cache_miss_tokens
        if total <= 0:
            return None
        return self.cache_hit_tokens / total


@dataclass(slots=True)
class ModelUsageRow:
    """单个模型的用量明细。"""

    name: str
    internal_name: str
    requests: int
    tokens: int
    cost: float
    avg_response: Optional[float]


@dataclass(slots=True)
class ModuleUsageRow:
    """模块分组的用量明细。"""

    name: str
    tokens: int
    requests: int
    cost: float


@dataclass(slots=True)
class ChatMessageRow:
    """聊天对象的消息量明细。"""

    name: str
    messages: int
    is_group: Optional[bool]


@dataclass(slots=True)
class SeriesData:
    """条形图所需的序列数据。"""

    labels: List[str]
    series: Dict[str, List[float]]
    unit_label: str
    value_formatter: str = "number"


@dataclass(slots=True)
class PieSlice:
    """扇形图所需的占比数据。"""

    name: str
    value: float


@dataclass(slots=True)
class ReportMetrics:
    """一次报告所需的全部指标快照。"""

    scope: str
    scope_name: str
    generated_at: datetime
    unit_name: str
    windows: Dict[str, WindowMetrics] = field(default_factory=dict)
    total: Optional[WindowMetrics] = None
    total_scope_note: str = ""
    messages: Optional[int] = None
    replies: Optional[int] = None
    online_hours: Optional[float] = None
    chat_tokens: Optional[int] = None
    """聊天链路（计划器 + 回复器）的 Token 合计；其余 Token 来自后台流水线。"""
    bars: Dict[str, SeriesData] = field(default_factory=dict)
    pies: Dict[str, List[PieSlice]] = field(default_factory=dict)
    models: List[ModelUsageRow] = field(default_factory=list)
    modules: List[ModuleUsageRow] = field(default_factory=list)
    chats: List[ChatMessageRow] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    chart_granularity: str = ""
    """本次趋势图实际使用的颗粒度；为空时按配置的 ``chart.bar_granularity`` 处理。"""
    window_scoped: bool = False
    """是否只统计单个时间窗口。

    为 True 时数据层已经只采集该窗口的数据（窗口、趋势图、模型排行、占比分布
    口径一致），渲染侧只需隐藏与窗口数字重复的「总计」行。
    """

    @property
    def received_messages(self) -> Optional[int]:
        """返回接收到的用户消息数（总消息数 − 回复数）。"""

        if self.messages is None or self.replies is None:
            return None
        return max(self.messages - self.replies, 0)

    def ordered_windows(self) -> List[WindowMetrics]:
        """按固定顺序返回 6 个时间窗口。"""

        return [self.windows[key] for key in WINDOW_ORDER if key in self.windows]


def format_number_zh(value: float) -> str:
    """按中文习惯格式化数量（万亿 / 亿 / 万 / 千分位）。

    Args:
        value: 待格式化的数值。

    Returns:
        str: 中文单位的数量文本。
    """

    if value is None:
        return "不可用"
    absolute_value = abs(value)
    if absolute_value >= 1e12:
        return f"{value / 1e12:.2f} 万亿"
    if absolute_value >= 1e8:
        return f"{value / 1e8:.2f} 亿"
    if absolute_value >= 1e4:
        return f"{value / 1e4:.2f} 万"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def format_duration_zh(seconds: float) -> str:
    """把秒数格式化为中文时长（天 / 小时 / 分钟 / 秒）。

    Args:
        seconds: 时长秒数。

    Returns:
        str: 中文时长文本。
    """

    total_seconds = max(int(seconds), 0)
    days, remain = divmod(total_seconds, 86400)
    hours, remain = divmod(remain, 3600)
    minutes, secs = divmod(remain, 60)
    if days > 0:
        return f"{days} 天 {hours} 小时"
    if hours > 0:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes > 0:
        return f"{minutes} 分钟 {secs} 秒"
    return f"{secs} 秒"


def format_cost_zh(cost: Optional[float]) -> str:
    """格式化金额。

    Args:
        cost: 金额（元）。

    Returns:
        str: 金额文本；无数据时返回「不可用」。
    """

    if cost is None:
        return "不可用"
    if 0 < abs(cost) < 0.0001:
        return "¥<0.0001"
    return f"¥{cost:.4f}"


def format_response_zh(seconds: Optional[float]) -> str:
    """格式化平均响应时间。

    Args:
        seconds: 平均响应秒数。

    Returns:
        str: 响应时间文本；无数据时返回「不可用」。
    """

    if seconds is None:
        return "不可用"
    if seconds < 1:
        return f"{seconds * 1000:.0f} 毫秒"
    return f"{seconds:.2f} 秒"


def format_per_100(cost: Optional[float], base: Optional[int]) -> str:
    """计算并格式化「每 100 条」的花费。

    Args:
        cost: 总花费。
        base: 基准条数。

    Returns:
        str: 形如 ``1.2345 ¥/100条`` 的文本；无法计算时返回 ``N/A``。
    """

    if cost is None or not base:
        return "N/A"
    return f"{cost / base * 100:.4f} ¥/100条"


def format_per_hour(cost: Optional[float], hours: Optional[float]) -> str:
    """计算并格式化「每小时」的花费。

    Args:
        cost: 总花费。
        hours: 在线小时数。

    Returns:
        str: 形如 ``1.23 ¥/小时`` 的文本；无法计算时返回 ``N/A``。
    """

    if cost is None or not hours:
        return "N/A"
    return f"{cost / hours:.2f} ¥/小时"


def format_tokens_per_hour(tokens: Optional[int], hours: Optional[float], unit_name: str) -> str:
    """计算并格式化「每小时的 Token 数」。

    Args:
        tokens: Token 总量。
        hours: 在线小时数。
        unit_name: Token 单位名。

    Returns:
        str: 形如 ``1.23 万 鸡蛋/小时`` 的文本；无法计算时返回 ``N/A``。
    """

    if tokens is None or not hours:
        return "N/A"
    return f"{format_number_zh(tokens / hours)} {unit_name}/小时"


def apply_model_alias(internal_name: str, alias_map: Dict[str, str]) -> str:
    """把模型内部名转换为配置的显示别名。

    Args:
        internal_name: 模型内部名（宿主聚合表中的 model_name）。
        alias_map: 别名映射。

    Returns:
        str: 显示名称。
    """

    normalized_name = str(internal_name or "").strip() or "未知模型"
    return alias_map.get(normalized_name, normalized_name)


def build_bar_bucket_label(timestamp: datetime, granularity: str) -> str:
    """按颗粒度生成条形图横轴标签。

    Args:
        timestamp: 原始数据桶时间戳。
        granularity: 颗粒度（hour/day/week/month）。

    Returns:
        str: 横轴标签。
    """

    if granularity == "hour":
        return timestamp.strftime("%m-%d %H:00")
    if granularity == "week":
        iso_year, iso_week, _ = timestamp.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if granularity == "month":
        return timestamp.strftime("%Y-%m")
    return timestamp.strftime("%m-%d")


def filter_and_merge_series(
    timestamps: List[str],
    series: Dict[str, List[float]],
    *,
    granularity: str,
    cutoff: datetime,
) -> SeriesData:
    """把能力返回的时间序列按颗粒度归并成条形图数据。

    Args:
        timestamps: 原始序列时间戳（形如 ``2026-10-06 12:00:00``）。
        series: 序列名到数值列表的映射。
        granularity: 目标颗粒度。
        cutoff: 起始时间，早于该时间的桶会被丢弃。

    Returns:
        SeriesData: 归并后的条形图数据。
    """

    bucket_order: List[str] = []
    bucket_values: Dict[str, Dict[str, float]] = {}
    for index, raw_timestamp in enumerate(timestamps):
        parsed = parse_timestamp(raw_timestamp)
        if parsed is None or parsed < cutoff:
            continue
        bucket_label = build_bar_bucket_label(parsed, granularity)
        if bucket_label not in bucket_values:
            bucket_values[bucket_label] = {}
            bucket_order.append(bucket_label)
        for series_name, values in series.items():
            if index >= len(values):
                continue
            bucket_values[bucket_label][series_name] = (
                bucket_values[bucket_label].get(series_name, 0.0) + float(values[index] or 0)
            )

    merged: Dict[str, List[float]] = {series_name: [] for series_name in series}
    for bucket_label in bucket_order:
        for series_name in series:
            merged[series_name].append(bucket_values[bucket_label].get(series_name, 0.0))
    return SeriesData(labels=bucket_order, series=merged, unit_label="")


def limit_series_top(series_data: SeriesData, top_count: int) -> SeriesData:
    """只保留总量前 N 个序列，其余序列合并为「其他」。

    Args:
        series_data: 原始多序列数据。
        top_count: 保留的序列数量。

    Returns:
        SeriesData: 处理后的多序列数据。
    """

    if len(series_data.series) <= top_count:
        return series_data

    totals = {name: sum(values) for name, values in series_data.series.items()}
    sorted_names = sorted(totals, key=lambda name: (-totals[name], name))
    kept_names = sorted_names[:top_count]
    others_names = sorted_names[top_count:]

    limited: Dict[str, List[float]] = {}
    for name in kept_names:
        limited[name] = list(series_data.series[name])
    others_values = [0.0] * len(series_data.labels)
    for name in others_names:
        values = series_data.series[name]
        for index, value in enumerate(values):
            if index < len(others_values):
                others_values[index] += float(value or 0)
    limited["其他"] = others_values
    return SeriesData(
        labels=series_data.labels,
        series=limited,
        unit_label=series_data.unit_label,
        value_formatter=series_data.value_formatter,
    )


def parse_timestamp(raw_value: object) -> Optional[datetime]:
    """解析能力返回或数据库返回的时间戳。

    Args:
        raw_value: 形如 ``2026-10-06 12:00:00``、``2026-10-06T12:00:00`` 的字符串或 datetime。

    Returns:
        Optional[datetime]: 解析成功时返回 datetime，否则返回 None。
    """

    if isinstance(raw_value, datetime):
        return raw_value
    if not isinstance(raw_value, str):
        return None
    text = raw_value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "").replace(" ", "T")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def build_window_predicates(now: datetime) -> Dict[str, "WindowPredicate"]:
    """构造 6 个时间窗口的判定函数。

    Args:
        now: 基准时间。

    Returns:
        Dict[str, WindowPredicate]: 窗口 key 到判定函数的映射。
    """

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    return {
        "today": WindowPredicate(matches=lambda ts: ts >= today_start, start=today_start),
        "this_week": WindowPredicate(matches=lambda ts: ts >= week_start, start=week_start),
        "this_month": WindowPredicate(matches=lambda ts: ts >= month_start, start=month_start),
        "last_24h": WindowPredicate(matches=lambda ts: ts >= now - timedelta(hours=24), start=now - timedelta(hours=24)),
        "last_7d": WindowPredicate(matches=lambda ts: ts >= now - timedelta(days=7), start=now - timedelta(days=7)),
        "last_30d": WindowPredicate(matches=lambda ts: ts >= now - timedelta(days=30), start=now - timedelta(days=30)),
    }


@dataclass(slots=True)
class WindowPredicate:
    """时间窗口判定条件。"""

    matches: Callable[[datetime], bool]
    start: datetime

    def contains(self, timestamp: datetime) -> bool:
        """判断时间戳是否落在窗口内。"""

        return self.matches(timestamp)


def sum_series_in_window(timestamps: List[str], values: List[float], predicate: "WindowPredicate") -> float:
    """累加落在指定时间窗口内的序列数值。

    Args:
        timestamps: 序列时间戳列表。
        values: 与时间戳一一对应的数值列表。
        predicate: 时间窗口判定条件。

    Returns:
        float: 窗口内的数值合计。
    """

    total = 0.0
    for index, raw_timestamp in enumerate(timestamps):
        if index >= len(values):
            break
        parsed = parse_timestamp(raw_timestamp)
        if parsed is None or not predicate.contains(parsed):
            continue
        total += float(values[index] or 0)
    return total


def resolve_module_group(module_name: str, group_map: Dict[str, str]) -> str:
    """把模块名映射为配置中的中文分组名。

    传入的应当是**完整请求类型**（``request_type``，如 ``maisaka.replyer``）：
    宿主的 ``module_name`` 只保留首个 ``.`` 之前的部分，会把 ``maisaka.*`` 全部压成
    ``maisaka``，导致带点的前缀（``maisaka.replyer`` / ``A_Memorix.ImageEmbedding``）永远匹配不上。
    只有宿主花费能力因为只支持 ``module_name`` 过滤，才会退化成传截断名。

    Args:
        module_name: 完整请求类型；模块花费场景下为 ``module_name``（首个 ``.`` 之前的部分）。
        group_map: 「模块名前缀 → 分组名」映射，按配置顺序遍历。

    Returns:
        str: 分组名；未命中时返回「其他」。
    """

    normalized_name = str(module_name or "").strip()
    if not normalized_name:
        return UNKNOWN_GROUP_NAME
    for prefix, group_name in group_map.items():
        if normalized_name == prefix or normalized_name.startswith(prefix):
            return group_name
    return UNKNOWN_GROUP_NAME


def ordered_group_items(values: Dict[str, float]) -> List[Tuple[str, float]]:
    """按固定分组顺序输出非零项，未列出的分组排在末尾。"""

    ordered: List[Tuple[str, float]] = []
    for group_name in MODULE_GROUP_ORDER:
        if values.get(group_name, 0) > 0:
            ordered.append((group_name, values[group_name]))
    for group_name, value in sorted(values.items(), key=lambda item: (-item[1], item[0])):
        if group_name in MODULE_GROUP_ORDER or value <= 0:
            continue
        ordered.append((group_name, value))
    return ordered


def normalize_window_key(text: object) -> Optional[str]:
    """把用户输入的时间说法解析为窗口 key。

    Args:
        text: 用户输入，例如 ``今日``、``today``、``7天``、``last_24h``。

    Returns:
        Optional[str]: 命中的窗口 key；无法识别时返回 None。
    """

    normalized = str(text or "").strip().lower()
    if not normalized:
        return None
    return WINDOW_ALIASES.get(normalized)


def model_token_total(metrics: "ReportMetrics") -> float:
    """返回全部模型的 Token 合计，用于计算「占比」列。

    优先取占比图里的完整模型清单：``metrics.models`` 只保留配置的显示条数，
    直接拿它求和会让占比虚高（被截断的模型被算丢了）。

    Args:
        metrics: 指标快照。

    Returns:
        float: Token 合计；完全无数据时返回 1.0（避免除零）。
    """

    slices = metrics.pies.get("model_tokens") or []
    total = sum(float(item.value) for item in slices)
    if total > 0:
        return total
    return float(sum(row.tokens for row in metrics.models)) or 1.0


def build_scope_name(scope: str, target_label: str = "") -> str:
    """生成报告中的统计范围描述。

    Args:
        scope: 统计范围类型。
        target_label: 指定群聊/用户时的具体名称。

    Returns:
        str: 统计范围描述文本。
    """

    base_label = SCOPE_LABELS.get(scope, scope)
    if target_label:
        return f"{base_label}（{target_label}）"
    return base_label