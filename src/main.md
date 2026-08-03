# Scarletmoon 主脚本说明（src/main.js）

本文件描述入口脚本 `src/main.js` 的设计：模块加载机制、构建流程、执行流程与菜单命令。

## 脚本目标

`src/main.js` 是油猴脚本入口，负责：

- 在绯月论坛任意页面加载时启动
- 加载签到模块（`src/sign-module.js`）
- 注册 Tampermonkey 菜单命令
- 自动触发每日签到

## 元数据（UserScript 头部）

| 字段 | 值 | 说明 |
|------|----|------|
| `@name` | 绯月论坛签到助手 | 脚本显示名 |
| `@match` | `https://bbs.kfpromax.com/*` | 仅论坛域名生效 |
| `@grant` | `GM_registerMenuCommand` | 注册菜单命令 |
| `@grant` | `GM_notification` | 通知（预留） |
| `@grant` | `GM_getResourceText` | 开发模式读取 `@resource`（发布版不依赖） |
| `@run-at` | `document-end` | 页面加载完成后执行 |

> 当前版本 `0.6.0`：新增分步骤调试日志（`[步骤1]`~`[步骤4]` + 开始/结束标记），便于 MCP 控制台排查。

## 模块加载机制

主脚本通过 `getSignModule()` 按优先级获取签到模块：

### 1. 发布版（自包含单文件）

构建产物（`dist/allinone.user.js`）已将模块内联：

- 模块源码转成普通脚本（去掉 `export`）
- 末尾挂载 `window.ScarletmoonSignModule = getDefaultSignModule();`
- 主脚本检测到全局对象后直接使用，**无需服务器、无需 `@resource`**

### 2. 开发版（ES 模块动态加载）

开发时主脚本未内联模块，会尝试：

1. 通过 `@resource` 引入 `src/sign-module.js`（需 HTTP 托管，Firefox 禁止 `file://`）
2. `GM_getResourceText('sign_module')` 获取源码
3. 创建 `Blob` 生成临时 `blob:` URL
4. `await import(blobUrl)` 动态加载 ES 模块

> **注意**：Tampermonkey 会缓存 `@resource`，修改模块后需给 URL 加版本参数（如 `?v=2`）强制刷新。

## 构建流程

修改 `src/sign-module.js` 后，运行：

```bash
node build.js
```

输出 `dist/allinone.user.js`（自包含单文件，`.user.js` 后缀可直接触发油猴安装/更新）。

构建步骤（见 `build.js`）：

1. 读取 `src/sign-module.js` 模块源码
2. 去掉 `export` 关键字，转为普通脚本
3. 替换 `src/main.js` 中的 `// @@INLINE_MODULE@@` 标记
4. 输出 `dist/allinone.user.js`

## 执行流程

1. 页面加载（`document-end`），`init()` 检查 `location.host === 'bbs.kfpromax.com'`
2. `getSignModule()` 获取模块（优先全局对象，其次动态加载）
3. `registerMenu()` 注册菜单命令
4. 调用 `signModule.autoSignInOnAnyPage()` 自动签到

## 菜单命令

| 命令 | 行为 |
|------|------|
| 签到助手：打开签到页面 | 跳转 `/kf_growup.php` |
| 签到助手：执行签到 | 手动触发一次签到流程 |

## 调试要点（实测）

- Tampermonkey 安装/更新确认页（`moz-extension://`）是浏览器特权页面，WebDriver 无法自动化，需手动确认。
- 开发模式加载失败常见原因：`@resource` URL 被缓存、本地服务器未启动、`file://` 被 Firefox 拒绝。
- 调试环境：Firefox Nightly + Tampermonkey 5.5.0，MCP 配置见 `.vscode/mcp.json`。
