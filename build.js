/**
 * Scarletmoon Helper 构建脚本
 * 将 sign-module.js 合并进 scarletmoon-helper.user.js，
 * 生成自包含的单文件版本（无需本地服务器 / @resource）。
 *
 * 用法：node build.js
 * 输出：dist/scarletmoon-helper.user.js
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const MODULE_FILE = path.join(ROOT, 'sign-module.js');
const MAIN_FILE = path.join(ROOT, 'scarletmoon-helper.user.js');
const OUT_DIR = path.join(ROOT, 'dist');
const OUT_FILE = path.join(OUT_DIR, 'scarletmoon-helper.user.allinone.js');
const INLINE_MARKER = '// @@INLINE_MODULE@@';

/** 把 ES 模块源码转换为普通脚本源码（去掉 export，挂到 window） */
const toPlainScript = (moduleSrc) => {
    const plain = moduleSrc
        // export const X = ... -> const X = ...
        .replace(/^export\s+const\s+/gm, 'const ')
        // export default X -> const __defaultExport = X（保留 getDefaultSignModule 供挂载）
        .replace(/^export\s+default\s+/gm, 'const __defaultExport = ')
        // 其余裸 export（如 export { a, b }）
        .replace(/^export\s+/gm, '');

    // 挂载为全局对象，供主脚本直接使用
    return `${plain}\n\nwindow.ScarletmoonSignModule = getDefaultSignModule();\n`;
};

const build = () => {
    if (!fs.existsSync(MODULE_FILE)) {
        console.error(`找不到模块文件：${MODULE_FILE}`);
        process.exit(1);
    }
    if (!fs.existsSync(MAIN_FILE)) {
        console.error(`找不到主脚本文件：${MAIN_FILE}`);
        process.exit(1);
    }

    const moduleSrc = fs.readFileSync(MODULE_FILE, 'utf8');
    const mainSrc = fs.readFileSync(MAIN_FILE, 'utf8');

    if (!mainSrc.includes(INLINE_MARKER)) {
        console.error(`主脚本缺少内联标记 ${INLINE_MARKER}`);
        process.exit(1);
    }

    const inlineModule = toPlainScript(moduleSrc);
    const bundled = mainSrc.replace(INLINE_MARKER, inlineModule);

    fs.mkdirSync(OUT_DIR, { recursive: true });
    fs.writeFileSync(OUT_FILE, bundled, 'utf8');

    console.log(`构建完成：${OUT_FILE}`);
    console.log(`大小：${(Buffer.byteLength(bundled, 'utf8') / 1024).toFixed(1)} KB`);
};

build();
