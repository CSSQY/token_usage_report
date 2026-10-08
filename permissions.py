"""两组查询权限（LLM 工具 / ``/token`` 指令）的配置解析与判定。

本模块是**纯函数**实现：只依赖 ``metrics`` 里的窗口词表，不 import ``config_model``、
pydantic 或宿主 SDK；权限配置按鸭子类型（``getattr``）读取，因此可以用普通对象
（如 ``types.SimpleNamespace``）直接做单元验证。

两组权限各自独立：一组管 ``query_token_usage`` 工具，一组管 ``/token`` 指令。
每组包含「对话流黑白名单 → 全局默认可查询范围/窗口 → 每对话覆盖」三层。
"""

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

import logging

from functools import lru_cache

from .metrics import WINDOW_ORDER, normalize_window_key

logger = logging.getLogger(__name__)

SCOPE_VALUES: Tuple[str, ...] = ("current", "all", "group", "user")
"""可查询范围取值。"""

WINDOW_VALUES: Tuple[str, ...] = ("all", *WINDOW_ORDER)
"""可查询窗口取值：``all`` = 不指定时间口径（全部窗口），其余为 ``metrics`` 的窗口 key。"""

SCOPE_LABELS: Dict[str, str] = {
    "current": "当前对话",
    "all": "全部会话",
    "group": "指定群聊",
    "user": "指定用户",
}

WINDOW_LABELS: Dict[str, str] = {
    "all": "全部窗口",
    "today": "今日",
    "this_week": "本周",
    "this_month": "本月",
    "last_24h": "最近24小时",
    "last_7d": "最近7天",
    "last_30d": "最近30天",
}

_SCOPE_ALIASES: Dict[str, str] = {
    "current": "current",
    "当前": "current",
    "当前对话": "current",
    "本群": "current",
    "all": "all",
    "全局": "all",
    "全部": "all",
    "全部会话": "all",
    "所有": "all",
    "全": "all",
    "group": "group",
    "群": "group",
    "群聊": "group",
    "指定群聊": "group",
    "user": "user",
    "个人": "user",
    "私聊": "user",
    "用户": "user",
    "指定用户": "user",
}
"""范围词表：配置里写中文或英文都能识别。"""

_CHAT_TYPE_ALIASES: Dict[str, str] = {
    "group": "group",
    "群": "group",
    "群聊": "group",
    "private": "private",
    "user": "private",
    "个人": "private",
    "私聊": "private",
    "用户": "private",
}
"""覆盖项第 1 段的聊天流类型词表。"""

_WILDCARD = "*"
"""覆盖项某一维度写 ``*`` 表示该维度全部放行。"""

_OVERRIDE_SEPARATORS = ("|", "｜")


@dataclass(frozen=True, slots=True)
class ConversationOverride:
    """单个对话流的范围/窗口覆盖。

    ``scopes`` / ``windows`` 为空集合表示该对话在对应维度上**完全禁止**查询
    （即显式列出覆盖项但把该段留空）。
    """

    scopes: FrozenSet[str]
    windows: FrozenSet[str]


def normalize_scope(text: Any) -> Optional[str]:
    """把范围写法规范化为 :data:`SCOPE_VALUES` 之一。

    Args:
        text: 用户或配置里的范围写法（``current`` / ``当前对话`` / ``群`` …）。

    Returns:
        Optional[str]: 规范化后的范围 key；无法识别时返回 None。
    """

    normalized = str(text or "").strip().lower()
    if not normalized:
        return None
    return _SCOPE_ALIASES.get(normalized)


def normalize_chat_type(text: Any) -> Optional[str]:
    """把聊天流类型写法规范化为 ``group`` 或 ``private``。"""

    normalized = str(text or "").strip().lower()
    if not normalized:
        return None
    return _CHAT_TYPE_ALIASES.get(normalized)


def normalize_window_token(text: Any) -> Optional[str]:
    """把可查询窗口写法规范化为 :data:`WINDOW_VALUES` 之一。"""

    normalized = str(text or "").strip().lower()
    if not normalized:
        return None
    if normalized in WINDOW_VALUES:
        return normalized
    return normalize_window_key(normalized)


def conversation_key(group_id: Any, user_id: Any) -> Tuple[str, str]:
    """返回当前对话在覆盖项里的键 ``(聊天流类型, 号码)``。

    群聊取群号，私聊取账号（回调里私聊的 ``group_id`` 为空）。
    """

    normalized_group = str(group_id or "").strip()
    if normalized_group:
        return "group", normalized_group
    return "private", str(user_id or "").strip()


def default_allowed(permission_config: Any) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """读取权限组的全局默认开关，返回 ``(放行的范围集合, 放行的窗口集合)``。"""

    scopes = frozenset(
        value for value in SCOPE_VALUES if bool(getattr(permission_config, f"allow_scope_{value}", False))
    )
    windows = frozenset(
        value for value in WINDOW_VALUES if bool(getattr(permission_config, f"allow_window_{value}", False))
    )
    return scopes, windows


def resolve_allowed(
    permission_config: Any,
    *,
    group_id: Any,
    user_id: Any,
) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """返回当前对话最终放行的 ``(范围集合, 窗口集合)``。

    命中「每对话覆盖」则用覆盖项，否则回落到全局默认开关。

    Args:
        permission_config: 该权限组的配置对象（鸭子类型）。
        group_id: 当前对话的群号（私聊为空）。
        user_id: 当前对话的账号。

    Returns:
        Tuple[FrozenSet[str], FrozenSet[str]]: 放行的范围集合与窗口集合。
    """

    overrides = _parse_scope_overrides_cached(_raw_override_entries(permission_config))
    override = overrides.get(conversation_key(group_id, user_id))
    if override is None:
        return default_allowed(permission_config)
    return override.scopes, override.windows


def check_conversation_access(
    permission_config: Any,
    *,
    group_id: Any,
    user_id: Any,
    is_local_operator: bool,
) -> bool:
    """判定当前对话是否有权访问该权限组（只判「对话流名单」，不判范围/窗口）。

    判定顺序：

    1. 本地调试终端（local operator）始终放行；
    2. 用户在 ``blacklist_users`` 中 → **始终拒绝**（优先于用户白名单）；
    3. 用户在 ``whitelist_users`` 中 → **始终放行**（只绕过对话流名单，范围/窗口仍受放行列表约束）；
    4. 群在 ``blacklist_groups`` 中 → 拒绝（不区分名单制度）；
    5. ``blacklist`` 模式 → 未命中黑名单即放行；
    6. ``whitelist`` 模式（默认）→ 群聊要求群在 ``whitelist_groups`` 中；
       私聊没有群可判定，必须命中用户白名单。

    Args:
        permission_config: 该权限组的配置对象（鸭子类型）。
        group_id: 当前对话的群号（私聊为空）。
        user_id: 当前对话的账号。
        is_local_operator: 是否本地调试终端。

    Returns:
        bool: 是否有权访问。
    """

    if is_local_operator:
        return True

    normalized_user = str(user_id or "").strip()
    blacklist_users = _normalized_items(permission_config, "blacklist_users")
    if normalized_user and normalized_user in blacklist_users:
        return False
    if normalized_user and normalized_user in _normalized_items(permission_config, "whitelist_users"):
        return True

    normalized_group = str(group_id or "").strip()
    is_group_chat = bool(normalized_group)
    if is_group_chat and normalized_group in _normalized_items(permission_config, "blacklist_groups"):
        return False

    mode = str(getattr(permission_config, "permission_mode", "") or "").strip().lower()
    if mode == "blacklist":
        return True
    if not is_group_chat:
        return False
    return normalized_group in _normalized_items(permission_config, "whitelist_groups")


def parse_scope_overrides(entries: Sequence[Any]) -> Dict[Tuple[str, str], ConversationOverride]:
    """解析「聊天流类型|号码|可查询范围|可查询窗口」形式的每对话覆盖列表。

    条目规则：

    - 必须 4 段，且聊天流类型可识别、号码非空，否则整条跳过并记一条 warning；
    - 范围 / 窗口段用逗号（中英文均可）分隔；该段写 ``*`` 表示整个维度全部放行，
      写空串表示该维度全部禁止；
    - 段内无法识别的 token 只跳过该 token 并记一条 warning，其余 token 继续生效；
    - 同一 ``(聊天流类型, 号码)`` 重复出现时，后一条覆盖前一条并记一条 warning。

    Args:
        entries: 配置里的覆盖项列表。

    Returns:
        Dict[Tuple[str, str], ConversationOverride]: ``(类型, 号码)`` 到覆盖内容的映射。
    """

    return dict(_parse_scope_overrides_cached(tuple(str(item or "") for item in (entries or []))))


def scope_labels(values: Sequence[str]) -> str:
    """把范围集合拼成可读文本（用于日志）。"""

    return "、".join(SCOPE_LABELS.get(value, value) for value in values) or "无"


def window_labels(values: Sequence[str]) -> str:
    """把窗口集合拼成可读文本（用于日志）。"""

    return "、".join(WINDOW_LABELS.get(value, value) for value in values) or "无"


def _raw_override_entries(permission_config: Any) -> Tuple[str, ...]:
    """取出权限组里的覆盖项并规范化为可哈希的元组（供缓存使用）。"""

    entries = getattr(permission_config, "scope_overrides", None) or []
    return tuple(str(item or "") for item in entries)


@lru_cache(maxsize=32)
def _parse_scope_overrides_cached(entries: Tuple[str, ...]) -> Dict[Tuple[str, str], ConversationOverride]:
    """带缓存的覆盖项解析（避免每次请求重复打印配置告警）。"""

    overrides: Dict[Tuple[str, str], ConversationOverride] = {}
    for raw_entry in entries:
        entry = raw_entry.strip()
        if not entry:
            continue
        parts = _split_override_entry(entry)
        if len(parts) != 4:
            logger.warning(
                "[token_usage_report] 每对话范围覆盖格式非法，已跳过：%s"
                "（应为「聊天流类型|号码|可查询范围|可查询窗口」，例如「群聊|123456|当前对话,全部会话|今日,本周」）",
                entry,
            )
            continue
        chat_type = normalize_chat_type(parts[0])
        target_id = parts[1]
        if chat_type is None or not target_id:
            logger.warning(
                "[token_usage_report] 每对话范围覆盖的聊天流类型或号码非法，已跳过：%s"
                "（类型可填 群聊/私聊，号码填群号或账号）",
                entry,
            )
            continue
        key = (chat_type, target_id)
        if key in overrides:
            logger.warning("[token_usage_report] 每对话范围覆盖重复，后一条覆盖前一条：%s", entry)
        overrides[key] = ConversationOverride(
            scopes=frozenset(_parse_dimension_tokens(parts[2], normalize_scope, SCOPE_VALUES, "可查询范围", entry)),
            windows=frozenset(
                _parse_dimension_tokens(parts[3], normalize_window_token, WINDOW_VALUES, "可查询窗口", entry)
            ),
        )
    return overrides


def _split_override_entry(entry: str) -> List[str]:
    """按竖线切分覆盖项（同时接受中文全角竖线）。"""

    parts = [entry]
    for separator in _OVERRIDE_SEPARATORS:
        if separator in entry:
            parts = entry.split(separator)
            break
    return [part.strip() for part in parts]


def _parse_dimension_tokens(
    raw_tokens: str,
    normalizer: Any,
    all_values: Sequence[str],
    dimension_label: str,
    entry: str,
) -> List[str]:
    """解析覆盖项某一维度的 token 列表（范围或窗口）。"""

    text = str(raw_tokens or "").strip()
    if not text:
        return []
    if text == _WILDCARD:
        return list(all_values)

    resolved: List[str] = []
    for raw_token in text.replace("，", ",").split(","):
        token = raw_token.strip()
        if not token:
            continue
        if token == _WILDCARD:
            resolved.extend(all_values)
            continue
        normalized = normalizer(token)
        if normalized is None:
            logger.warning(
                "[token_usage_report] 每对话范围覆盖的%s无法识别，已忽略该项：%s（出现在 %s）",
                dimension_label,
                token,
                entry,
            )
            continue
        if normalized not in resolved:
            resolved.append(normalized)
    return resolved


def _normalized_items(permission_config: Any, field_name: str) -> FrozenSet[str]:
    """读取权限组里的名单字段并规范化为去空白的集合。"""

    raw_items = getattr(permission_config, field_name, None) or []
    return frozenset(str(item or "").strip() for item in raw_items if str(item or "").strip())
