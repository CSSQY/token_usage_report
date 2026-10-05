"""图片报告模板：4 套内置风格与纯内联 SVG 图表。

图表不依赖任何前端库或 CDN，唯一的外部资源是按配置注入的字体服务地址。
"""

from dataclasses import dataclass
from html import escape as _escape
from typing import Any, Dict, List, Sequence, Tuple

import logging
import math

from .config_model import TokenUsageReportConfig
from .metrics import (
    ReportMetrics,
    SeriesData,
    format_cost_zh,
    format_duration_zh,
    format_number_zh,
    format_per_100,
    format_per_hour,
    format_response_zh,
    format_tokens_per_hour,
)

logger = logging.getLogger(__name__)

_PALETTE: Tuple[str, ...] = (
    "#4F8CF6",
    "#F2994A",
    "#27AE60",
    "#EB5757",
    "#9B51E0",
    "#2D9CDB",
    "#F2C94C",
    "#6FCF97",
    "#BB6BD9",
    "#56CCF2",
)

_THEMES: Dict[str, Dict[str, str]] = {
    "simple": {
        "page_bg": "#f5f7fb",
        "card_bg": "#ffffff",
        "text": "#1f2937",
        "muted": "#6b7280",
        "accent": "#4F8CF6",
        "border": "#e5e7eb",
        "font": "'Noto Sans SC', 'Microsoft YaHei', sans-serif",
        "radius": "16px",
    },
    "dark": {
        "page_bg": "#0b1220",
        "card_bg": "#151f33",
        "text": "#e6edf7",
        "muted": "#93a4bf",
        "accent": "#5ea0ff",
        "border": "#233149",
        "font": "'Noto Sans SC', 'Microsoft YaHei', sans-serif",
        "radius": "18px",
    },
    "handbook": {
        "page_bg": "#fdf6e3",
        "card_bg": "#fffdf5",
        "text": "#4a3f35",
        "muted": "#8c7b6b",
        "accent": "#c1743f",
        "border": "#e6d8bf",
        "font": "'Noto Serif SC', 'Songti SC', serif",
        "radius": "10px",
    },
    "rank": {
        "page_bg": "#101418",
        "card_bg": "#1b2129",
        "text": "#f3f5f7",
        "muted": "#9aa5b1",
        "accent": "#f2c14e",
        "border": "#2b333d",
        "font": "'Noto Sans SC', 'Microsoft YaHei', sans-serif",
        "radius": "6px",
    },
}


@dataclass(frozen=True, slots=True)
class FontService:
    """一个字体服务组（CSS 基地址 + 静态字体基地址）。"""

    name: str
    css_base: str
    static_base: str


def parse_font_services(raw_services: Sequence[str]) -> List[FontService]:
    """解析配置中的字体服务组条目。

    Args:
        raw_services: 形如 ``名称|CSS地址|静态字体地址`` 的条目列表。

    Returns:
        List[FontService]: 解析成功的服务组列表；非法条目会被跳过并记录一次警告。
    """

    services: List[FontService] = []
    for raw_entry in raw_services:
        entry = str(raw_entry or "").strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split("|")]
        if len(parts) != 3 or not all(parts):
            logger.warning("[token_usage_report] 字体服务组格式非法，已跳过：%s（应为「名称|CSS地址|静态地址」）", entry)
            continue
        name, css_base, static_base = parts
        if not css_base.startswith(("http://", "https://")) or not static_base.startswith(("http://", "https://")):
            logger.warning("[token_usage_report] 字体服务地址必须是 http(s) URL，已跳过：%s", entry)
            continue
        services.append(FontService(name=name, css_base=css_base.rstrip("/"), static_base=static_base.rstrip("/")))
    return services


def describe_template(template_name: str) -> str:
    """返回模板风格的中文说明。"""

    return {
        "simple": "简约白卡片",
        "dark": "深色数据面板",
        "handbook": "手账便签",
        "rank": "榜单风",
    }.get(template_name, template_name)


def build_html(metrics: ReportMetrics, config: TokenUsageReportConfig, font_service: FontService) -> str:
    """生成完整的报告 HTML。

    Args:
        metrics: 指标快照。
        config: 插件配置。
        font_service: 本次使用的字体服务组。

    Returns:
        str: 可直接交给宿主渲染的 HTML 文本。
    """

    theme_name = config.render.template_name
    theme = _THEMES.get(theme_name, _THEMES["simple"])
    sections: List[str] = [
        _render_header(metrics),
        _render_window_cards(metrics),
        _render_kpi_block(metrics),
        _render_bar_section(metrics, config, theme),
        _render_pie_section(metrics, config, theme),
        _render_model_table(metrics),
        _render_detail_table(metrics, config),
        _render_footer(metrics),
    ]
    body = "\n".join(section for section in sections if section)
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<link rel=\"preconnect\" href=\"{_escape(font_service.static_base)}\">\n"
        f"<link href=\"{_escape(font_service.css_base)}/css2?family=Noto+Sans+SC:wght@400;700;900"
        "&family=Noto+Serif+SC:wght@400;700&display=swap\" rel=\"stylesheet\">\n"
        f"<style>{_render_style(theme)}</style>\n</head>\n"
        f"<body class=\"theme-{_escape(theme_name)}\">\n<div id=\"report-card\">\n{body}\n</div>\n</body>\n</html>"
    )


def _render_style(theme: Dict[str, str]) -> str:
    """生成主题样式表。"""

    return f"""
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px; background: {theme['page_bg']}; font-family: {theme['font']}; color: {theme['text']}; }}
#report-card {{ width: 900px; margin: 0 auto; padding: 28px 30px 20px; background: {theme['card_bg']};
  border: 1px solid {theme['border']}; border-radius: {theme['radius']}; }}
h1 {{ font-size: 26px; margin: 0 0 6px; }}
h2 {{ font-size: 16px; margin: 22px 0 10px; color: {theme['accent']}; }}
.meta {{ font-size: 13px; color: {theme['muted']}; margin-bottom: 18px; }}
.grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
.window-card {{ padding: 12px 14px; border: 1px solid {theme['border']}; border-radius: {theme['radius']};
  background: {theme['card_bg']}; }}
.window-label {{ font-size: 13px; color: {theme['muted']}; }}
.window-value {{ font-size: 20px; font-weight: 700; margin: 4px 0; }}
.window-sub {{ font-size: 12px; color: {theme['muted']}; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
.kpi {{ padding: 10px 12px; border: 1px solid {theme['border']}; border-radius: {theme['radius']}; }}
.kpi-title {{ font-size: 12px; color: {theme['muted']}; }}
.kpi-value {{ font-size: 17px; font-weight: 700; margin-top: 4px; }}
.chart-card {{ margin-top: 12px; padding: 14px; border: 1px solid {theme['border']}; border-radius: {theme['radius']}; }}
.chart-title {{ font-size: 14px; font-weight: 700; margin-bottom: 8px; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; font-size: 12px; color: {theme['muted']}; }}
.legend-item {{ display: flex; align-items: center; gap: 6px; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
.pie-row {{ display: flex; gap: 18px; align-items: center; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
th, td {{ padding: 7px 8px; border-bottom: 1px solid {theme['border']}; text-align: left; }}
th {{ color: {theme['muted']}; font-weight: 600; }}
.rank-1 {{ color: #f2c14e; font-weight: 700; }}
.rank-2 {{ color: #c0c7d1; font-weight: 700; }}
.rank-3 {{ color: #d29a6a; font-weight: 700; }}
.footer {{ margin-top: 18px; font-size: 11.5px; color: {theme['muted']}; line-height: 1.7; }}
.unavailable {{ color: #EB5757; }}
"""


def _render_header(metrics: ReportMetrics) -> str:
    """渲染标题区。"""

    title = f"{metrics.unit_name} 消耗统计"
    return (
        f"<h1>{_escape(title)}</h1>\n"
        f"<div class=\"meta\">统计范围：{_escape(metrics.scope_name)} · 生成时间："
        f"{metrics.generated_at.strftime('%Y-%m-%d %H:%M:%S')} · 单位：{_escape(metrics.unit_name)}</div>"
    )


def _render_window_cards(metrics: ReportMetrics) -> str:
    """渲染 6 个时间窗口卡片。"""

    cards: List[str] = []
    for window in metrics.ordered_windows():
        cards.append(
            "<div class=\"window-card\">"
            f"<div class=\"window-label\">{_escape(window.label)}</div>"
            f"<div class=\"window-value\">{_escape(format_number_zh(window.tokens))} {_escape(metrics.unit_name)}</div>"
            f"<div class=\"window-sub\">{window.requests} 次请求 · {_escape(format_cost_zh(window.cost))}</div>"
            "</div>"
        )
    if not cards:
        return ""
    return f"<h2>时间窗口</h2>\n<div class=\"grid\">{''.join(cards)}</div>"


def _render_kpi_block(metrics: ReportMetrics) -> str:
    """渲染总计与派生指标。"""

    total = metrics.total
    total_cost = total.cost if total is not None else None
    kpis: List[Tuple[str, str]] = []
    if total is not None:
        kpis.extend(
            [
                (
                    f"总计（{metrics.total_scope_note or '最近 365 天'}）",
                    f"{format_number_zh(total.tokens)} {metrics.unit_name}",
                ),
                ("输入 Token", format_number_zh(total.prompt_tokens)),
                ("输出 Token", format_number_zh(total.completion_tokens)),
                ("总请求数", format_number_zh(total.requests)),
                ("总花费", format_cost_zh(total.cost)),
                ("平均响应", format_response_zh(total.avg_response)),
            ]
        )
    kpis.extend(
        [
            ("消息数", format_number_zh(metrics.messages) if metrics.messages is not None else "不可用"),
            ("回复数", format_number_zh(metrics.replies) if metrics.replies is not None else "不可用"),
            (
                "接收消息数",
                format_number_zh(metrics.received_messages) if metrics.received_messages is not None else "不可用",
            ),
            (
                "在线时长",
                format_duration_zh(metrics.online_hours * 3600) if metrics.online_hours is not None else "不可用",
            ),
            ("花费/消息数量", format_per_100(total_cost, metrics.messages)),
            ("花费/接收消息数量", format_per_100(total_cost, metrics.received_messages)),
            ("花费/回复数量", format_per_100(total_cost, metrics.replies)),
            ("花费/时间", format_per_hour(total_cost, metrics.online_hours)),
            (
                "Token/时间",
                format_tokens_per_hour(total.tokens if total else None, metrics.online_hours, metrics.unit_name),
            ),
            (
                "Prompt 缓存命中率",
                f"{total.cache_hit_rate * 100:.2f}%" if total and total.cache_hit_rate is not None else "宿主不提供",
            ),
            (
                "缓存命中 Token",
                format_number_zh(total.cache_hit_tokens) if total and total.cache_hit_tokens is not None else "宿主不提供",
            ),
            (
                "缓存未命中 Token",
                format_number_zh(total.cache_miss_tokens)
                if total and total.cache_miss_tokens is not None
                else "宿主不提供",
            ),
        ]
    )
    items = "".join(
        f"<div class=\"kpi\"><div class=\"kpi-title\">{_escape(title)}</div>"
        f"<div class=\"kpi-value\">{_escape(value)}</div></div>"
        for title, value in kpis
    )
    return f"<h2>总览指标</h2>\n<div class=\"kpi-grid\">{items}</div>"


def _render_bar_section(metrics: ReportMetrics, config: TokenUsageReportConfig, theme: Dict[str, str]) -> str:
    """渲染全部条形图卡片。"""

    chart_config = config.chart
    chart_specs: Sequence[Tuple[str, bool, str]] = (
        ("tokens", chart_config.bar_tokens, "Token 趋势"),
        ("cost", chart_config.bar_cost, "总花费趋势"),
        ("model_cost", chart_config.bar_model_cost, "各模型花费趋势"),
        ("module_cost", chart_config.bar_module_cost, "各模块花费趋势"),
        ("chat_messages", chart_config.bar_chat_messages, "各聊天流消息数趋势"),
    )
    cards: List[str] = []
    for key, enabled, title in chart_specs:
        if not enabled:
            continue
        series_data = metrics.bars.get(key)
        if series_data is None or not series_data.labels:
            continue
        granularity_label = _granularity_label(chart_config.bar_granularity)
        cards.append(
            "<div class=\"chart-card\">"
            f"<div class=\"chart-title\">{_escape(title)}（{_escape(granularity_label)}）</div>"
            f"{render_bar_chart(series_data, theme)}"
            "</div>"
        )
    if not cards:
        return ""
    return f"<h2>用量趋势</h2>\n{''.join(cards)}"


def _render_pie_section(metrics: ReportMetrics, config: TokenUsageReportConfig, theme: Dict[str, str]) -> str:
    """渲染全部扇形图卡片。"""

    chart_config = config.chart
    pie_specs: Sequence[Tuple[str, bool, str, str]] = (
        ("module_tokens", chart_config.pie_module_tokens, "模块 Token 占比", "number"),
        ("module_cost", chart_config.pie_module_cost, "模块花费分布", "cost"),
        ("model_tokens", chart_config.pie_model_tokens, "模型 Token 占比", "number"),
        ("model_cost", chart_config.pie_model_cost, "模型花费分布", "cost"),
        ("chat_messages", chart_config.pie_chat_messages, "聊天消息分布", "number"),
    )
    cards: List[str] = []
    for key, enabled, title, formatter in pie_specs:
        if not enabled:
            continue
        slices = metrics.pies.get(key) or []
        if not slices:
            continue
        cards.append(
            "<div class=\"chart-card\">"
            f"<div class=\"chart-title\">{_escape(title)}</div>"
            f"{render_pie_chart(slices, theme, formatter=formatter)}"
            "</div>"
        )
    if not cards:
        return ""
    return f"<h2>占比分布</h2>\n{''.join(cards)}"


def _render_model_table(metrics: ReportMetrics) -> str:
    """渲染模型排行表。"""

    if not metrics.models:
        return ""
    rows: List[str] = []
    token_total = sum(row.tokens for row in metrics.models) or 1
    for index, row in enumerate(metrics.models, start=1):
        rank_class = f"rank-{index}" if index <= 3 else ""
        avg_text = format_response_zh(row.avg_response) if row.avg_response is not None else "N/A"
        rows.append(
            f"<tr><td class=\"{rank_class}\">{index}</td><td>{_escape(row.name)}</td>"
            f"<td>{_escape(format_number_zh(row.tokens))}</td>"
            f"<td>{row.requests}</td><td>{_escape(format_cost_zh(row.cost))}</td>"
            f"<td>{_escape(avg_text)}</td><td>{row.tokens / token_total * 100:.1f}%</td></tr>"
        )
    return (
        "<h2>模型用量排行</h2>\n<table><thead><tr>"
        "<th>#</th><th>模型</th><th>Token</th><th>调用次数</th><th>费用</th><th>平均耗时</th><th>占比</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _render_detail_table(metrics: ReportMetrics, config: TokenUsageReportConfig) -> str:
    """渲染详细数据表（受 ``render.show_details`` 控制）。"""

    if not config.render.show_details:
        return ""
    rows: List[str] = []
    token_total = sum(row.tokens for row in metrics.models) or 1
    for row in metrics.models:
        avg_text = format_response_zh(row.avg_response) if row.avg_response is not None else "N/A"
        rows.append(
            f"<tr><td>{_escape(row.name)}</td><td>{row.requests}</td>"
            f"<td>{_escape(format_number_zh(row.tokens))}</td>"
            f"<td>{_escape(format_cost_zh(row.cost))}</td><td>{_escape(avg_text)}</td>"
            f"<td>{row.tokens / token_total * 100:.1f}%</td></tr>"
        )
    if not rows:
        for module_row in metrics.modules:
            rows.append(
                f"<tr><td>{_escape(module_row.name)}</td><td>{module_row.requests}</td>"
                f"<td>{_escape(format_number_zh(module_row.tokens))}</td>"
                f"<td>{_escape(format_cost_zh(module_row.cost))}</td><td>N/A</td><td>N/A</td></tr>"
            )
    if not rows:
        return ""
    return (
        "<h2>详细数据</h2>\n<table><thead><tr>"
        "<th>名称</th><th>调用次数</th><th>Token</th><th>费用</th><th>平均耗时</th><th>占比</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _render_footer(metrics: ReportMetrics) -> str:
    """渲染脚注（口径说明与不可用区块）。"""

    lines = [f"· {_escape(note)}" for note in metrics.notes]
    if metrics.unavailable:
        unavailable_text = _escape("、".join(sorted(set(metrics.unavailable))))
        lines.append(f"· <span class=\"unavailable\">本次不可用的数据：{unavailable_text}</span>")
    if not lines:
        return ""
    return f"<div class=\"footer\">{'<br>'.join(lines)}</div>"


def _granularity_label(granularity: str) -> str:
    """返回颗粒度的中文说明。"""

    return {"hour": "按小时", "day": "按天", "week": "按周", "month": "按月"}.get(granularity, granularity)


def format_chart_value(value: float, formatter: str) -> str:
    """按图表类型格式化数值。

    Args:
        value: 原始数值。
        formatter: ``number`` 或 ``cost``。

    Returns:
        str: 展示文本。
    """

    if formatter == "cost":
        return format_cost_zh(value)
    return format_number_zh(value)


def render_bar_chart(series_data: SeriesData, theme: Dict[str, str], height: int = 220) -> str:
    """渲染多序列纵向条形图（纯内联 SVG）。

    Args:
        series_data: 序列数据。
        theme: 主题配色。
        height: 图表高度。

    Returns:
        str: SVG 与图例的 HTML 片段。
    """

    labels = series_data.labels
    if not labels:
        return ""
    series_names = list(series_data.series.keys())
    max_value = max((max(values) if values else 0.0) for values in series_data.series.values()) or 1.0

    width = 860
    padding_left = 6
    padding_bottom = 26
    padding_top = 12
    plot_height = height - padding_bottom - padding_top
    plot_width = width - padding_left * 2
    group_width = plot_width / max(len(labels), 1)
    bar_width = max(1.5, (group_width * 0.72) / max(len(series_names), 1))

    parts: List[str] = [
        f"<svg viewBox=\"0 0 {width} {height}\" width=\"100%\" height=\"{height}\" "
        "xmlns=\"http://www.w3.org/2000/svg\" role=\"img\">"
    ]
    parts.append(
        f"<line x1=\"{padding_left}\" y1=\"{padding_top + plot_height}\" "
        f"x2=\"{width - padding_left}\" y2=\"{padding_top + plot_height}\" "
        f"stroke=\"{theme['border']}\" stroke-width=\"1\"/>"
    )
    label_step = max(1, len(labels) // 12)
    for index, label in enumerate(labels):
        group_x = padding_left + index * group_width
        for series_index, series_name in enumerate(series_names):
            values = series_data.series[series_name]
            value = float(values[index] or 0) if index < len(values) else 0.0
            bar_height = max(0.0, value / max_value * plot_height)
            bar_x = group_x + (group_width - bar_width * len(series_names)) / 2 + series_index * bar_width
            bar_y = padding_top + plot_height - bar_height
            color = _PALETTE[series_index % len(_PALETTE)]
            tooltip = f"{series_name} {label}：{format_chart_value(value, series_data.value_formatter)}"
            parts.append(
                f"<rect x=\"{bar_x:.1f}\" y=\"{bar_y:.1f}\" width=\"{bar_width:.1f}\" height=\"{bar_height:.1f}\" "
                f"fill=\"{color}\" rx=\"2\"><title>{_escape(tooltip)}</title></rect>"
            )
        if index % label_step == 0:
            parts.append(
                f"<text x=\"{group_x + group_width / 2:.1f}\" y=\"{height - 8}\" font-size=\"10\" "
                f"fill=\"{theme['muted']}\" text-anchor=\"middle\">{_escape(label)}</text>"
            )
    parts.append("</svg>")
    if len(series_names) > 1:
        legend_items = "".join(
            f"<span class=\"legend-item\"><span class=\"legend-dot\" style=\"background:{_PALETTE[index % len(_PALETTE)]}\">"
            f"</span>{_escape(name)}</span>"
            for index, name in enumerate(series_names)
        )
        parts.append(f"<div class=\"legend\">{legend_items}</div>")
    return "".join(parts)


def render_pie_chart(
    slices: Sequence[Any],
    theme: Dict[str, str],
    formatter: str = "number",
    size: int = 190,
) -> str:
    """渲染扇形图（纯内联 SVG + 图例）。

    Args:
        slices: 占比数据（需带 ``name`` 与 ``value`` 属性）。
        theme: 主题配色。
        formatter: 数值格式化方式。
        size: 饼图直径。

    Returns:
        str: SVG 与图例的 HTML 片段。
    """

    normalized: List[Tuple[str, float]] = [
        (str(getattr(item, "name", "")), float(getattr(item, "value", 0.0))) for item in slices
    ]
    normalized = [item for item in normalized if item[1] > 0]
    if not normalized:
        return ""
    normalized.sort(key=lambda item: -item[1])
    if len(normalized) > 8:
        others_value = sum(value for _name, value in normalized[8:])
        normalized = normalized[:8] + [("其他", others_value)]
    total_value = sum(value for _name, value in normalized) or 1.0

    center = size / 2
    radius = center - 4
    start_angle = -90.0
    parts: List[str] = [
        f"<svg viewBox=\"0 0 {size} {size}\" width=\"{size}\" height=\"{size}\" "
        "xmlns=\"http://www.w3.org/2000/svg\" role=\"img\">"
    ]
    legend_parts: List[str] = []
    for index, (name, value) in enumerate(normalized):
        color = _PALETTE[index % len(_PALETTE)]
        angle = value / total_value * 360.0
        tooltip = f"{name}：{format_chart_value(value, formatter)}（{value / total_value * 100:.1f}%）"
        if angle >= 359.99:
            parts.append(
                f"<circle cx=\"{center}\" cy=\"{center}\" r=\"{radius}\" fill=\"{color}\">"
                f"<title>{_escape(tooltip)}</title></circle>"
            )
            end_angle = start_angle + angle
        else:
            end_angle = start_angle + angle
            path = _describe_arc(center, radius, start_angle, end_angle)
            parts.append(
                f"<path d=\"{path}\" fill=\"{color}\"><title>{_escape(tooltip)}</title></path>"
            )
        legend_parts.append(
            f"<div class=\"legend-item\"><span class=\"legend-dot\" style=\"background:{color}\"></span>"
            f"{_escape(name)} {value / total_value * 100:.1f}%"
            f"（{_escape(format_chart_value(value, formatter))}）</div>"
        )
        start_angle = end_angle
    parts.append("</svg>")
    return (
        f"<div class=\"pie-row\">{''.join(parts)}"
        f"<div class=\"legend\" style=\"flex-direction:column;align-items:flex-start;\">{''.join(legend_parts)}</div>"
        "</div>"
    )


def _describe_arc(center: float, radius: float, start_angle: float, end_angle: float) -> str:
    """生成扇形路径。

    Args:
        center: 圆心坐标。
        radius: 半径。
        start_angle: 起始角度（度，-90 度指向正上方）。
        end_angle: 结束角度（度）。

    Returns:
        str: SVG path 的 ``d`` 属性。
    """

    start_radian = math.radians(start_angle)
    end_radian = math.radians(end_angle)
    start_x = center + radius * math.cos(start_radian)
    start_y = center + radius * math.sin(start_radian)
    end_x = center + radius * math.cos(end_radian)
    end_y = center + radius * math.sin(end_radian)
    large_arc_flag = 1 if (end_angle - start_angle) > 180 else 0
    return (
        f"M {center:.2f} {center:.2f} L {start_x:.2f} {start_y:.2f} "
        f"A {radius:.2f} {radius:.2f} 0 {large_arc_flag} 1 {end_x:.2f} {end_y:.2f} Z"
    )