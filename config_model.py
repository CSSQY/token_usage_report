"""Token 用量统计与播报插件的配置模型。

所有配置节均继承 ``PluginConfigBase``，由 Runner 负责补齐默认值、
生成 ``config.toml`` 以及向 WebUI 提供配置表单 Schema。

字段的 ``json_schema_extra`` 用于 WebUI 可视化，按宿主 Dashboard 的实际取值：
``label`` 是表单里的中文短名；``hint`` 是字段下方**直接显示**的说明（插件配置页
只显示 hint，不显示 description）；``placeholder`` 是输入框示例；
``x-widget`` 会被宿主规范化为 ``ui_type``，其中字符串列表必须用 ``list``
（``tags`` 在该页面没有对应控件，会退化成普通输入框）。
"""

from typing import Any, Dict, List, Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator

import logging

logger = logging.getLogger(__name__)

_DEFAULT_UNIT_NAME = "Token"
"""用量单位名的默认值；配置里留空时回退到它。"""

_DEFAULT_TEMPLATE = """📊 {unit_name} 消耗统计（{date}）
统计范围：{scope_name}
今日：{today}（{today_requests}次请求 / {today_cost}）
本周：{this_week}（{this_week_requests}次请求 / {this_week_cost}）
本月：{this_month}（{this_month_requests}次请求 / {this_month_cost}）
最近24小时：{last_24h}（{last_24h_requests}次请求 / {last_24h_cost}）
最近7×24小时：{last_7d}（{last_7d_requests}次请求 / {last_7d_cost}）
最近30×24小时：{last_30d}（{last_30d_requests}次请求 / {last_30d_cost}）
总计（{total_scope}）：{total_tokens}
输入 {total_prompt} / 输出 {total_completion} / 请求 {total_requests}次 / 花费 {total_cost}
消息 {messages}条 · 回复 {replies}条 · 接收 {received_messages}条 · 在线 {online_duration}
平均响应 {total_avg_response} · 花费/100条消息 {cost_per_100_messages} · 花费/小时 {cost_per_hour} · {tokens_per_hour}
{model_ranking}
{module_breakdown}
{chat_message_share}"""

_DEFAULT_LLM_PROMPT = (
    "请用自然、口语化的中文，把下面这份用量统计数据转述给群友或用户。要求：保留全部数字，"
    "不要编造、不要省略任何一项，不要输出表格或代码块，控制在 10 行以内。"
    "数据里给出了用量单位名称，转述时请照用该单位名。"
)


def _list_field(placeholder: str, hint: str) -> Dict[str, object]:
    """构造字符串列表字段的 UI 元数据。

    列表字段必须声明 ``x-widget: list``，宿主 Dashboard 才会渲染成
    「输入框 + 添加按钮 + 已添加项列表」的编辑器。

    Args:
        placeholder: 输入框示例文案。
        hint: 字段下方直接显示的说明。

    Returns:
        Dict[str, object]: 列表字段的 json_schema_extra。
    """

    return {"label": "", "hint": hint, "placeholder": placeholder, "x-widget": "list"}


class PluginSection(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="关闭后插件不会被加载，指令、工具与定时播报全部停止",
        json_schema_extra={"label": "启用插件", "hint": "关闭后 /token、LLM 工具与定时播报全部停止"},
    )
    config_version: str = Field(
        default="1.8.4",
        description="配置结构版本，由插件维护；升级配置结构时递增，用户一般不需要改动",
        json_schema_extra={"label": "配置版本", "hint": "插件用它判断是否升级配置结构，一般不用改", "placeholder": "1.8.4"},
    )


class TokenUnitSection(PluginConfigBase):
    """用量单位配置。"""

    __ui_label__ = "用量单位"
    __ui_icon__ = "tag"
    __ui_order__ = 1

    unit_name: str = Field(
        default=_DEFAULT_UNIT_NAME,
        description="用量的显示单位名称，会出现在文本报告、图片报告与工具返回中，可随意改成 鸡蛋 / 词元 / 白饭 等；"
        f"留空时回退为默认值 {_DEFAULT_UNIT_NAME}",
        json_schema_extra={
            "label": "单位名称",
            "hint": "改这里就能把报告里的用量单位换成 鸡蛋/词元/白饭 等，例如填「鸡蛋」后显示「1.23 万 鸡蛋」；留空则回退为 Token",
            "placeholder": "例如：Token、鸡蛋、词元",
        },
    )

    @field_validator("unit_name", mode="before")
    @classmethod
    def _normalize_unit_name(cls, value: Any) -> str:
        """规范化单位名：去首尾空白，留空时回退为默认单位名。

        单位名为空会让报告出现「1.23 万 」「/小时」「模型排行（按  排序）」这类残缺文案，
        因此这里统一兜到默认值，并记一条警告，便于发现配置漏填而不是静默产出坏报告。
        """

        normalized = str(value or "").strip()
        if normalized:
            return normalized
        logger.warning("[token_usage_report] 用量单位名为空，已回退为默认值「%s」", _DEFAULT_UNIT_NAME)
        return _DEFAULT_UNIT_NAME


class ModelAliasSection(PluginConfigBase):
    """模型别名配置。"""

    __ui_label__ = "模型别名"
    __ui_icon__ = "cpu"
    __ui_order__ = 2

    aliases: List[str] = Field(
        default_factory=list,
        description="模型显示别名列表，每条格式为「内部名=显示名」，例如 deepseek-v4-flash=小深；"
        "模型排行、占比图与详细数据表都会显示别名，留空则显示宿主里的原始模型名",
        json_schema_extra={
            **_list_field("内部名=显示名", "在输入框填「内部名=显示名」后点右侧 + 添加；留空则显示原始模型名"),
            "label": "别名列表",
        },
    )


class ReportSection(PluginConfigBase):
    """每日定时播报与 LLM 风格化转述配置。"""

    __ui_label__ = "定时播报"
    __ui_icon__ = "clock"
    __ui_order__ = 3

    enabled: bool = Field(
        default=False,
        description="开启后按「发送时刻」定时推送报告；默认关闭，避免在没有配置目标群时就向聊天发消息",
        json_schema_extra={"label": "启用定时播报", "hint": "默认关闭；开启前请先填好目标群号或目标 QQ 号"},
    )
    schedule_times: List[str] = Field(
        default=["08:00", "20:00"],
        description="每日发送时刻，24 小时制 HH:MM，可填多个；已经过去的时刻不会补发",
        json_schema_extra={
            **_list_field("08:00", "在输入框填 HH:MM 后点 + 添加，可加多条；已过去的时刻不补发"),
            "label": "发送时刻",
        },
    )
    platform: str = Field(
        default="qq",
        description="目标平台标识，用于把群号/QQ 号解析成聊天流；一般保持 qq 即可",
        json_schema_extra={"label": "平台", "hint": "用于把群号/QQ 号解析成聊天流，通常保持 qq", "placeholder": "qq"},
    )
    target_groups: List[str] = Field(
        default_factory=list,
        description="接收播报的 QQ 群号；群号不存在时会尝试创建聊天流，仍失败则跳过并在日志提示",
        json_schema_extra={
            **_list_field("123456789", "填群号后点 + 添加，可加多个群；群不存在会尝试建会话，失败则跳过"),
            "label": "目标群号",
        },
    )
    target_users: List[str] = Field(
        default_factory=list,
        description="接收播报的 QQ 号（私聊）；与目标群号可以同时配置",
        json_schema_extra={
            **_list_field("10001", "填 QQ 号后点 + 添加，可加多个；与目标群号可以同时配置"),
            "label": "目标 QQ 号",
        },
    )
    mode: Literal["template", "llm"] = Field(
        default="template",
        description="报告的文本呈现方式：模板渲染 或 复用宿主模型组做风格化转述",
        json_schema_extra={
            "label": "呈现方式",
            "hint": "选「文本模板」=固定格式、结果稳定；选「LLM 风格化转述」=用宿主模型组改写成自然语言（可带人格）",
        },
    )
    template: str = Field(
        default=_DEFAULT_TEMPLATE,
        description="文本模板，仅在呈现方式为「文本模板」时使用。"
        "占位符随取随用：下面列出的 54 个不要求全部使用，想显示哪个就写哪个，"
        "没写的不会出现在报告里（只用了部分占位符时，空出来的行会被自动压掉）。"
        "可用占位符（共 54 个）——"
        "基础：{date} 报告时间、{unit_name} 单位名、{scope} 范围标识、{scope_name} 范围中文、{total_scope} 总计口径；"
        "窗口（每个窗口 4 个：用量带单位 / _raw 纯数字 / _requests 请求数 / _cost 花费）："
        "{today} {today_raw} {today_requests} {today_cost}、"
        "{this_week} {this_week_raw} {this_week_requests} {this_week_cost}、"
        "{this_month} {this_month_raw} {this_month_requests} {this_month_cost}、"
        "{last_24h} {last_24h_raw} {last_24h_requests} {last_24h_cost}、"
        "{last_7d} {last_7d_raw} {last_7d_requests} {last_7d_cost}、"
        "{last_30d} {last_30d_raw} {last_30d_requests} {last_30d_cost}；"
        "总计：{total_tokens} {total_raw} {total_prompt} {total_completion} {total_requests} {total_cost} {total_avg_response}；"
        "消息与运营：{messages} {replies} {received_messages} {online_duration} "
        "{cost_per_100_messages} {cost_per_100_received} {cost_per_100_replies} {cost_per_hour} {tokens_per_hour}；"
        "缓存（仅会话维度有值，全局视图无数据时整行隐藏）：{cache_hit_rate} {cache_hit_tokens} {cache_miss_tokens}；"
        "多行区块：{model_ranking} 模型排行、{module_breakdown} 模块占比、{chat_message_share} 聊天消息分布、"
        "{details} 详细数据表、{unavailable} 本次不可用数据、{notes} 统计口径脚注。"
        "注意事项：正文里若需要显示花括号本身，请写成两个左花括号与两个右花括号；"
        "写错的占位符会被置空并记录一条警告，模板语法错误会回退内置默认模板。",
        json_schema_extra={
            "label": "文本模板",
            "hint": "占位符随取随用：写哪个就显示哪个，没写的不会出现，空出来的行会自动压掉；把鼠标移到字段名上可看全部 54 个占位符清单",
            "x-widget": "textarea",
            "rows": 14,
            "placeholder": "📊 {unit_name} 消耗统计（{date}）…",
        },
    )
    llm_task_name: str = Field(
        default="utils",
        description="复用的宿主模型组（model_config.toml 里的任务配置名，如 utils / replyer / planner）；"
        "模型与温度等参数都在宿主侧维护，填了不存在的名字时由宿主按默认策略解析",
        json_schema_extra={
            "label": "模型组（任务配置名）",
            "hint": "填宿主已分配好的模型组名，例如 utils / replyer / planner；模型与温度都由宿主那侧决定",
            "placeholder": "utils",
        },
    )
    use_persona: bool = Field(
        default=True,
        description="开启后自动读取宿主人格（[personality] 人格设定与表达风格、[bot] 昵称）并按下面的模板注入提示词",
        json_schema_extra={"label": "注入宿主人格", "hint": "自动读取宿主的 [personality] 人格与表达风格、[bot] 昵称，拼进提示词"},
    )
    persona_template: str = Field(
        default="",
        description="人格提示词模板，留空则使用内置拼装（昵称/人格/表达风格各占一行）。"
        "可用占位符：{bot_name}、{personality}、{reply_style}",
        json_schema_extra={
            "label": "人格提示词模板",
            "hint": "留空=用内置拼装（昵称/人格/表达风格各一行）；可用占位符 {bot_name}、{personality}、{reply_style}",
            "x-widget": "textarea",
            "rows": 4,
            "placeholder": "你是{bot_name}。你的人格设定：{personality}。你的表达风格：{reply_style}",
        },
    )
    persona_extra: str = Field(
        default="",
        description="额外提示词，会追加在人格提示词之后（注入宿主人格关闭时它仍会生效）",
        json_schema_extra={
            "label": "额外提示词",
            "hint": "追加在人格之后；即使关掉「注入宿主人格」这条也仍然生效",
            "x-widget": "textarea",
            "rows": 3,
            "placeholder": "例如：语气活泼一点，最后加一句鼓励的话",
        },
    )
    llm_prompt: str = Field(
        default=_DEFAULT_LLM_PROMPT,
        description="转述任务说明（不含数据本体与人格）；人格会拼在它前面，统计数据会附在它后面。"
        "支持占位符 {unit_name}（会被替换成 token_unit.unit_name）",
        json_schema_extra={
            "label": "转述提示词",
            "hint": "只写任务说明即可；系统会自动把「人格」拼在前面、「统计数据」附在后面。可用 {unit_name} 引用当前单位名",
            "x-widget": "textarea",
            "rows": 5,
            "placeholder": "请用自然、口语化的中文把数据转述给群友…",
        },
    )
    send_image: bool = Field(
        default=True,
        description="播报时是否附带图片报告；图片渲染失败会自动改为只发文字并记录一条错误日志",
        json_schema_extra={"label": "附带图片", "hint": "图片渲染失败时会自动改为只发文字，并记一条错误日志"},
    )


class CommandSection(PluginConfigBase):
    """``/token`` 指令配置。"""

    __ui_label__ = "统计指令"
    __ui_icon__ = "terminal"
    __ui_order__ = 4

    enabled: bool = Field(
        default=True,
        description="关闭后 /token 与 /tokens 不再响应（机器人的其他功能不受影响）",
        json_schema_extra={"label": "启用指令", "hint": "关闭后 /token 与 /tokens 不再响应，其他功能不受影响"},
    )
    permission_mode: Literal["all", "whitelist", "blacklist"] = Field(
        default="whitelist",
        description="群名单制度：默认「群白名单」（只有白名单内的群可用，避免群里任何人一句话就把全局账本翻出来）；"
        "确认要开放给所有群时再改成「不限制」",
        json_schema_extra={
            "label": "群名单制度",
            "hint": "默认「群白名单」：先把允许用指令的群号填到下面的白名单里；选「不限制」=任何群都能用（群黑名单仍生效）；"
            "选「群黑名单」=黑名单里的群不可用。用户名单三种制度下都生效，且冲突时黑名单优先",
        },
    )
    whitelist_groups: List[str] = Field(
        default_factory=list,
        description="群白名单：仅在「群白名单」制度下生效；与群黑名单冲突时黑名单优先",
        json_schema_extra={
            **_list_field("123456789", "填群号后点 + 添加；仅在「群白名单」制度下生效"),
            "label": "白名单群号",
        },
    )
    whitelist_users: List[str] = Field(
        default_factory=list,
        description="用户白名单：命中的 QQ 号始终可用（优先于群名单）；与用户黑名单冲突时黑名单优先",
        json_schema_extra={
            **_list_field("10001", "填 QQ 号后点 + 添加；命中的用户始终可用（优先于群名单）"),
            "label": "白名单 QQ 号",
        },
    )
    blacklist_groups: List[str] = Field(
        default_factory=list,
        description="群黑名单：命中的群在任何制度下都不可用（优先于群白名单）",
        json_schema_extra={
            **_list_field("123456789", "填群号后点 + 添加；命中的群在任何制度下都不可用"),
            "label": "黑名单群号",
        },
    )
    blacklist_users: List[str] = Field(
        default_factory=list,
        description="用户黑名单：命中的 QQ 号始终不可用（最高优先级，高于用户白名单）",
        json_schema_extra={
            **_list_field("10001", "填 QQ 号后点 + 添加；命中的用户始终不可用（优先级最高）"),
            "label": "黑名单 QQ 号",
        },
    )
    notify_no_permission: bool = Field(
        default=True,
        description="无权限时是否回复提示；关闭后无权限的调用会被静默忽略，不发送任何消息",
        json_schema_extra={
            "label": "提示无权限",
            "hint": "关闭后无权限时完全静默（不发任何消息）；开启时按下面的「无权限提示」内容回复",
        },
    )
    deny_message: str = Field(
        default="你没有权限使用该指令",
        description="无权限时的回复内容；留空则静默拒绝（与「提示无权限」开关任一关闭都不回复）",
        json_schema_extra={
            "label": "无权限提示",
            "hint": "「提示无权限」开启时回复的内容；留空则不回复",
            "placeholder": "你没有权限使用该指令",
        },
    )
    tool_allowed_scopes: List[str] = Field(
        default_factory=lambda: ["current"],
        description="LLM 工具 query_token_usage 允许查询的范围（默认只允许当前对话）；"
        "填 current / all / group / user，留空表示禁止通过工具查询",
        json_schema_extra={
            **_list_field(
                "current",
                "填 current=当前对话（默认）/ all=全部会话 / group=指定群 / user=指定用户，填完点 + 添加；"
                "留空=模型完全不能查（工具存在但一律拒绝）",
            ),
            "label": "工具可查询范围",
        },
    )
    use_image: bool = Field(
        default=True,
        description="指令回复是否使用图片渲染；渲染失败会自动改为只发文字并记录一条错误日志",
        json_schema_extra={"label": "指令使用图片", "hint": "渲染失败会自动改为只发文字，并记一条错误日志"},
    )


class RenderSection(PluginConfigBase):
    """图片渲染配置。"""

    __ui_label__ = "图片渲染"
    __ui_icon__ = "image"
    __ui_order__ = 5

    template_name: str = Field(
        default="simple",
        description="图片报告使用的模板名称：可填 4 个内置名 simple / dark / handbook / rank，"
        "也可填「自定义模板」里定义的名称；填了不存在的名字会记录一条错误日志并回退 simple",
        json_schema_extra={
            "label": "图片模板",
            "hint": "填 simple / dark / handbook / rank，或「自定义模板」里定义的名称；四种内置模板数据相同、只是配色排版不同",
            "placeholder": "simple",
        },
    )
    custom_templates: List[str] = Field(
        default_factory=list,
        description="自定义图片模板列表，每条格式为「名称|基础样式|主色|页背景色|卡片底色」："
        "基础样式取 simple / dark / handbook / rank（决定排版与字体），主色必填且为 #RRGGBB，"
        "页背景色与卡片底色可选（省略则沿用基础样式配色）。添加后在「图片模板」里填该名称即可使用。",
        json_schema_extra={
            **_list_field(
                "我的模板|dark|#5ea0ff|#0b1220|#151f33",
                "填「名称|基础样式|主色」后点 + 添加（页背景色与卡片底色可选）；基础样式取 simple/dark/handbook/rank，"
                "颜色写 #RRGGBB；加好后到「图片模板」里填这个名字",
            ),
            "label": "自定义模板",
        },
    )
    image_format: Literal["png", "jpeg"] = Field(
        default="png",
        description="图片格式：PNG 无损体积较大，JPEG 体积小、可指定质量",
        json_schema_extra={
            "label": "图片格式",
            "hint": "PNG 无损、体积大；JPEG 体积小，可配合下面的「JPEG 质量」调节",
        },
    )
    jpeg_quality: int = Field(
        default=90,
        ge=1,
        le=100,
        description="JPEG 质量（1~100），仅在「图片格式」为 JPEG 时生效；数值越低体积越小、细节越少",
        json_schema_extra={"label": "JPEG 质量", "hint": "仅当图片格式为 JPEG 时生效；越低体积越小", "x-widget": "slider", "step": 1},
    )
    resolution_scale: float = Field(
        default=2.0,
        ge=1.0,
        le=4.0,
        description="分辨率等级（设备像素比）：越大越清晰、图片越大、渲染越慢；2.0 适合手机查看，3.0+ 适合电脑放大",
        json_schema_extra={
            "label": "分辨率等级",
            "hint": "越大越清晰也越慢：2.0 适合手机查看，3.0 以上适合电脑放大",
            "x-widget": "slider",
            "step": 0.5,
        },
    )
    first_round_timeout_ms: int = Field(
        default=15000,
        ge=1000,
        le=60000,
        description="第一轮渲染的单次超时（毫秒）：正常渲染通常几秒内完成，超过该时间会切换到下一个字体服务组",
        json_schema_extra={
            "label": "第一轮超时",
            "hint": "单位毫秒；超过就换下一个字体服务组重试",
            "placeholder": "15000",
            "step": 1000,
        },
    )
    second_round_timeout_ms: int = Field(
        default=25000,
        ge=1000,
        le=60000,
        description="第二轮（回退轮）的单次超时（毫秒）：第一轮全部失败后用兜底视口再试一次，可给更长时间",
        json_schema_extra={
            "label": "第二轮超时",
            "hint": "单位毫秒；仅在第一轮全部失败后的兜底轮使用",
            "placeholder": "25000",
            "step": 1000,
        },
    )
    viewport_width: int = Field(
        default=1000,
        ge=200,
        le=3000,
        description="第一轮渲染的浏览器视口宽度（px），一般与报告卡片宽度一致即可",
        json_schema_extra={"label": "视口宽度", "hint": "第一轮浏览器视口宽度（px）", "placeholder": "1000", "step": 50},
    )
    viewport_height: int = Field(
        default=600,
        ge=200,
        le=4000,
        description="第一轮渲染的浏览器视口高度（px）；截图按内容自适应，该值主要影响布局换行",
        json_schema_extra={
            "label": "视口高度",
            "hint": "主要影响布局换行；截图会按内容自适应高度",
            "placeholder": "600",
            "step": 50,
        },
    )
    fallback_viewport_width: int = Field(
        default=900,
        ge=200,
        le=3000,
        description="报告渲染视口兜底宽度（px）：仅第二轮使用，用于规避第一轮布局导致的高图或超时",
        json_schema_extra={"label": "兜底视口宽度", "hint": "仅第二轮（回退轮）使用", "placeholder": "900", "step": 50},
    )
    fallback_viewport_height: int = Field(
        default=1200,
        ge=200,
        le=4000,
        description="报告渲染视口兜底高度（px）：仅第二轮使用，通常给一个更高的值让长报告完整渲染",
        json_schema_extra={"label": "兜底视口高度", "hint": "仅第二轮使用；长报告建议给大一些", "placeholder": "1200", "step": 50},
    )
    allow_network: bool = Field(
        default=True,
        description="是否允许渲染页面访问外部网络；字体服务地址需要联网，关闭后页面只能用系统字体",
        json_schema_extra={"label": "允许联网渲染", "hint": "字体 CDN 需要联网；关闭后只能用系统字体"},
    )
    font_services: List[str] = Field(
        default=[
            "内地|https://fonts.loli.net|https://gstatic.loli.net",
            "海外|https://fonts.googleapis.com|https://fonts.gstatic.com",
        ],
        description="字体服务组，按顺序作为优先级；每条格式为「名称|CSS 基地址|静态字体基地址」，"
        "渲染失败会自动切换到下一组重试（整条管线有 45 秒总预算）",
        json_schema_extra={
            **_list_field(
                "内地|https://fonts.loli.net|https://gstatic.loli.net",
                "在输入框填「名称|CSS地址|静态地址」后点 + 添加；按列表顺序作为优先级，渲染失败自动换下一组",
            ),
            "label": "字体服务组",
        },
    )
    show_details: bool = Field(
        default=True,
        description="是否在报告末尾输出模型用量排行表（模型名、用量、调用次数、费用、平均耗时、占比）",
        json_schema_extra={
            "label": "输出模型用量排行表",
            "hint": "开启后图片末尾会带上模型排行表（含 # 名次）；关闭后只保留图表与总览指标。文本报告的 {details} 占位符同样受它控制",
        },
    )
    anonymize: bool = Field(
        default=True,
        description="开启后聊天对象（群名 / 昵称）在报告里统一显示为 群聊A / 个人用户A，避免群里任何人发一句 /token 就把别的群名和群友昵称晒出来",
        json_schema_extra={
            "label": "聊天对象匿名化",
            "hint": "默认开启：聊天消息分布与聊天流趋势图里的群名/昵称显示为「群聊A / 个人用户A」；"
            "只有你确定报告不会外传时再关掉",
        },
    )


class ChartSection(PluginConfigBase):
    """图表开关与颗粒度配置。"""

    __ui_label__ = "图表"
    __ui_icon__ = "bar-chart"
    __ui_order__ = 6

    bar_granularity: Literal["hour", "day", "week", "month"] = Field(
        default="day",
        description="条形图横轴颗粒度：决定每根柱子代表多长时间；小时档只回溯最近 32 天，其余档回溯 365 天",
        json_schema_extra={
            "label": "条形图颗粒度",
            "hint": "每根柱子代表多长时间：小时档只回溯 32 天，天/周/月档回溯 365 天",
        },
    )
    chart_style: Literal["bar", "line", "bar_line"] = Field(
        default="bar_line",
        description="趋势图的图形样式：bar=仅条形图，line=仅平滑曲线，bar_line=条形图叠加平滑曲线",
        json_schema_extra={
            "label": "趋势图样式",
            "hint": "可选 bar（仅条形）/ line（仅曲线）/ bar_line（条形+曲线叠加，默认）：叠加时条形半透明、"
            "曲线为平滑曲线并带数据点；五个趋势图（用量/总花费/各模型/各模块/各聊天流）统一生效",
        },
    )
    bar_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="条形图横轴回溯范围（天）：只显示最近这些天的数据，越大图越密",
        json_schema_extra={"label": "横轴范围（天）", "hint": "只显示最近这些天的数据，越大柱子越密", "placeholder": "30", "step": 1},
    )
    series_top: int = Field(
        default=5,
        ge=1,
        le=20,
        description="多序列图（各模型/各模块/各聊天流）显示前 N 个序列，其余合并成「其他」，避免图例过长",
        json_schema_extra={
            "label": "多序列显示条数",
            "hint": "多序列图只画前 N 个，其余合并成「其他」，避免图例过长",
            "x-widget": "slider",
            "step": 1,
        },
    )
    bar_tokens: bool = Field(
        default=True,
        description="「用量趋势」条形图：横轴时间、纵轴用量总量",
        json_schema_extra={"label": "条形图：用量趋势", "hint": "横轴时间、纵轴用量总量"},
    )
    bar_cost: bool = Field(
        default=True,
        description="「总花费趋势」条形图：横轴时间、纵轴花费（元）",
        json_schema_extra={"label": "条形图：总花费趋势", "hint": "横轴时间、纵轴花费（元）"},
    )
    bar_model_cost: bool = Field(
        default=True,
        description="「各模型花费趋势」多序列条形图：每个模型一个序列，前 N 个之外归入「其他」",
        json_schema_extra={"label": "条形图：各模型花费趋势", "hint": "每个模型一条序列；序列条数见上方「多序列显示条数」"},
    )
    bar_module_cost: bool = Field(
        default=True,
        description="「各模块花费趋势」多序列条形图；每个模块需要一次额外查询，模块个数由「读取上限-模块花费统计个数」控制",
        json_schema_extra={"label": "条形图：各模块花费趋势", "hint": "每个模块要额外查询一次；模块个数见「读取上限」分节"},
    )
    bar_chat_messages: bool = Field(
        default=True,
        description="「各聊天流消息数趋势」多序列条形图：展示各群/各用户的每日消息量",
        json_schema_extra={"label": "条形图：各聊天流消息数趋势", "hint": "展示各群/各用户每天的发言条数"},
    )
    pie_module_tokens: bool = Field(
        default=True,
        description="「模块用量占比」扇形图：计划器/回复器/图片/记忆/表情/插件/其他 的用量与请求占比",
        json_schema_extra={"label": "扇形图：模块用量占比", "hint": "展示 计划器/回复器/图片/记忆/表情/插件/其他 各占多少"},
    )
    pie_module_cost: bool = Field(
        default=True,
        description="「模块花费分布」扇形图：各模块的花费占比（只统计用量排名靠前的模块）",
        json_schema_extra={"label": "扇形图：模块花费分布", "hint": "各模块花费占比；只统计用量靠前的若干模块"},
    )
    pie_model_tokens: bool = Field(
        default=True,
        description="「模型用量占比」扇形图：各模型的用量占比",
        json_schema_extra={"label": "扇形图：模型用量占比", "hint": "各模型用量占比（显示别名）"},
    )
    pie_model_cost: bool = Field(
        default=True,
        description="「模型花费分布」扇形图：各模型的花费占比",
        json_schema_extra={"label": "扇形图：模型花费分布", "hint": "各模型花费占比（显示别名）"},
    )
    pie_model_requests: bool = Field(
        default=True,
        description="「模型调用量分布」扇形图：各模型被调用次数的占比",
        json_schema_extra={"label": "扇形图：模型调用量分布", "hint": "各模型被调用次数占比（显示别名）；看哪个模型用得最频繁"},
    )
    pie_chat_messages: bool = Field(
        default=True,
        description="「聊天消息分布」扇形图：各群/各用户的消息量占比（受「聊天对象匿名化」影响）",
        json_schema_extra={"label": "扇形图：聊天消息分布", "hint": "各群/各用户消息量占比；受「聊天对象匿名化」影响"},
    )


class ModuleGroupSection(PluginConfigBase):
    """模块分组配置：把宿主的模块名归入中文分组，用于模块占比统计。"""

    __ui_label__ = "模块分组"
    __ui_icon__ = "layers"
    __ui_order__ = 7

    planner: List[str] = Field(
        default=["planner", "maisaka.plan", "plan"],
        description="归入「计划器」的请求类型前缀；按完整 request_type 前缀匹配（如 maisaka.plan 可命中 maisaka.planner）",
        json_schema_extra={
            **_list_field("planner", "填请求类型前缀后点 + 添加；命中即归入「计划器」"),
            "label": "计划器",
        },
    )
    replyer: List[str] = Field(
        default=["replyer", "maisaka.replyer", "response.splitter", "reply", "response", "smart_segmentation"],
        description="归入「回复器」的模块名前缀",
        json_schema_extra={
            **_list_field("replyer", "填模块名前缀后点 + 添加；命中即归入「回复器」"),
            "label": "回复器",
        },
    )
    image: List[str] = Field(
        default=["image", "vlm", "A_Memorix.ImageEmbedding"],
        description="归入「图片」的模块名前缀（含图片理解与向量化）",
        json_schema_extra={
            **_list_field("image", "填模块名前缀后点 + 添加；命中即归入「图片」"),
            "label": "图片",
        },
    )
    memory: List[str] = Field(
        default=[
            "memory",
            "A_Memorix",
            "maisaka",
            "heuristic_memory_impression",
            "memory_feedback_correction",
            "memories",
            "person_fact_writeback",
            "chat_history_summarizer",
            "dream",
            "story",
            "expression",
            "jargon",
            "behavior",
            "embedding",
        ],
        description="归入「记忆」的模块名前缀（含记忆读写与表达/黑话/行为等学习任务）",
        json_schema_extra={
            **_list_field("memory", "填模块名前缀后点 + 添加；命中即归入「记忆」"),
            "label": "记忆",
        },
    )
    emoji: List[str] = Field(
        default=["emoji"],
        description="归入「表情」的模块名前缀",
        json_schema_extra={
            **_list_field("emoji", "填模块名前缀后点 + 添加；命中即归入「表情」"),
            "label": "表情",
        },
    )
    plugin: List[str] = Field(
        default=["plugin", "mcp_sampling"],
        description="归入「插件」的模块名前缀；本插件自己的调用也会记为 plugin.<插件 ID> 并归到这里",
        json_schema_extra={
            **_list_field("plugin", "填模块名前缀后点 + 添加；本插件的调用也归入「插件」"),
            "label": "插件",
        },
    )


class LimitsSection(PluginConfigBase):
    """读取上限与显示条数配置。"""

    __ui_label__ = "读取上限"
    __ui_icon__ = "gauge"
    __ui_order__ = 8

    max_session_rows: int = Field(
        default=20000,
        ge=1,
        le=200000,
        description="会话维度（当前对话/指定群/指定用户）明细的最大读取行数；"
        "超出时直接报错放弃统计（不会给出被截断的数字），可按需调大",
        json_schema_extra={
            "label": "会话明细行数上限",
            "hint": "会话维度读明细的行数上限；超出会直接报错，不会给出被截断的数字",
            "placeholder": "20000",
            "step": 1000,
        },
    )
    top_models: int = Field(
        default=20,
        ge=1,
        le=50,
        description="模型排行与多序列图显示的模型条数；宿主统计能力上线为 50",
        json_schema_extra={
            "label": "模型显示条数",
            "hint": "模型排行与多序列图显示多少条；宿主上限 50",
            "x-widget": "slider",
            "step": 1,
        },
    )
    top_modules: int = Field(
        default=8,
        ge=1,
        le=20,
        description="模块花费统计覆盖的模块个数；每多一个模块会多一次宿主查询，过大将拖慢报告生成",
        json_schema_extra={
            "label": "模块花费统计个数",
            "hint": "每多一个模块多一次查询，过大将拖慢报告生成",
            "x-widget": "slider",
            "step": 1,
        },
    )


class TokenUsageReportConfig(PluginConfigBase):
    """Token 用量统计与播报插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    token_unit: TokenUnitSection = Field(default_factory=TokenUnitSection)
    model_aliases: ModelAliasSection = Field(default_factory=ModelAliasSection)
    report: ReportSection = Field(default_factory=ReportSection)
    command: CommandSection = Field(default_factory=CommandSection)
    render: RenderSection = Field(default_factory=RenderSection)
    chart: ChartSection = Field(default_factory=ChartSection)
    module_groups: ModuleGroupSection = Field(default_factory=ModuleGroupSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)


def build_model_alias_map(raw_aliases: List[str]) -> Dict[str, str]:
    """把「内部名=显示名」的配置条目解析为别名映射。

    Args:
        raw_aliases: 配置中的别名条目列表。

    Returns:
        Dict[str, str]: 内部模型名到显示名的映射；非法条目会被忽略。
    """

    alias_map: Dict[str, str] = {}
    for raw_entry in raw_aliases:
        entry = str(raw_entry or "").strip()
        if not entry or "=" not in entry:
            continue
        internal_name, display_name = entry.split("=", 1)
        internal_name = internal_name.strip()
        display_name = display_name.strip()
        if internal_name and display_name:
            alias_map[internal_name] = display_name
    return alias_map


def build_module_group_map(section: ModuleGroupSection) -> Dict[str, str]:
    """把模块分组配置展开为「模块名前缀 → 分组名」的映射。

    Args:
        section: 模块分组配置节。

    Returns:
        Dict[str, str]: 模块名前缀到中文分组名的映射。
    """

    group_sources = (
        ("计划器", section.planner),
        ("回复器", section.replyer),
        ("图片", section.image),
        ("记忆", section.memory),
        ("表情", section.emoji),
        ("插件", section.plugin),
    )
    group_map: Dict[str, str] = {}
    for group_name, prefixes in group_sources:
        for raw_prefix in prefixes:
            prefix = str(raw_prefix or "").strip()
            if prefix:
                group_map[prefix] = group_name
    return group_map