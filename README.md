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
- **图片报告**：4 套内置模板（简约白卡片 / 深色数据面板 / 手账便签 / 榜单风），含 **5 个条形图 + 5 个扇形图**、模型用量排行与详细数据表；每个图表可单独开关。
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

| 指令 | 说明 |
|---|---|
| `/token` | 统计**当前对话**（哪里发的返回哪里） |
| `/token all` | 统计**全部会话** |
| `/token 群 123456` | 统计指定 QQ 群（按群号解析会话） |
| `/token 用户 123456` | 统计指定 QQ 号（按 QQ 号解析私聊会话） |

- 指令回复 = 图片（若开启）+ 文本报告；图片渲染失败会自动改为只发文本。
- 未找到目标会话时会明确提示（例如「未找到群 123456 的会话记录（该群可能尚未与 Bot 交互）」），不会给出虚假数据。

### 3.2 LLM 工具 `query_token_usage`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scope` | string | 否 | `all`（默认）/ `current` / `group` / `user` |
| `target_id` | string | 否 | `scope=group` 时的群号；`scope=user` 时的 QQ 号 |
| `stream_id` | string | 否 | `scope=current` 时必填，当前聊天流 ID |
| `window` | string | 否 | `today` / `this_week` / `this_month` / `last_24h` / `last_7d` / `last_30d`；留空返回全部窗口 |

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
| 开关 | `enabled`、`use_persona`、`use_image`、`send_image`、`anonymize`、`allow_network`、所有图表开关 | 直接切换开/关 |
| 输入框 | `unit_name`、`platform`、`llm_task_name`、`deny_message`、超时/视口等数值 | 数值项已带步进（如超时 1000 毫秒一步） |
| 下拉选择 | `report.mode`、`command.permission_mode`、`render.template_name`、`render.image_format`、`chart.bar_granularity` | 选项自带中文名与逐项说明，鼠标悬停或展开即可看到该选项的作用 |
| 标签输入（回车添加） | 所有群号/QQ 号列表、模型别名、字体服务组、模块分组前缀 | 输入后按回车生成一个标签；标签内容就是配置里的字符串元素 |
| 多行文本 | `template`、`persona_template`、`persona_extra`、`llm_prompt` | 已设置合适高度，支持换行与占位符 |
| 滑块 | `jpeg_quality`、`resolution_scale`、`series_top`、`top_models`、`top_modules` | 拖动即可，取值范围由 Schema 限定 |
| 分组卡片 | 每个 `[...]` 配置节 | 卡片标题即该节中文名（插件 / Token 单位 / 模型别名 / 定时播报 / 统计指令 / 图片渲染 / 图表 / 模块分组 / 读取上限） |

每个字段在表单里都有三样东西，用来回答「填什么、干什么、能填什么」：

1. **中文短名**（`label`）：例如「模型组（任务配置名）」；
2. **用途说明**（`description`）：字段下方的说明文字，写清了该字段影响什么、默认值含义、以及有什么注意事项；
3. **输入示例**（`placeholder`）：例如 `内地|https://fonts.loli.net|https://gstatic.loli.net`、`123456789`、`内部名=显示名`。

下拉类字段额外提供**选项中文名**与**逐项说明**，所以不需要去翻文档也能选对。

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
| `[command]` | `enabled = true`、`permission_mode = "all"`、`use_image = true` | **装完在任意群发 `/token` 即可看到报告** |
| `[render]` | `template_name = "simple"`、`image_format = "png"`、内置内地+海外两组字体服务 | 联网即可渲染中文；渲染不可用时自动回退文字 |
| `[chart]` | 10 个图表开关全为 `true` | 图片报告默认包含 5 条形图 + 5 扇形图 |
| `[module_groups]` | 已按宿主内置模块名预置 | 无需配置即可得到「计划器/回复器/图片/记忆/表情/插件/其他」分组 |
| `[limits]` | `max_session_rows = 20000`、`top_models = 20`、`top_modules = 8` | 适配常见数据量；会话明细超上限会明确报错而不是给错数字 |

> 安全提示：`permission_mode` 默认为 `all`（方便装完立刻验证），此时任意群/任意用户都能用 `/token`，其中 `/token all` 会展示全局花费与 Token 数据。若这些数据敏感，请在正式环境改成 `whitelist` 并填写允许的群号/QQ 号，或把 `enabled` 设为 `false` 先关闭指令。

### 4.1 `[plugin]`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 是否启用插件 |
| `config_version` | str | `"1.0.0"` | 配置版本，升级配置结构时递增 |

### 4.2 `[token_unit]`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `unit_name` | str | `"Token"` | Token 的显示单位名称，例如 鸡蛋 / 词元 / 白饭 |

### 4.3 `[model_aliases]`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `aliases` | list[str] | `[]` | 每条格式为 `内部名=显示名`，例如 `deepseek-v4-flash=小深`；排行、图表与文本中均显示别名 |

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
| `permission_mode` | str | `"all"` | 群名单制度：`all` 不启用群白名单（群黑名单仍生效）；`whitelist` 启用群白名单；`blacklist` 启用群黑名单 |
| `whitelist_groups` | list[str] | `[]` | 群白名单：`whitelist` 模式下仅这些群可用；与群黑名单冲突时黑名单优先 |
| `whitelist_users` | list[str] | `[]` | 用户白名单：命中的用户**始终可用**（优先于群名单；与用户黑名单冲突时黑名单优先） |
| `blacklist_groups` | list[str] | `[]` | 群黑名单：命中的群在**任何模式下**都不可用（优先于群白名单） |
| `blacklist_users` | list[str] | `[]` | 用户黑名单：命中的用户**始终不可用**（最高优先级，优先于用户白名单） |
| `deny_message` | str | `"你没有权限使用该指令"` | 无权限时的提示语，留空则不回复 |
| `use_image` | bool | `true` | 指令回复是否使用图片渲染 |

**权限判定规则（严格按顺序）**：

1. 本地调试终端（local operator）始终放行；
2. 用户在 `blacklist_users` 中 → **始终拒绝**（用户层黑名单优先于用户白名单）；
3. 用户在 `whitelist_users` 中 → **始终放行**（优先于群名单，包括群黑名单）；
4. 群在 `blacklist_groups` 中 → **拒绝**（群层黑名单优先于群白名单，且不区分名单制度）；
5. `permission_mode = "all"` → 放行（不启用群白名单）；
6. `permission_mode = "whitelist"` → 群聊要求群号在 `whitelist_groups` 中；私聊没有群可判定，只能靠用户白名单，未列入即拒绝；
7. `permission_mode = "blacklist"` → 群聊未命中群黑名单即放行（命中已在第 4 步处理）；私聊放行。

> 冲突处理：**同一个用户同时出现在用户黑白名单 → 黑名单胜出**；**同一个群同时出现在群黑白名单 → 黑名单胜出**。用户名单优先于群名单，用户白名单可以放行一个处于群黑名单中的群（见下方示例）。

对应示例：

- 用户在 `whitelist_users`，`permission_mode = "whitelist"` 且群不在 `whitelist_groups` → **通过**；
- 用户在 `whitelist_users`，`permission_mode = "blacklist"` 且群在 `blacklist_groups` → **通过**（用户白名单优先于群名单）；
- 用户在 `blacklist_users` → 无论模式与群名单如何 → **不通过**；
- 用户同时在黑白名单中 → **不通过**；
- 群同时在群白名单与群黑名单中 → **不通过**（任何模式下）。

### 4.6 `[render]` 图片渲染

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `template_name` | str | `"simple"` | `simple` 简约白卡片 / `dark` 深色数据面板 / `handbook` 手账便签 / `rank` 榜单风 |
| `image_format` | str | `"png"` | `png` 或 `jpeg` |
| `jpeg_quality` | int | `90` | JPEG 质量（1~100，仅 `jpeg` 生效） |
| `resolution_scale` | float | `2.0` | 分辨率等级（设备像素比，1.0~4.0） |
| `first_round_timeout_ms` | int | `15000` | 第一轮渲染超时（毫秒） |
| `second_round_timeout_ms` | int | `25000` | 第二轮（回退轮）渲染超时（毫秒） |
| `viewport_width` / `viewport_height` | int | `1000` / `600` | 第一轮渲染视口 |
| `fallback_viewport_width` / `fallback_viewport_height` | int | `900` / `1200` | 第二轮（回退轮）渲染视口兜底宽高 |
| `allow_network` | bool | `true` | 是否允许渲染页面访问外部网络（加载字体 CDN 需要） |
| `font_services` | list[str] | 内地 + 海外两组 | 字体服务组，见 4.6.1 |
| `show_details` | bool | `true` | 是否输出详细数据表 |
| `anonymize` | bool | `false` | 聊天消息分布扇形图是否匿名化（`群聊A` / `个人用户A`） |

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

### 4.7 `[chart]` 图表

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `bar_granularity` | str | `"day"` | 条形图颗粒度：`hour` / `day` / `week` / `month` |
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
| `pie_chat_messages` | bool | `true` | 扇形图：聊天消息分布 |

关闭某个图表后，该图表不再出现在图片中，也不会触发对应的宿主能力调用。

### 4.8 `[module_groups]` 模块分组

宿主统计中的模块名是 `request_type` 中第一个 `.` 之前的部分（例如 `plugin.xxx` → `plugin`、`A_Memorix.EpisodeSegmentation` → `A_Memorix`）。这里配置各中文分组对应的**模块名前缀**，未命中的模块会归入「其他」并以 `info` 级别日志记录一次，便于你补充映射。

| 字段 | 默认值 |
|---|---|
| `planner`（计划器） | `["planner", "maisaka.plan", "plan"]` |
| `replyer`（回复器） | `["replyer", "maisaka.replyer", "response.splitter"]` |
| `image`（图片） | `["image", "vlm", "A_Memorix.ImageEmbedding"]` |
| `memory`（记忆） | `["memory", "A_Memorix", "maisaka", "heuristic_memory_impression", "memory_feedback_correction"]` |
| `emoji`（表情） | `["emoji"]` |
| `plugin`（插件） | `["plugin", "mcp_sampling"]` |

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
| `{cache_hit_rate}` `{cache_hit_tokens}` `{cache_miss_tokens}` | 缓存指标（全局视图显示「宿主不提供」，仅会话视图有值） |
| `{model_ranking}` `{module_breakdown}` `{chat_message_share}` `{details}` | 多行文本区块（模型排行 / 模块占比 / 聊天消息分布 / 详细数据） |
| `{unavailable}` `{notes}` | 本次不可用的数据区块 / 统计口径脚注 |

说明：模板使用 Python `str.format_map` 语法，未知占位符会置空并记录一次警告；模板语法错误会回退为内置默认模板。

## 5. 统计口径与已知限制

- **数据来源**：全局指标取宿主 `statistics.local.*` 聚合表（小时级聚合，**最新数据最长滞后约 15 分钟**）；会话维度取 `ModelUsage` 调用明细后在插件内聚合。
- **总计口径**：全局「总计」= **最近 365 天**（宿主统计能力上限，非无限回溯）；会话「总计」= 该会话全部历史记录（受 `max_session_rows` 行数上限约束）。
- **消息数与回复数**：只覆盖消息量前 50 会话与前 50 工具（宿主 `top_chats` / `top_tools` 上限）；回复数沿用宿主内置口径（`reply` 工具调用次数）。
- **模块花费**：宿主没有「按模块聚合花费」的能力，插件对 Token 排名前 `limits.top_modules` 个模块逐个查询后汇总，其余模块归入「其他」。
- **缓存指标**：宿主聚合表不含缓存字段，因此缓存命中率 / 命中 Token / 未命中 Token **仅会话维度可用**，全局视图显示「宿主不提供」。
- **会话维度**：不包含消息数 / 回复数 / 在线时长（宿主无按会话过滤这些指标的能力）；会话明细读取超上限时**直接报错**，不会给出被截断的错误数字。
- **以下功能因宿主能力限制未实现**：
  1. 按群聊 / 用户的 **Token** 占比扇形图（用「聊天消息分布」替代展示）；
  2. 聊天流花费分布（会话维度的花费）；
  3. 请求类型花费分布 / 调用来源花费分布（只能按模型与模块两个维度聚合花费）；
  4. per-model 输入 / 输出 Token 拆分（仅全局总量提供输入 / 输出拆分）；
  5. 消息数 / 回复数的全量精确值（受前 50 上限）。

## 6. 常见问题

- **图片没发出来，只收到文字？** 说明渲染整体失败（字体服务不可用、Playwright 浏览器不可用、超出 45 秒预算等），插件会记录一条 `error` 日志并自动回退文字版本；可检查日志中的「渲染失败（第 N 轮 / 字体服务 …）」定位原因。
- **字体显示成默认字体？** 检查 `render.allow_network` 是否为 `true`，以及 `font_services` 中的地址在当前网络环境是否可访问。
- **数字与 WebUI 统计页面对不上？** 先确认时间窗口口径（WebUI 页面与插件窗口定义可能不同），再确认宿主聚合的最新延迟（约 15 分钟），最后确认上文中「前 50」等上限是否生效。
- **`/token 群 xxx` 提示找不到会话？** 说明该群尚未与 Bot 产生过聊天流记录；可以让群里先发一条消息，或在配置中改用私聊目标。
- **Pillow 需要自己安装吗？** 不需要，宿主已依赖 `pillow>=12.3.0`；插件的 `requirements.txt` 仅供本地开发与静态检查参考。
- **怎么让转述更像麦麦？** 把 `report.mode` 设为 `llm`，`report.use_persona` 保持 `true`（会自动读取宿主的 `[personality] personality`、`[personality] reply_style` 与 `[bot] nickname`），`report.llm_task_name` 填你想复用的宿主模型组（例如 `replyer`），必要时再用 `report.persona_extra` 补充额外设定。
- **权限怎么配？** 例如只允许两个群使用：`permission_mode = "whitelist"` + `whitelist_groups = ["111", "222"]`；若还要让管理员在任何群里都能用：再把管理员 QQ 号填进 `whitelist_users`（用户白名单优先于群名单）；若只想屏蔽某个群：`permission_mode = "blacklist"` + `blacklist_groups = ["111"]`；若想永久屏蔽某人：`blacklist_users = ["444"]`（最高优先级，三种模式下都生效）。

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