# Scarletmoon 签到模块说明

本文件描述 `src/sign-module.js` 在绯月论坛签到功能中的设计、执行流程、API 以及扩展方式。

## 模块目标

`src/sign-module.js` 的目标是提供一个独立可复用的签到功能模块，支持：

- 在任意论坛页面执行签到逻辑
- 自动读取签到页面内容
- 从签到页面中解析签到动作链接
- 直接发起签到请求
- 检查并避免重复签到
- 通过简单 API 暴露给主脚本调用

## 实测发现（重要）

以下信息基于 Firefox Nightly + Tampermonkey 5.5.0 在真实论坛会话中的调试结果：

- **论坛页面是 GBK 编码**（`charset=gbk`），直接 `response.text()` 会按 UTF-8 解码导致中文乱码，所有中文正则匹配失效。必须用 `TextDecoder('gbk')` 或按响应头 charset 解码。
- **`ok=2&color=N` 链接是“选择该颜色”（ID 颜色设置），不是签到**，必须排除。误发送会修改用户的 ID 颜色。
- 真实的每日奖励签到动作是 **`kf_growup.php?ok=3&safeid=...`**（2026-08-03 未签到状态下实测验证：脚本自动发送 `ok=3` 请求后签到成功）。注意：油猴旧版脚本并无硬编码 `ok=3`，实际是靠正则兜底 `ok=(?!2)\d+` 匹配到页面中的 `ok=3` 链接完成签到的。

## 适用场景

- Tampermonkey / Violentmonkey / Greasemonkey 脚本内部
- 对接绯月论坛 `bbs.kfpromax.com`
- 通过论坛已登录会话自动完成每日签到

## 核心常量

- `FORUM_HOST`：`bbs.kfpromax.com`
- `SIGN_PAGE`：`/kf_growup.php`
- `DENY_TEXT`：链接文本黑名单，含这些词的链接不视为签到入口
- `TARGET_TEXT`：链接文本白名单，含这些词的链接优先视为签到入口
- `SIGN_STATUS_REGEX`：已签到状态正则（匹配“已经领过了/请明天继续/已领过”等）

## 调试日志

模块在各步骤输出 `[绯月签到助手]` 前缀的详细日志（v0.6.0 起）：

| 标记 | 内容 |
|------|------|
| `===== 自动签到开始/结束` | 执行边界 + 耗时 |
| `[步骤1]` | 签到页获取：URL、HTTP 状态、charset、长度 |
| `[步骤2]` | 已领取判定结果 |
| `[步骤3]` | 候选链接列表、各排除原因、最终选择（DOM 或正则兜底） |
| `[步骤4]` | 签到请求 URL、响应状态、已领取判定、响应关键文本 |

## 功能说明

### 1. decodeResponse（内部）

`decodeResponse(response)`

- 从响应头解析 `charset`（绯月为 `gbk`）
- 使用 `TextDecoder` 按正确编码解码响应体
- 未知编码时依次回退：`gbk` → `utf-8`

用途：解决 GBK 页面中文乱码导致的所有判定/解析失效问题。

### 2. parseSignStatus

`parseSignStatus(htmlText)`

- 返回状态对象：
  - `alreadySigned`：是否已签到（同 `isAlreadySigned`）
  - `hasTryAgainText`：是否包含“请明天继续”提示

用途：结构化读取签到状态，便于后续扩展状态展示。

> 注：该 API 已导出，但**未包含**在 `getDefaultSignModule()` 返回对象中。

### 3. fetchSignPage

`fetchSignPage()`

- 访问签到页面 `location.origin + SIGN_PAGE`
- 使用 `fetch` 发起 GET 请求，并携带 `credentials: 'include'`
- 通过 `decodeResponse` 正确解码 GBK 页面
- 返回签到页面 HTML 文本，若请求失败则返回 `null`

用途：在当前页面中自动获取签到页面内容，而无需用户手动打开。

### 4. isAlreadySigned

`isAlreadySigned(htmlText)`

- 对签到页面 HTML 或签到结果 HTML 执行正则匹配（`SIGN_STATUS_REGEX`）
- 检测文本：
  - `今天的每日奖励已经领过了，请明天继续。`
  - `今日奖励已领取`
  - `已领过`
  - `请明天继续`
- 如果匹配成功，则说明当天已完成签到

用途：避免重复请求，减少无效签到。

### 5. findSignActionUrl

`findSignActionUrl(htmlText)`

- 使用 `DOMParser` 将 HTML 转换为 DOM
- 查找所有含 `href` 或 `onclick` 的元素
- 过滤规则：
  - 排除 `javascript:` 链接
  - **排除 `ok=2&color=N` 的 ID 颜色设置链接**（否则会把“选择该颜色”误判为签到并发送请求）
  - 排除包含“已领过”等拒绝提示的链接文本
  - 优先匹配链接文本含 `签到`/`领取`/`每日奖励` 且 href 为 `kf_growup.php?ok=数字` 的链接（实测命中 `ok=3`）
  - 其次匹配 `kf_growup.php?ok=3`（每日奖励领取动作，已验证）；`ok=1` 保留兼容
- 如果 DOM 查找失败，则退而使用正则匹配（同样排除 `ok=2`）

返回值：有效签到动作链接字符串，如 `kf_growup.php?ok=3&safeid=582603c`

用途：从签到页面中提取真正的签到请求 URL，避免误发颜色设置请求。

### 6. sendSignRequest

`sendSignRequest(actionUrl)`

- 通过 `normalizeActionUrl` 将相对路径或绝对路径标准化为完整 URL
- 发起 `fetch` GET 请求，带 `credentials: 'include'`
- 通过 `decodeResponse` 读取返回 HTML 文本
- 返回对象：
  - `ok`：HTTP 响应是否成功
  - `status`：HTTP 状态码
  - `actionUrl`：实际请求的完整 URL
  - `alreadySigned`：是否已签到
  - `text`：完整响应 HTML

用途：执行签到动作并获取结果。

### 7. autoSignInOnAnyPage

`autoSignInOnAnyPage()`

执行完整签到流程：

1. 调用 `fetchSignPage()` 获取签到页面内容
2. 若无法获取，则终止并记录失败信息
3. 如果 `isAlreadySigned(signPageText)` 为真，则终止并记录“已签到”状态
4. 调用 `findSignActionUrl(signPageText)` 获取签到链接
5. 若未找到链接，则终止并记录失败信息
6. 调用 `sendSignRequest(actionUrl)` 发起签到请求
7. 根据返回结果判断签到是否成功或是否已签到

返回对象：

- `success`：流程是否成功完成
- `message`：状态说明文本
- `alreadySigned`：是否已签到
- `actionUrl`：实际发送的签到 URL（未发送时为 `null`）
- `responseText`：签到响应 HTML（请求发送后才有）

同时向控制台输出 `[绯月签到助手]` 前缀的状态日志。

用途：主脚本调用此方法即可在任意论坛页面尝试自动签到。

### 8. goToSignPage

`goToSignPage()`

- 直接跳转到签到页面：`location.origin + SIGN_PAGE`

用途：作为菜单命令或手动入口，让用户直接打开签到页面查看结果。

### 9. executeSignIn

`executeSignIn()`

- 仅做一次简单封装，当前实现直接执行 `autoSignInOnAnyPage()`。

用途：提供一个更语义化的外部 API，便于主脚本或其它模块调用。

### 10. getDefaultSignModule

`getDefaultSignModule()`

- 返回包含模块所有方法与常量的对象：
  - `FORUM_HOST`、`SIGN_PAGE`
  - `fetchSignPage`、`isAlreadySigned`、`findSignActionUrl`、`sendSignRequest`
  - `autoSignInOnAnyPage`、`goToSignPage`、`executeSignIn`
- 构建产物（`dist/allinone.user.js`）通过它挂载 `window.ScarletmoonSignModule`

用途：将模块作为普通对象暴露给非 ES 模块环境统一调用。

> 注：`parseSignStatus` 不在返回对象中。

## 内部辅助函数

以下函数为模块内部使用，未导出：

- `normalizeActionUrl(actionUrl)`：将相对/绝对 URL 标准化为完整 URL，无效时返回 `null`
- `extractActionUrlFromOnclick(htmlText)`：从 `onclick="location.href='...'"` 中提取 URL
- `isColorSettingLink(href)`：判断是否为 `ok=2&color=N` 的 ID 颜色设置链接

## 兼容性与注意事项

- 依赖浏览器环境支持 `fetch`、`DOMParser`、`TextDecoder`
- 依赖 `credentials: 'include'` 以使用当前论坛登录会话
- 若论坛签到页面结构变化，`findSignActionUrl` 可能需要更新解析规则

## 扩展建议

可在此模块基础上扩展：

- 增加签到次数、奖励内容解析
- 提取签到结果中的具体奖励文本
- 提供 `signStatus()` 返回更丰富状态对象
- 支持定时自动签到和签到历史缓存
- 提供纯对象配置：`createSignModule({ host, signPage, denyText, targetText })`

## 参考内容

- 签到页面路径：`/kf_growup.php`
- 签到动作链接：`kf_growup.php?ok=3&safeid=...`（2026-08-03 已验证）
- ID 颜色设置链接（**非签到**，需排除）：`kf_growup.php?ok=2&safeid=...&color=N`
- 已签到判定文本：`今天的每日奖励已经领过了，请明天继续。` 等
