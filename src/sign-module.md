# Scarletmoon 签到模块说明

本文件描述 `sign-module.js` 在绯月论坛签到功能中的设计、执行流程、API 以及扩展方式。

## 模块目标

`sign-module.js` 的目标是提供一个独立可复用的签到功能模块，支持：

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
- 真实的每日奖励签到动作预计为 `kf_growup.php?ok=1&safeid=...`（待未签到状态时验证）。

## 适用场景

- Tampermonkey / Violentmonkey / Greasemonkey 脚本内部
- 对接绯月论坛 `bbs.kfpromax.com`
- 通过论坛已登录会话自动完成每日签到

## 核心常量

- `FORUM_HOST`：`bbs.kfpromax.com`
- `SIGN_PAGE`：`/kf_growup.php`
- `DENY_TEXT`：用于识别“已签到”或“无需再次签到”的提示文本
- `TARGET_TEXT`：用于识别签到按钮或签到链接上的关键字

## 功能说明

### 1. decodeResponse（内部）

`decodeResponse(response)`

- 从响应头解析 `charset`（绯月为 `gbk`）
- 使用 `TextDecoder` 按正确编码解码响应体
- 未知编码时依次回退：`gbk` → `utf-8`

用途：解决 GBK 页面中文乱码导致的所有判定/解析失效问题。

### 3. fetchSignPage

`fetchSignPage()`

- 访问签到页面 `location.origin + SIGN_PAGE`
- 使用 `fetch` 发起 GET 请求，并携带 `credentials: 'include'`
- 通过 `decodeResponse` 正确解码 GBK 页面
- 返回签到页面 HTML 文本，若请求失败则返回 `null`

用途：在当前页面中自动获取签到页面内容，而无需用户手动打开。

### 4. isAlreadySigned

`isAlreadySigned(htmlText)`

- 对签到页面 HTML 或签到结果 HTML 执行正则匹配
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
  - 优先匹配链接文本含 `签到`/`领取`/`每日奖励` 且 href 为 `kf_growup.php?ok=数字` 的链接
  - 其次匹配 `kf_growup.php?ok=1`（每日奖励领取动作）
- 如果 DOM 查找失败，则退而使用正则匹配（同样排除 `ok=2`）

返回值：有效签到动作链接字符串，如 `kf_growup.php?ok=1&safeid=56a7ccd`

用途：从签到页面中提取真正的签到请求 URL，避免误发颜色设置请求。

### 6. sendSignRequest

`sendSignRequest(actionUrl)`

- 将相对路径或绝对路径标准化为完整 URL
- 发起 `fetch` GET 请求，带 `credentials: 'include'`
- 读取返回 HTML 文本
- 返回对象：
  - `ok`：HTTP 响应是否成功
  - `status`：HTTP 状态码
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

- 返回包含模块所有方法与常量的对象
- 便于在不支持模块默认导出的环境中统一调用

用途：如果需要将模块作为普通对象加载，这个方法可以快速获取完整 API 集合。

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
- 签到链接模式：`kf_growup.php?ok=...&safeid=...&color=...`
- 已签到判定文本：`今天的每日奖励已经领过了，请明天继续。` 等
