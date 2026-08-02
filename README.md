# Scarletmoon Helper

绯月论坛每日签到油猴脚本。

## 目标

- 提供绯月论坛每日签到辅助功能
- 自动查找签到入口并执行签到
- 提供手动签到命令和自动签到开关

## 安装

### 方式一：自包含单文件版（推荐）

1. 安装 Tampermonkey
2. 将 `dist/scarletmoon-helper.user.allinone.js` 的内容复制进 Tampermonkey 新建脚本
3. 登录论坛后刷新页面，脚本会自动在后台执行签到

> 单文件版已内置全部逻辑，**无需本地服务器、无需 `@resource`**。

### 方式二：开发版（ES 模块 + 本地调试）

1. 将 `scarletmoon-helper.user.js` 的内容复制进 Tampermonkey 新建脚本
2. 在 `@resource` 中取消注释并填写模块托管地址（Firefox 禁止 `file://`，需 HTTP 服务）：
   ```
   // @resource     sign_module http://localhost:8899/sign-module.js?v=2
   ```
3. 在仓库目录运行本地服务器：
   ```
   python -m http.server 8899
   ```
4. 修改模块后，若未生效，给资源 URL 加版本参数（如 `?v=2`）强制刷新缓存

> 注：安装/更新确认页需在 Tampermonkey 界面手动点击确认。

## 构建发布版

修改 `sign-module.js` 后，运行以下命令重新生成自包含单文件：

```
node build.js
```

输出到 `dist/scarletmoon-helper.user.allinone.js`。

## 签到说明

- 当前脚本会在任意论坛页面加载时自动访问 `/kf_growup.php`
- 通过解析签到页面内的 `kf_growup.php?ok=...&safeid=...&color=...` 链接，直接构造 HTTP 请求执行签到
- 如果已经领取过，会检测到“今天的每日奖励已经领过了，请明天继续。”并停止重复签到

## 脚本说明

- `scarletmoon-helper.user.js`：主脚本文件
- 通过菜单命令执行签到
- 可切换自动签到模式
- 自动识别按钮/链接文本中包含“签到”“每日签到”等关键字的元素

## 当前状态

- 已实现签到入口查找与一键签到功能
- 可拓展为签到结果提示、签到日历展示、签到状态缓存等
