# Scarletmoon Helper

绯月论坛每日签到油猴脚本。

## 目标

- 提供绯月论坛每日签到辅助功能
- 自动查找签到入口并执行签到
- 提供手动签到命令和自动签到开关

## 安装

### 方式一：自包含单文件版（推荐）

1. 安装 Tampermonkey
2. 将 `dist/allinone.user.js` 的内容复制进 Tampermonkey 新建脚本（或直接在浏览器中打开该文件触发安装）
3. 登录论坛后刷新页面，脚本会自动在后台执行签到

> 单文件版已内置全部逻辑，**无需本地服务器、无需 `@resource`**。

### 方式二：开发版（ES 模块 + 本地调试）

1. 将 `src/main.js` 的内容复制进 Tampermonkey 新建脚本
2. 在 `@resource` 中取消注释并填写模块托管地址（Firefox 禁止 `file://`，需 HTTP 服务）：
   ```
   // @resource     sign_module http://localhost:8899/sign-module.js?v=2
   ```
3. 在仓库目录运行本地服务器：
   ```
   python -m http.server 8899
   ```
   （需将模块托管为仓库根路径：如 `src/sign-module.js` 映射到 `/sign-module.js`）
4. 修改模块后，若未生效，给资源 URL 加版本参数（如 `?v=2`）强制刷新缓存

> 注：安装/更新确认页需在 Tampermonkey 界面手动点击确认。

## 构建发布版

修改 `src/sign-module.js` 后，运行以下命令重新生成自包含单文件：

```
node build.js
```

输出 `dist/allinone.user.js`（`.user.js` 后缀可在浏览器中直接触发 Tampermonkey 安装/更新）。

## 签到说明

- 当前脚本会在任意论坛页面加载时自动访问 `/kf_growup.php`
- 正确解码 GBK 页面后，检测"今天的每日奖励已经领过了，请明天继续。"等提示，已领取则直接停止
- 未领取时，解析签到动作链接（匹配 `kf_growup.php?ok=1` 或含"领取"等文本的链接），直接构造 HTTP 请求执行签到
- **不会误触 `ok=2&color=N` 的"选择该颜色"链接**（ID 颜色设置，非签到）

## 脚本说明

- `src/main.js`：主脚本文件（入口）
- 通过菜单命令执行签到
- 可切换自动签到模式
- 自动识别按钮/链接文本中包含“签到”“每日签到”等关键字的元素

## 文档索引

- `src/main.md`：主脚本（入口）说明——加载机制、菜单命令、执行流程
- `src/sign-module.md`：签到模块说明——API、解析规则、实测发现
- `build.md`：构建脚本说明——合并流程、产物、开发循环

## 当前状态

- 已实现签到入口查找与一键签到功能
- 可拓展为签到结果提示、签到日历展示、签到状态缓存等
