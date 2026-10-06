# Token 用量统计与播报（MaiBot 插件）

一个只依赖 MaiBot 宿主能力的统计插件：统计 Bot 自身消耗的 Token，支持**会话维度筛选**、**图片报告**、**每日定时播报**与 **LLM 风格化转述**。

- 插件 ID：`cssqy.token-usage-report`
- 仓库：<https://github.com/CSSQY/token_usage_report>
- 目录：`plugins/token_usage_report/`
- 宿主版本要求：`1.3.0 ~ 1.3.99`；SDK 要求：`2.9.0 ~ 2.99.99`

## 1. 功能特性

- **LLM 工具 `query_token_usage`**：查询 `今日 / 本周 / 本月 / 最近24h / 最近7×24h / 最近30×24h` 与「总计」的 Token 消耗，可指定统计范围（全部会话 / 当前对话 / 指定群聊 / 指定用户）。
- **`/token` 指令**：立即统计并在指令来源处回复（哪里发的返回哪里），支持 `all`、`群 <群号>`、`用户 <QQ号>`。
- **每日定时播报**：按配置的多个每日时刻，向多个 QQ 群 / QQ 号推送报告；漏过的时刻不补发、执行失败不重试（仅记一次日志）。
- **两种呈现**：自定义文本模板（占位符）或 LLM 风格化转述（**复用宿主已分配的模型组**，并自动注入宿主人格与表达风格）。
- **图片报告**：4 套内置模板（简约白卡片 / 深色数据面板 / 手账便签 / 榜单风），含 **5 个条形图 + 6 个扇形图**与模型用量排行表；每个图表可单独开关。
- **中文单位**：自动使用 万亿 / 亿 / 万 与 天 / 小时 / 分钟 / 秒。
- **模型别名**：可配置「内部名=显示名」，统计与图片中一律显示别名。
- **渲染失败自动回退文字版本**：图片渲染失败时记录一次错误日志并改为发送文字报告。

## 2. 安装与启用

1. 把整个 `token_usage_report/` 目录放到 MaiBot 的 `plugins/` 下（保持目录名不变）。
2. 启动 / 重启 MaiBot；插件会在插件运行时（Runner）进程里被自动发现并加载。
3. 首次加载时，Runner 会按插件配置模型自动生成 `plugins/token_usage_report/config.toml`，之后修改该文件即可热更新配置（无需重启）。
4. 在 WebUI 的插件页面可以查看插件状态，并按配置模型自动生成的表单修改配置。

依赖说明：插件只使用宿主提供的 `maibot-plugin-sdk` 与自己声明的 `pillow`（宿主已自带），无需手动安装任何包。

## 3. 使用示例

### 3.1 `/token` 指令

```
/token [范围] [时间]
```

| 参数 | 可填内容 |
|---|---|
| 范围（可省略） | 省略 = 当前对话；`all` / `全部` / `全局` / `所有` = 全部会话；`群 <群号>`（也支持 `群聊`）；`用户 <QQ号>`（也支持 `个人` / `私聊`） |
| 时间（可省略） | 省略 = 报告里展示全部 6 个窗口；可填 `今日` / `本周` / `本月` / `最近24小时` / `最近7天` / `最近30天`，也支持 `今天`、`这周`、`这个月`、`一天`、`一周`、`一个月`、`24h`、`7d`、`30d` 等写法 |

**两个参数顺序不限**，时间可以写在最前面：

| 指令 | 效果 |
|---|---|
| `/token` | 当前对话，全部时间窗口 |
| `/token 今日` | 当前对话，只统计今日 |
| `/token all` | 全部会话，全部时间窗口 |
| `/token all 本周` | 全部会话，只统计本周 |
| `/token 群 123456` | 指定群，全部时间窗口 |
| `/token 群 123456 最近7天` | 指定群，只统计最近 7 天 |
| `/token 本周 群 123456` | 同上（顺序不限） |
| `/token 用户 10001 一个月` | 指定用户，只统计最近 30 天 |

指定时间后：

- 报告**只保留所选窗口**：其他窗口的行整行消失（不会留下「本周：（次请求 /）」这类空壳行）；
- **整份报告都收敛到该窗口**：时间窗口卡片、趋势图、模型排行、模块/模型占比、聊天消息分布、消息数 / 回复数 / 在线时长与派生指标，全部只统计该窗口内的数据；
- 因此不会再出现「今日」报告里混进一年数据的情况；与窗口卡片重复的那一行「总计（…）」会被整行丢弃；
- 报告末尾会自动追加一句范围说明；想看全部时间窗口 + 全时段数据，**去掉时间参数**即可（`/token`、`/token all`）。

其他说明：

- 指令回复优先发**图片**；图片渲染成功且 `report.mode = "template"` 时**不再重复发送同内容的文字**（文字与图片重复），只有渲染失败才回退文字版本；`report.mode = "llm"` 时风格化文字与图片内容不同，仍会照常发送。
- 参数写错会回复用法说明（含全部可填值），不会静默失败。
- 未找到目标会话时会明确提示（例如「未找到群 123456 的会话记录（该群可能尚未与 Bot 交互）」）。
- `群` / `用户` 后面紧邻的参数一律当作目标值，因此 `/token 群 本周` 会按「群号=本周」处理而不是误判成时间。

### 3.2 LLM 工具 `query_token_usage`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scope` | string | 否 | `current`（默认，取本次调用的会话上下文）/ `all` / `group` / `user` |
| `target_id` | string | 否 | `scope=group` 时的群号；`scope=user` 时的 QQ 号 |
| `window` | string | 否 | `今日` / `本周` / `本月` / `最近24小时` / `最近7天` / `最近30天`（也支持 `today` / `this_week` / `this_month` / `last_24h` / `last_7d` / `last_30d`）；留空返回全部窗口 |

工具侧的安全收口（与指令一致）：

- **权限**：与 `/token` 共用同一套黑白名单（用调用上下文里的群号 / QQ 号判定），未授权会话直接拒绝，模型问也拿不到数据；
- **范围**：只允许查询「统计指令 → 工具可查询范围」里列出的范围，**默认只有 `current`**，因此开箱状态下模型无法替任何人把全局账本或别的群的数据取回来；
- 会话上下文（`stream_id` / 群号 / QQ 号）由宿主注入，**不由模型提供**，模型无法自己指定要读哪个会话。

返回示例（节选）：

```json
{
  "success": true,
  "content": "统计范围：全部会话\n今日：12,345 Token，请求 45 次，花费 ¥0.1234\n……",
  "scope": "all",
  "windows": { "today": { "tokens": 12345, "requests": 45, "cost": 0.1234 } },
  "total": { "tokens": 987654, "requests": 3210, "cost": 1.2345 }
}
```

### 3.3 定时播报

在配置中设置 `report.enabled = true`，并把 `report.schedule_times` 写成希望发送的时刻（如 `["08:00", "20:00"]`），再在 `report.target_groups` / `report.target_users` 填写目标群号 / QQ 号即可。到达时刻后插件会自动统计并推送；如果某个时刻已过去，不会补发。

## 4. 配置说明

配置文件为插件目录下的 `config.toml`（由 Runner 依据下方默认值自动生成与补齐）。

### 4.0 在 WebUI 里怎么填

插件向 WebUI 提供了完整的配置 Schema，打开插件的配置页即可看到按下面 9 个分节分组的表单（分节顺序即 `__ui_order__`）：

| 表单控件 | 出现在哪些字段 | 怎么用 |
|---|---|---|
| 开关 | `enabled`、`use_persona`、`use_image`、`send_image`、`anonymize`、`allow_network`、`notify_no_permission`、所有图表开关 | 直接切换开/关 |
| 输入框 | `unit_name`、`platform`、`llm_task_name`、`deny_message`、超时/视口等数值 | 数值项已带步进（如超时 1000 毫秒一步） |
| 下拉选择 | `report.mode`、`command.permission_mode`、`render.template_name`、`render.image_format`、`chart.bar_granularity` | 选项即为配置里的英文/短值，每个值的含义写在字段下方的说明里 |
| **列表编辑器** | 所有群号/QQ 号列表、模型别名、字体服务组、模块分组前缀 | **在输入框填好内容后点右侧「+」按钮添加**（也可按回车），每项显示为一行并带删除按钮 |
| 多行文本 | `template`、`persona_template`、`persona_extra`、`llm_prompt` | 已设置合适高度，支持换行与占位符 |
| 滑块 | `jpeg_quality`、`resolution_scale`、`series_top`、`top_models`、`top_modules` | 拖动即可，取值范围由 Schema 限定 |
| 分组卡片 | 每个 `[...]` 配置节 | 卡片标题即该节中文名（插件 / Token 单位 / 模型别名 / 定时播报 / 统计指令 / 图片渲染 / 图表 / 模块分组 / 读取上限） |

每个字段在表单里都有三样东西，用来回答「填什么、干什么、能填什么」：

1. **中文短名**（`label`）：例如「模型组（任务配置名）」；
2. **字段下方的说明文字**（`hint`）：直接显示在控件下面，写清该字段的作用、取值范围与注意事项 —— 插件配置页只渲染 `hint`，所以每个字段都填了它；
3. **输入示例**（`placeholder`）：例如 `内地|https://fonts.loli.net|https://gstatic.loli.net`、`123456789`、`内部名=显示名`。

> 列表类字段必须声明 `x-widget: list`（宿主 Dashboard 用它渲染「输入框 + 添加按钮 + 已添加项」编辑器）；早期用 `tags` 会退化成普通输入框、无法增删条目。

模板类字段的**说明文字里直接列了全部可用占位符**，无需查文档：

| 字段 | 说明里包含 |
|---|---|
| `report.template` | 全部 54 个占位符（基础 / 6 个窗口 × 4 / 总计 / 消息与运营 / 缓存 / 多行区块） |
| `report.persona_template` | `{bot_name}`、`{personality}`、`{reply_style}` 三个占位符及其读取来源 |
| `report.llm_prompt` | 说明它是「任务说明」，人格会自动拼在前面、数据会自动附在后面 |

> 插件自带一致性校验：冒烟测试会比对「说明里列的占位符」与「代码实际产出的占位符」，两者不一致会直接报错，避免文档漂移。

### 4.0.1 开箱默认值（装完即可用）

按下表核对默认值，除「定时播报」外都不需要改动就能直接使用：

| 分节 | 默认状态 | 说明 |
|---|---|---|
| `[plugin]` | `enabled = true` | 插件加载即生效 |
| `[token_unit]` | `unit_name = "Token"` | 想换成 鸡蛋/词元 直接改这一个字段 |
| `[model_aliases]` | 空列表 | 可选；不配就显示宿主里的原始模型名 |
| `[report]` | `enabled = false`、`mode = "template"` | 定时播报默认关闭（避免未经确认就发消息）；要开启需先填 `target_groups` / `target_users` |
| `[command]` | `enabled = true`、`permission_mode = "whitelist"`、`use_image = true`、`tool_allowed_scopes = ["current"]` | **默认只允许白名单里的群用指令**：先把自己的群号填进 `whitelist_groups`（或把 `permission_mode` 改成 `all` 全面开放）；LLM 工具默认只能查当前对话 |
| `[render]` | `anonymize = true` | 报告里的群名/昵称默认匿名化成 `群聊A` / `个人用户A`，避免群里任何人一句 `/token` 就把别的群晒出来 |
| `[render]` | `template_name = "simple"`、`image_format = "png"`、内置内地+海外两组字体服务 | 联网即可渲染中文；渲染不可用时自动回退文字 |
| `[chart]` | 11 个图表开关全为 `true` | 图片报告默认包含 5 条形图 + 6 扇形图 |
| `[module_groups]` | 已按宿主内置模块名预置 | 无需配置即可得到「计划器/回复器/图片/记忆/表情/插件/其他」分组 |
| `[limits]` | `max_session_rows = 20000`、`top_models = 20`、`top_modules = 8` | 适配常见数据量；会话明细超上限会明确报错而不是给错数字 |

> 安全提示（隐私与权限边界）：**默认配置是收紧的** —— `command.permission_mode` 默认 `whitelist`、`render.anonymize` 默认 `true`、`command.tool_allowed_scopes` 默认只有 `current`。
> 也就是说，装完不改配置时：只有 `whitelist_groups` 里列出的群能用 `/token`（未列出的群一律拒绝），图片里的群名/昵称一律匿名化成 `群聊A` / `个人用户A`，LLM 工具也只能查当前对话——**不会出现「群里第一个人发一句 `/token all` 就把全局账本和别的群昵称翻出来」**。
> 若确实要开放，请显式改配置并自行承担相应风险：把 `permission_mode` 改成 `all`（任何群可用）或 `blacklist`（仅屏蔽黑名单群），把 `anonymize` 改成 `false`（显示真实群名/昵称），把 `tool_allowed_scopes` 加上 `all` / `group` / `user`。想彻底关掉指令把 `enabled` 设为 `false` 即可。

### 4.1 `[plugin]`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 是否启用插件 |
| `config_version` | str | `"1.8.0"` | 配置版本，升级配置结构时递增 |

### 4.2 `[token_unit]`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `unit_name` | str | `"Token"` | Token 的显示单位名称，例如 鸡蛋 / 词元 / 白饭 |

### 4.3 `[model_aliases]`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `aliases` | list[str] | `[]` | 每条格式为 `内部名=显示名`，例如 `deepseek-v4-flash=小深`；排行、图表与文本中均显示别名 |

**别名合并规则**：多条别名可以指向同一个显示名，此时这些内部模型会被**合并成一项统计** ——

```
aliases = [
  "deepseek-v4-flash=小深",
  "deepseek-v4-flash-20260101=小深",   # 同一个显示名 -> 与上面合并
  "glm-4-flash=小快",
]
```

- **调用次数、Token、费用**：先分别求和再展示（上例合并后是 1 项「小深」，次数/Token/费用为两者之和）；
- **平均耗时**：按调用次数**加权平均**（例如 100 次 @2.0s 与 300 次 @4.0s → 3.5s，而不是简单平均 3.0s）；
- **占比图（扇形图）**：同一别名只出现一片，占比之和不会重复计算；
- **各处口径一致**：模型排行表、模型 Token/花费扇形图、模型花费趋势条形图、会话视图，全部按显示名合并；
- 发生合并时会记录一条 info 日志（列出被合并的内部名），便于核对是否符合预期；
- 未配置别名的模型保持内部原名，互不合并。

### 4.4 `[report]` 定时播报

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `false` | 是否启用每日定时播报（默认关闭，避免未经确认就向群里发消息） |
| `schedule_times` | list[str] | `["08:00", "20:00"]` | 每日发送时刻（24 小时制 `HH:MM`），可填多个；漏过不补发 |
| `platform` | str | `"qq"` | 目标平台标识 |
| `target_groups` | list[str] | `[]` | 接收播报的 QQ 群号 |
| `target_users` | list[str] | `[]` | 接收播报的 QQ 号（私聊） |
| `mode` | str | `"template"` | `template`=文本模板；`llm`=复用宿主模型组做风格化转述 |
| `template` | str | 见默认模板 | 文本模板，支持下方占位符表 |
| `llm_task_name` | str | `"utils"` | **复用的宿主模型组**，即 `model_config.toml` 里的任务配置名（如 `utils` / `replyer` / `planner`）|
| `use_persona` | bool | `true` | 转述时自动读取宿主人格（`[personality]` 的人格设定与表达风格、`[bot]` 的昵称）并注入提示词 |
| `persona_template` | str | `""`（内置拼装） | 人格提示词模板，可用占位符 `{bot_name}`、`{personality}`、`{reply_style}` |
| `persona_extra` | str | `""` | 额外提示词，追加在人格提示词之后（`use_persona=false` 时它仍然生效） |
| `llm_prompt` | str | 见默认提示词 | 转述任务说明（不含数据本体与人格） |
| `send_image` | bool | `true` | 播报时是否附带图片 |

**4.4.1 复用宿主模型组与人格（`mode = "llm"` 时生效）**

- **模型组**：`llm_task_name` 直接复用宿主 `model_config.toml` 中已分配好的任务配置（模型、温度等都在宿主侧维护），插件不额外配置模型。填了不存在的名字时由宿主按默认策略解析，插件会在日志中提示可选列表。
- **人格**：`use_persona = true` 时，插件通过宿主 `config.get` 能力读取全局配置：

  | 占位符 | 读取的配置键 | 来源 |
  |---|---|---|
  | `{bot_name}` | `bot.nickname` | `[bot] nickname` |
  | `{personality}` | `personality.personality` | `[personality] personality` |
  | `{reply_style}` | `personality.reply_style` | `[personality] reply_style` |

  拼装规则：

  1. `persona_template` 留空 → 用内置拼装（昵称/人格/表达风格各一行，跳过空值）；
  2. `persona_template` 填了内容 → 按占位符渲染，例如
     `你是{bot_name}。你的人格设定：{personality}。你的表达风格：{reply_style}`；
     模板语法错误会记录一次错误日志并回退到内置拼装；
  3. `persona_extra` 追加在最后；`use_persona = false` 时只保留 `persona_extra`。

  最终提示词结构为：`人格提示词` + `llm_prompt` + `统计数据`。读取人格失败只缺片段并留一条警告，不影响出报告。
- **发送**：转述结果仍由插件自己调用宿主的 `send.text` / `send.image` 能力发出（不经过回复器/规划器链路，也不占用聊天流的上下文）。

### 4.5 `[command]` `/token` 指令

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 是否启用 `/token` 指令 |
| `permission_mode` | str | `"whitelist"` | 群名单制度：`whitelist` 启用群白名单（**默认，只有白名单内的群可用**）；`all` 不启用群白名单（群黑名单仍生效）；`blacklist` 启用群黑名单 |
| `whitelist_groups` | list[str] | `[]` | 群白名单：`whitelist` 模式下仅这些群可用；与群黑名单冲突时黑名单优先 |
| `whitelist_users` | list[str] | `[]` | 用户白名单：命中的用户**始终可用**（优先于群名单；与用户黑名单冲突时黑名单优先） |
| `blacklist_groups` | list[str] | `[]` | 群黑名单：命中的群在**任何模式下**都不可用（优先于群白名单） |
| `blacklist_users` | list[str] | `[]` | 用户黑名单：命中的用户**始终不可用**（最高优先级，优先于用户白名单） |
| `notify_no_permission` | bool | `true` | 无权限时是否回复提示；关闭后无权限调用完全静默 |
| `deny_message` | str | `"你没有权限使用该指令"` | 无权限时的提示内文；留空则不回复 |
| `tool_allowed_scopes` | list[str] | `["current"]` | **LLM 工具 `query_token_usage` 允许查询的范围**（与指令共用上面的黑白名单，但范围另受此列表限制）：可填 `current` / `all` / `group` / `user`，留空 = 禁止模型通过工具查询任何范围 |
| `use_image` | bool | `true` | 指令回复是否使用图片渲染 |

**权限判定规则（严格按顺序）**：

1. 本地调试终端（local operator）始终放行；
2. 用户在 `blacklist_users` 中 → **始终拒绝**（用户层黑名单优先于用户白名单）；
3. 用户在 `whitelist_users` 中 → **始终放行**（优先于群名单，包括群黑名单）；
4. 群在 `blacklist_groups` 中 → **拒绝**（群层黑名单优先于群白名单，且不区分名单制度）；
5. `permission_mode = "whitelist"`（默认）→ 群聊要求群号在 `whitelist_groups` 中；私聊没有群可判定，只能靠用户白名单，未列入即拒绝；
6. `permission_mode = "all"` → 放行（不启用群白名单）；
7. `permission_mode = "blacklist"` → 群聊未命中群黑名单即放行（命中已在第 4 步处理）；私聊放行。

> 冲突处理：**同一个用户同时出现在用户黑白名单 → 黑名单胜出**；**同一个群同时出现在群黑白名单 → 黑名单胜出**。用户名单优先于群名单，用户白名单可以放行一个处于群黑名单中的群（见下方示例）。

被拒绝时的回复由两个配置共同决定：`notify_no_permission = false`（关闭提示）或 `deny_message` 留空 → **完全静默**，不发送任何消息。

对应示例：

- 用户在 `whitelist_users`，`permission_mode = "whitelist"` 且群不在 `whitelist_groups` → **通过**；
- 用户在 `whitelist_users`，`permission_mode = "blacklist"` 且群在 `blacklist_groups` → **通过**（用户白名单优先于群名单）；
- 用户在 `blacklist_users` → 无论模式与群名单如何 → **不通过**；
- 用户同时在黑白名单中 → **不通过**；
- 群同时在群白名单与群黑名单中 → **不通过**（任何模式下）。

### 4.6 `[render]` 图片渲染

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `template_name` | str | `"simple"` | 图片模板名称：4 个内置名，或「自定义模板」里定义的名称 |
| `custom_templates` | list[str] | `[]` | **可添加**的自定义图片模板，见 4.6.2 |
| `image_format` | str | `"png"` | `png` 或 `jpeg` |
| `jpeg_quality` | int | `90` | JPEG 质量（1~100，仅 `jpeg` 生效） |
| `resolution_scale` | float | `2.0` | 分辨率等级（设备像素比，1.0~4.0） |
| `first_round_timeout_ms` | int | `15000` | 第一轮渲染超时（毫秒） |
| `second_round_timeout_ms` | int | `25000` | 第二轮（回退轮）渲染超时（毫秒） |
| `viewport_width` / `viewport_height` | int | `1000` / `600` | 第一轮渲染视口 |
| `fallback_viewport_width` / `fallback_viewport_height` | int | `900` / `1200` | 第二轮（回退轮）渲染视口兜底宽高 |
| `allow_network` | bool | `true` | 是否允许渲染页面访问外部网络（加载字体 CDN 需要）；**注意**：渲染页面会访问 `font_services` 里的地址，插件只校验其为 `http(s)` 前缀，因此部署者若把它配成 `127.0.0.1` 或内网 / 云元数据地址（如 `169.254.169.254`），渲染页面也会去访问这些地址——配置权在部署者手上、不受聊天输入影响，请自行确认 `font_services` 只填可信的字体服务 |
| `font_services` | list[str] | 内地 + 海外两组 | 字体服务组，见 4.6.1 |
| `show_details` | bool | `true` | 是否输出**模型用量排行表**（图片末尾的表格，含 # 名次、Token、调用次数、费用、平均耗时、占比）；文本模板里的 `{details}` 占位符同样受它控制 |
| `anonymize` | bool | `true` | 是否匿名化聊天对象（**默认开启**）：**聊天消息分布扇形图**与**各聊天流消息数趋势图**里的群名/昵称统一显示为 `群聊A` / `个人用户A`（拿不到会话类型时显示 `会话X`），文字报告与图片报告口径一致；改成 `false` 会显示真实群名/昵称 |

**4.6.1 字体服务组格式**：每条为 `名称|CSS 基地址|静态字体基地址`，按列表顺序作为优先级：

```toml
font_services = [
  "内地|https://fonts.loli.net|https://gstatic.loli.net",
  "海外|https://fonts.googleapis.com|https://fonts.gstatic.com",
]
```

- 渲染时按顺序使用第一个服务组；若本轮渲染失败（超时或异常）自动换下一组重试；两组都失败后进入第二轮（兜底视口）再按同样顺序重试。
- 整条渲染管线有 **45 秒总预算**（常量，防止超出宿主 60 秒组件调用超时）；预算耗尽会直接放弃并回退文字版本。
- 条目格式非法（不是 3 段或不是 http(s) 地址）会被跳过并记录一次警告。

> **最低 SDK 版本**：`render.html2png` 的超时参数在 SDK 里由旧版的 `timeout_ms` 改名为 `render_timeout_ms`。插件优先用 `render_timeout_ms` 调用，仅在捕获到 `TypeError` 且异常信息里含 `render_timeout_ms` 时才回退到旧名 `timeout_ms` 重试（[renderer.py](file:///h:/Files/Code/MaiBot%E6%8F%92%E4%BB%B6%E5%BC%80%E5%8F%91/plugins/token_usage_report/renderer.py)）。
> 这段兼容写法**依赖宿主的异常文案**：若宿主换了报错方式（不再是 `TypeError`，或信息里不再出现参数名），回退不会触发、渲染会直接失败并回退文字版。因此请确保宿主 / SDK 版本满足 [`_manifest.json`](file:///h:/Files/Code/MaiBot%E6%8F%92%E4%BB%B6%E5%BC%80%E5%8F%91/plugins/token_usage_report/_manifest.json) 里声明的 `sdk.min_version`（`2.9.0`）——该版本起 `render.html2png` 即接受 `render_timeout_ms`，走的是主路径，不依赖上面的兼容回退。兼容分支仅用于兜底更早的 SDK，未来若确认不再需要会移除。

**4.6.2 自定义图片模板（可添加，无需改代码）**

内置 4 套模板：`simple` 简约白卡片 / `dark` 深色数据面板 / `handbook` 手账便签 / `rank` 榜单风。除了这 4 套，你可以在配置里**自己添加任意数量的模板**：

1. 打开插件配置页 → 「图片渲染」分节 → **自定义模板**；
2. 在输入框按下面的格式填一条，点右侧 **+** 添加（可重复添加多条）：
   ```
   名称|基础样式|主色|页背景色|卡片底色
   ```
   | 段 | 必填 | 说明 |
   |---|---|---|
   | 名称 | 是 | 自定义模板名，不能与 4 个内置名重名 |
   | 基础样式 | 是 | 取 `simple` / `dark` / `handbook` / `rank`，决定排版与字体 |
   | 主色 | 是 | `#RRGGBB`，用于标题、图表主色等强调色 |
   | 页背景色 | 否 | `#RRGGBB`，省略则沿用基础样式 |
   | 卡片底色 | 否 | `#RRGGBB`，省略则沿用基础样式 |
3. 到上面的 **图片模板** 字段里填**你这个自定义模板的名称**，保存即可生效。

示例（可直接粘进输入框）：

```
夜蓝|dark|#5ea0ff|#0b1220|#151f33
奶油|simple|#c1743f|#fdf6e3|#fffdf5
极简|simple|#4F8CF6
```

规则与容错：

- 非法条目（段数不足、基础样式不是那 4 个、颜色不是 `#RRGGBB`、名称与内置名重复）会被**跳过并记录一条警告**，不影响其他条目；
- 「图片模板」填了不存在的名字时会记录一条 **error 日志**（日志里会列出当前可用的模板名）并回退 `simple`；
- 渲染成功时日志会打印实际使用的模板名与配置值，便于确认是否写错。

### 4.7 `[chart]` 图表

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `bar_granularity` | str | `"day"` | 条形图颗粒度：`hour` / `day` / `week` / `month` |
| `chart_style` | str | `"bar_line"` | 趋势图样式：`bar`（仅条形）/ `line`（仅平滑曲线）/ `bar_line`（条形+平滑曲线叠加） |
| `bar_days` | int | `30` | 条形图横轴范围（天） |
| `series_top` | int | `5` | 多序列图显示前 N 个序列，其余归入「其他」 |
| `bar_tokens` | bool | `true` | 条形图：总 Token 趋势 |
| `bar_cost` | bool | `true` | 条形图：总花费趋势 |
| `bar_model_cost` | bool | `true` | 条形图：各模型花费趋势 |
| `bar_module_cost` | bool | `true` | 条形图：各模块花费趋势 |
| `bar_chat_messages` | bool | `true` | 条形图：各聊天流消息数趋势 |
| `pie_module_tokens` | bool | `true` | 扇形图：模块 Token / 请求占比 |
| `pie_module_cost` | bool | `true` | 扇形图：模块花费分布 |
| `pie_model_tokens` | bool | `true` | 扇形图：模型 Token 占比 |
| `pie_model_cost` | bool | `true` | 扇形图：模型花费分布 |
| `pie_model_requests` | bool | `true` | 扇形图：模型调用量分布（各模型被调用次数的占比） |
| `pie_chat_messages` | bool | `true` | 扇形图：聊天消息分布 |

关闭某个图表后，该图表不再出现在图片中，也不会触发对应的宿主能力调用。

`chart_style` 对五个趋势图统一生效：`bar_line`（默认）先画半透明条形、再在柱顶叠加同色**平滑曲线**与数据点，既能看单点数值又能看走势；`line` 只画平滑曲线（点少时带数据点标记）；`bar` 保持原来的纯条形样式。曲线用 Catmull-Rom 转三次贝塞尔生成，**严格经过每个数据点**（数据点、柱顶、曲线三者对齐），控制点会被夹在绘图区内，数值突变时也不会跌到 0 轴以下。

### 4.8 `[module_groups]` 模块分组

宿主统计中的模块名是 `request_type` 中第一个 `.` 之前的部分（例如 `plugin.xxx` → `plugin`、`A_Memorix.EpisodeSegmentation` → `A_Memorix`）。这里配置各中文分组对应的**模块名前缀**，未命中的模块会归入「其他」并以 `info` 级别日志记录一次，便于你补充映射。

| 字段 | 默认值 |
|---|---|
| `planner`（计划器） | `["planner", "maisaka.plan", "plan"]` |
| `replyer`（回复器） | `["replyer", "maisaka.replyer", "response.splitter", "reply", "response", "smart_segmentation"]` |
| `image`（图片） | `["image", "vlm", "A_Memorix.ImageEmbedding"]` |
| `memory`（记忆） | `["memory", "A_Memorix", "maisaka", "heuristic_memory_impression", "memory_feedback_correction", "memories", "person_fact_writeback", "chat_history_summarizer", "dream", "story", "expression", "jargon", "behavior", "embedding"]` |
| `emoji`（表情） | `["emoji"]` |
| `plugin`（插件） | `["plugin", "mcp_sampling"]` |

> 插件在运行时会以 `info` 日志列出「未被模块分组覆盖的模块名」，把这些名字按前缀补进对应分组即可让占比图更准确。若看到仍未归类的名字（例如 `tool_executor`、`generator_api`、`prompt_injection_detection`），直接加到最合适的分组前缀里即可。

### 4.9 `[limits]` 读取上限

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `max_session_rows` | int | `20000` | 会话维度明细最大读取行数；**超出时直接报错放弃统计**（不截断取样） |
| `top_models` | int | `20` | 模型排行与多序列图显示的模型条数（宿主上限 50） |
| `top_modules` | int | `8` | 模块花费统计的模块个数（每个模块多一次能力调用） |

### 4.10 文本模板占位符

**占位符随取随用**：下表 54 个占位符**不要求全部使用**，想显示哪个就写哪个 ——

- 没写的占位符**不会出现在报告里**（不会留下「{xxx}」这样的原始文本）；
- 只用了部分占位符时，空出来的行会被**自动压掉**（连续空行压缩为一个、首尾空白去除），排版不会留空洞；
- 只写一行也可以（例如 `今日消耗：{today}`），不会被强制套上默认格式；
- 写错的占位符会被**置空**并记录一条 warning 日志（日志关键字「文本模板使用了未知占位符」），便于你发现拼错；
- 模板语法错误（如落单的花括号）会记录一条 error 并用内置默认模板兜底；
- 正文里想显示花括号本身，写成 `{{` 与 `}}` 即可（`{{原样}}` → `{原样}`）。

| 占位符 | 含义 |
|---|---|
| `{date}` | 报告生成时间 |
| `{unit_name}` | Token 单位名（来自 `token_unit.unit_name`） |
| `{scope}` / `{scope_name}` | 统计范围标识 / 中文描述 |
| `{total_scope}` | 总计口径说明（全局=最近 365 天；会话=该会话全部历史） |
| `{today}` `{this_week}` `{this_month}` `{last_24h}` `{last_7d}` `{last_30d}` | 各窗口的 Token 数（含单位名） |
| `{<窗口>_raw}` | 同上，纯数字 |
| `{<窗口>_requests}` / `{<窗口>_cost}` | 各窗口的请求数与花费 |
| `{total_tokens}` `{total_raw}` `{total_prompt}` `{total_completion}` `{total_requests}` `{total_cost}` `{total_avg_response}` | 总计区块 |
| `{messages}` `{replies}` `{received_messages}` `{online_duration}` | 消息数 / 回复数 / 接收消息数 / 在线时长 |
| `{cost_per_100_messages}` `{cost_per_100_received}` `{cost_per_100_replies}` `{cost_per_hour}` `{tokens_per_hour}` | 派生指标 |
| `{cache_hit_rate}` `{cache_hit_tokens}` `{cache_miss_tokens}` | 缓存指标（只有会话视图有值；全局视图无数据，引用它们的行会整行隐藏） |
| `{model_ranking}` `{module_breakdown}` `{chat_message_share}` `{details}` | 多行文本区块（模型排行 / 模块占比 / 聊天消息分布 / 详细数据） |
| `{unavailable}` `{notes}` | 本次不可用的数据区块 / 统计口径脚注 |

说明：模板使用 Python `str.format_map` 语法，未知占位符会置空并记录一次警告；模板语法错误会回退为内置默认模板。

## 5. 统计口径与已知限制

- **总量包含全部模块**：报告统计的是**所有模块**的 Token——除了聊天链路（计划器 + 回复器）的对话调用，还有记忆抽取/写入、embedding、图片理解(VLM)、表情向量、插件等**后台流水线**。这些流水线不随你发消息的多少变化，且 prompt/输出都很长，往往是总量的大头。总览指标里专门有一格 **「聊天链路 Token（占 X%）」**，想核对「我到底用了多少」时请看这一格。
- **总计与「输入 + 输出」可能对不齐**：插件的「总计」= 宿主聚合表的 `SUM(total_tokens)`，即**供应商原样上报的 total_tokens 之和**；部分供应商会把缓存 / 推理 token 也算进 `total_tokens`，因此总计可能**略大于**「输入 + 输出」（零点几个百分点）。宿主 WebUI 的合计用的是 `SUM(prompt_tokens) + SUM(completion_tokens)`，所以拿插件的「总计」去对 WebUI 的 token 天然会有这点差异；要严格对齐请用「输入 + 输出」。
- **模块 Token 与模块花费的粒度不同**：模块 Token 占比 / 请求次数按**完整请求类型**（`request_type`，如 `maisaka.replyer`）归类；模块花费受宿主能力限制（`model_trend` 只支持按 `module_name` 过滤）只能按 **`module_name`**（首个「.」之前的部分）归类，粒度更粗——`maisaka.planner` / `maisaka.replyer` / `maisaka.mid_term_memory` 在**花费**里会合并成 `maisaka`。因此「模块花费分布」里某一分组的占比可能明显高于「模块 Token 占比」里的同名分组。
- **在线时长的算法**：插件直读宿主 `online_time` 明细，按每条记录的 `end_timestamp - start_timestamp` 与统计区间求交集累加，与宿主 WebUI 的算法一致（单窗口模式下会正确切出「窗口内的那一段」，而不是整条丢弃或整条计入）。宿主自带的 `statistics.local.online_time_trend` 能力不可用——它 `SUM(duration_minutes)`，而该字段只在建记录时写死为 5、之后心跳只更新结束时间。
- **数据来源**：全局指标取宿主 `statistics.local.*` 聚合表（小时级聚合，**最新数据最长滞后约 15 分钟**）；会话维度取 `ModelUsage` 调用明细后在插件内聚合。
- **窗口口径**：「今日 / 本周 / 本月」按**本地日历零点**起算（今日 = 今天 00:00 至今），「最近 24 / 7×24 / 30×24 小时」按**滚动时长**回算；宿主的 WebUI 统计页只有滚动档位（24 小时 / 7 天 / 30 天），**没有「今日」这个口径**，所以不要拿插件的「今日」去对 WebUI 的数——要对比请用插件的「最近24小时」对 WebUI 的「24 小时」。
- **总计口径**：全局「总计」= **最近 365 天**（宿主统计能力上限，非无限回溯）；会话「总计」= 该会话全部历史记录（受 `max_session_rows` 行数上限约束）。
- **消息数与回复数**：只覆盖消息量前 50 会话与前 50 工具（宿主 `top_chats` / `top_tools` 上限）；回复数沿用宿主内置口径（`reply` 工具调用次数）。
- **模块花费**：宿主没有「按模块聚合花费」的能力，插件对 Token 排名前 `limits.top_modules` 个模块逐个查询后汇总，其余模块归入「其他」。
- **缓存指标**：宿主聚合表（`statistics_model_hourly`）不含缓存字段，因此缓存命中率 / 命中 Token / 未命中 Token **只有会话视图（`ModelUsage` 调用明细）能拿到**；全局视图不再输出这几项（KPI 里不显示、模板里引用它们的那一行会被整行隐藏），而不是显示「宿主不提供」。
- **会话维度**：不包含消息数 / 回复数 / 在线时长（宿主无按会话过滤这些指标的能力）；会话明细读取超上限时**直接报错**，不会给出被截断的错误数字。
- **会话视图与在线时长依赖宿主内部表结构（无对外承诺）**：会话维度通过 `database.get` 读取宿主的 `ModelUsage` 表（实际表名 `llm_usage`）并依赖其中的 `total_tokens` / `timestamp` 等列名；在线时长同样通过 `database.get` 读取宿主的 `OnlineTime` 表（实际表名 `online_time`）并依赖 `start_timestamp` / `end_timestamp`。`database.get` 是公开能力，但**表名与列结构属于宿主内部实现、文档没有承诺**。为此插件加了结构守卫：会话视图取到行却读不到已知字段时会**直接报错**（提示「宿主 ModelUsage 表结构与插件不兼容，缺少字段：…」），而不是用 `row.get(...) or 0` 把一切兜成 0、静默算出错误数字。若你看到这条报错，说明宿主升级改了表结构，请把报错反馈给插件作者。
- **指定时间后的口径**：`/token all 今日` 这类查询会把**整份报告**收敛到该窗口——插件的取数档位按窗口跨度选择，并在取数后按时间戳把每条序列裁剪到窗口起点，所以窗口卡片、趋势图、模型排行与占比分布来自同一份窗口内数据，彼此口径一致。「今日 / 本周 / 本月」以零点为起点，裁剪后完全精确；跨度超过 7 天的窗口（如「最近30×24小时」）改用天桶，最早不足一天的部分不计入。
- **统计范围**：`scope=all` 取全部会话的宿主聚合表；`current` / `group` / `user` 取对应会话的 `ModelUsage` 明细，所以指定会话 + 指定时间时同样只统计该窗口内的调用。
- **以下功能因宿主能力限制未实现**：
  1. 按群聊 / 用户的 **Token** 占比扇形图（用「聊天消息分布」替代展示）；
  2. 聊天流花费分布（会话维度的花费）；
  3. 请求类型花费分布 / 调用来源花费分布（只能按模型与模块两个维度聚合花费）；
  4. per-model 输入 / 输出 Token 拆分（仅全局总量提供输入 / 输出拆分）；
  5. 消息数 / 回复数的全量精确值（受前 50 上限）。

## 6. 常见问题

- **图片没发出来，只收到文字？** 说明渲染整体失败（字体服务不可用、Playwright 浏览器不可用、超出 45 秒预算等），插件会记录一条 `error` 日志并自动回退文字版本；可检查日志中的「渲染失败（第 N 轮 / 字体服务 …）」定位原因。
- **只收到图片、没有文字？** 这是有意行为：图片和模板文字内容重复，图片渲染成功就不再重复发一遍文字。想看文字版可以把 `command.use_image` 关掉（或让渲染失败）；`report.mode = "llm"` 的风格化转述内容与图片不同，不会因此被省略。
- **字体显示成默认字体？** 检查 `render.allow_network` 是否为 `true`，以及 `font_services` 中的地址在当前网络环境是否可访问。
- **数字与 WebUI 统计页面对不上？** 先确认时间窗口口径（WebUI 页面与插件窗口定义可能不同），再确认宿主聚合的最新延迟（约 15 分钟），最后确认上文中「前 50」等上限是否生效。
- **`/token 群 xxx` 提示找不到会话？** 说明该群尚未与 Bot 产生过聊天流记录；可以让群里先发一条消息，或在配置中改用私聊目标。
- **Pillow 需要自己安装吗？** 不需要，宿主已依赖 `pillow>=12.3.0`；插件的 `requirements.txt` 仅供本地开发与静态检查参考。
- **怎么让转述更像麦麦？** 把 `report.mode` 设为 `llm`，`report.use_persona` 保持 `true`（会自动读取宿主的 `[personality] personality`、`[personality] reply_style` 与 `[bot] nickname`），`report.llm_task_name` 填你想复用的宿主模型组（例如 `replyer`），必要时再用 `report.persona_extra` 补充额外设定。
- **权限怎么配？** 默认就是 `permission_mode = "whitelist"`，只要把允许的群号填进 `whitelist_groups` 即可（例如 `["111", "222"]`）；想让所有群都能用就改成 `all`。若还要让管理员在任何群里都能用：把管理员 QQ 号填进 `whitelist_users`（用户白名单优先于群名单）；若只想屏蔽某个群：`permission_mode = "blacklist"` + `blacklist_groups = ["111"]`；若想永久屏蔽某人：`blacklist_users = ["444"]`（最高优先级，三种模式下都生效）。
- **模型问「这个月烧了多少 token」被拒？** 这是默认行为：LLM 工具与 `/token` 共用同一套黑白名单，且可查范围默认只有 `current`（当前对话）。要让模型能查全局 / 指定群 / 指定用户，请在配置里把 `command.tool_allowed_scopes` 加上 `all` / `group` / `user`（留空则完全禁止模型查询）；需要全局数据时更推荐由管理员直接发 `/token all`。

## 7. 开发说明

| 文件 | 职责 |
|---|---|
| `plugin.py` | 插件入口：生命周期、`/token` 指令、LLM 工具、每日定时播报循环、权限判定 |
| `config_model.py` | 全部配置节（`PluginConfigBase`）与别名/模块映射解析 |
| `data_sources.py` | 全局视图取数：调用 `statistics.local.*` 并归并窗口、图表与占比 |
| `session_stats.py` | 会话视图取数：`database.get` 等值过滤明细 + 行数上限 + 本地聚合 |
| `metrics.py` | 指标数据类、派生指标、中文单位格式化、窗口判定 |
| `text_report.py` | 文本模板渲染、占位符构造、LLM 风格化转述 |
| `templates.py` | 4 套 HTML 模板与内联 SVG 条形图 / 扇形图 |
| `renderer.py` | 渲染管线：字体服务轮换、两轮超时、PNG/JPEG 转换、失败回退 |
| `delivery.py` | 播报目标解析与发送 |

代码检查：`uv run ruff check plugins/token_usage_report`（也可用 `python -m py_compile plugins/token_usage_report/*.py` 做语法检查）。

## 8. 更新记录

| 版本 | 主要变更 |
|---|---|
| 1.0.0 | 首版：`query_token_usage` 工具、`/token` 指令（含白/黑名单权限）、每日定时播报（模板 / LLM 转述）、4 套图片模板与 5 条形图 + 5 扇形图、中文单位、模型别名、渲染失败自动回退文字 |
| 1.1.0 | 配置可视化整改：列表字段改用 `x-widget: list`（恢复「+ 添加」按钮）、全部字段补充 `hint`（字段下方直接显示说明）、文本域改用有效的 `rows` 键、移除宿主 SDK 不支持的 `x-option-*` 与 `x-textarea-rows`；新增「提示无权限」开关（`command.notify_no_permission`） |
| 1.2.0 | 图片模板改为**可添加**：`render.template_name` 支持自定义名称，新增 `render.custom_templates` 列表（每条 `名称\|基础样式\|主色[\|页背景色[\|卡片底色]]`，无需改代码即可派生新模板）；非法条目跳过并告警，名称未命中时记 error 日志并回退 `simple` |
| 1.3.0 | `/token` 指令支持**时间参数**（`/token [范围] [时间]`，顺序不限，支持 今日/本周/本月/最近24小时/最近7天/最近30天 及多种中英文写法）；指定时间后报告只保留该窗口并在范围描述里标注；LLM 工具的 `window` 参数同样支持中文写法 |
| 1.3.1 | 修复模型别名冲突统计：多个内部模型名映射到同一显示名时，**合并为一项**统计（次数/Token/费用求和、平均耗时按调用次数加权、占比图不再重复分片），全局排行与会话视图口径一致；合并时记录 info 日志 |
| 1.3.2 | 修复图片渲染全部失败：渲染超时参数改用 SDK 的 `render_timeout_ms`（旧版 SDK 的 `timeout_ms` 会自动兼容重试），不再出现 `got an unexpected keyword argument 'timeout_ms'`；默认模块分组补齐宿主常见模块名（reply / response / smart_segmentation / memories / person_fact_writeback / chat_history_summarizer / dream / story / expression / jargon / behavior / embedding），减少「其他」占比；默认模板去掉数字与「次请求」之间多余的空格 |
| 1.4.0 | 修复单窗口口径串味：指定时间后**隐藏全时段数据**（总计、模型排行、模块占比、聊天消息分布、消息/回复/在线时长、派生指标），图片同样只渲染该窗口卡片，并在末尾自动说明原因；**整行丢弃**引用到被隐藏占位符的模板行，不再出现「本周：（次请求 /）」空壳行；模型排行与聊天消息分布去掉多余空格 |
| 1.4.1 | 修正 1.4.0 的过度隐藏：指定时间后**图表恢复渲染**（条形图 / 扇形图 / 模型排行仍显示），只隐藏确实属于全时段口径的**单值数字**（总计、消息/回复/在线时长、派生指标）与总览/明细表；分布区块统一标注「（近 365 天）」、趋势图标注「按天 · 近 N 天」，文字版与图片版口径一致 |
| 1.5.0 | 单窗口报告改为**采集期收敛**：`/token <时间>` 会把整份报告（窗口卡片、趋势图、模型排行、模块/模型占比、聊天分布、消息/回复/在线时长）都只统计该窗口，不再出现「今日」里混进一年数据；窗口跨度决定取数档位并在取数后按时间戳裁剪，仅与窗口卡片重复的「总计」行整行丢弃；**图片渲染成功且为模板模式时不再重复发送同内容文字**（LLM 风格化转述仍照常发送，渲染失败仍回退文字） |
| 1.6.0 | 趋势图新增**折线样式**：`chart.chart_style` 可选 `bar`（仅条形）/ `line`（仅折线）/ `bar_line`（默认，半透明条形叠加同色折线与数据点），五个趋势图统一生效，无需额外能力调用 |
| 1.6.1 | 趋势图的折线改为**平滑曲线**（Catmull-Rom 转三次贝塞尔）：曲线严格经过每个数据点，柱顶/数据点/曲线三者对齐，控制点夹在绘图区内，数值突变时不会跌到 0 轴以下；两点时退化为直线 |
| 1.6.2 | 修复聊天对象匿名化不生效：此前只匿名化了「聊天消息分布扇形图」，图片里「各聊天流消息数趋势」的图例仍是真实群名/昵称；现在两者共用同一套展示名（分布与趋势图取数范围不同、前 N 名不一致时同样覆盖），并顺带修复多序列图截断成「其他」后丢失数值格式（花费图悬停提示显示成纯数字）的问题 |
| 1.6.3 | 缓存指标不再显示「宿主不提供」：宿主聚合表没有缓存字段，全局视图改为**直接不输出**缓存相关 KPI，文本模板里引用 `{cache_hit_rate}` / `{cache_hit_tokens}` / `{cache_miss_tokens}` 的行会整行隐藏；会话视图（调用明细里有缓存字段）照常显示真实数值 |
| 1.6.4 | 合并图片里重复的两张表：「详细数据」与「模型用量排行」列与数据完全相同，现统一为**模型用量排行表**（含 # 名次），由 `render.show_details` 控制（关掉即只保留图表与总览指标）；顺带修正占比分母——此前只按显示出来的模型求和，模型数超过「显示条数」时占比会虚高，现改为按全部模型合计 |
| 1.7.0 | 图片新增**模型调用量分布**扇形图（各模型被调用次数的占比，`chart.pie_model_requests` 可关）：与模型 Token / 花费占比互补，便于看哪个模型用得最频繁；全局与会话视图都支持 |
| 1.7.1 | 报告新增**聊天链路 Token（占 X%）** 指标并补充口径脚注：总量包含记忆抽取、embedding、图片理解等后台流水线，很多时候它们才是大头，核对「我实际用了多少」请看这一格；「模块 Token 占比」改为用带时间戳的按模块序列统计（一次调用覆盖全部模块，不再受 `limits.top_modules` 截断，单窗口模式下也精确）；脚注新增窗口口径说明（日历零点 vs 滚动时长、宿主聚合 15 分钟刷新） |
| 1.8.0 | **审核整改（隐私与权限边界）**：`command.permission_mode` 默认由 `all` 收紧为 `whitelist`（只有白名单内的群可用指令）、`render.anonymize` 默认由 `false` 收紧为 `true`（群名/昵称默认匿名化）；LLM 工具 `query_token_usage` 补上与 `/token` 一致的黑白名单判定，并新增 `command.tool_allowed_scopes`（默认只有 `current`）限制工具可查范围，工具不再声明 `stream_id` 参数（会话上下文由宿主注入、模型无法自行指定）；会话视图新增结构守卫——取到行却读不到已知字段时**明确报错**（「宿主 ModelUsage 表结构与插件不兼容」），不再用 `row.get(...) or 0` 静默兜成 0；README 补充 `render.allow_network` / `font_services` 的安全说明、`render.html2png` 兼容写法依赖异常文案所对应的最低 SDK 版本，以及「会话视图依赖宿主内部表结构」的契约说明 |
| 1.8.1 | 修复模块归类：宿主聚合表的 `module_name` 只保留首个「.」之前的部分，`maisaka.planner` / `maisaka.replyer`（Maisaka 主聊天链路）被压成 `maisaka` 归入「记忆」、`A_Memorix.ImageEmbedding` 被归入「记忆」而非「图片」，导致「聊天链路 Token」严重低估；现改为按**完整 request_type**（`group_by=type`）归类，会话视图同步；模块花费受宿主能力限制仍为 `module_name` 粒度并在脚注标注；README 与脚注补充「总计 = 供应商上报 total_tokens 之和，可能略大于输入 + 输出」「模块 Token / 模块花费粒度不同」等口径说明 |
| 1.8.2 | 修复**在线时长**：`statistics.local.online_time_trend` 返回的 `SUM(duration_minutes)` 中该字段永不更新（每条恒 5 分钟），且按 `start_timestamp` 分桶会把跨越窗口起点的记录整条裁掉（长跑进程在「今日」窗口显示 **0 秒**）；现改为直读宿主 `online_time` 明细，按 `end - start` 与统计区间求交集累加，与宿主 WebUI 算法一致；`_manifest.json` 移除该能力的声明 |