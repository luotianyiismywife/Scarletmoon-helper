# Scarletmoon 构建说明（build.js）

本文件描述构建脚本 `build.js` 的设计与使用。

## 作用

将源码（`src/main.js` + `src/sign-module.js`）合并为**自包含单文件**，供 Tampermonkey 直接安装，无需本地服务器或 `@resource`。

## 使用

```bash
node build.js
```

依赖：Node.js（零第三方依赖，仅用内置 `fs`/`path`）。

## 产物

- `dist/allinone.user.js`：自包含单文件
  - 已内联模块全部逻辑
  - `.user.js` 后缀：浏览器中直接打开可触发 Tampermonkey 安装/更新

## 输入与输出

| 文件 | 角色 |
|------|------|
| `src/sign-module.js` | 模块源码（ES 模块） |
| `src/main.js` | 入口脚本（含 `// @@INLINE_MODULE@@` 内联标记） |
| `dist/allinone.user.js` | 构建产物 |

## 构建步骤

1. 读取 `src/sign-module.js` 模块源码
2. 转换模块为普通脚本（`toPlainScript`）：
   - `export const X = ...` → `const X = ...`
   - `export default X` → `const __defaultExport = X`
   - 其余裸 `export` 语句移除
   - 末尾追加 `window.ScarletmoonSignModule = getDefaultSignModule();`
3. 读取 `src/main.js`，将转换后的模块替换 `// @@INLINE_MODULE@@` 标记
4. 输出到 `dist/allinone.user.js`

## 内联标记约定

`src/main.js` 中必须保留 `// @@INLINE_MODULE@@` 标记行，构建脚本用它定位模块插入点：

- 构建产物：该行被替换为模块源码
- 开发版（未构建）：该行原样保留，主脚本改走 `@resource` 动态加载

## 失败检查

构建脚本启动时校验：

- `src/sign-module.js` 存在
- `src/main.js` 存在
- `src/main.js` 包含 `// @@INLINE_MODULE@@` 标记

任一不满足则报错退出（`process.exit(1)`）。

## 开发循环

```
修改 src/sign-module.js → node build.js → 安装/更新 dist/allinone.user.js → 验证
```

> 提示：Tampermonkey 已安装的脚本不会自动跟随仓库更新，需要手动重新安装产物。
