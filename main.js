// ==UserScript==
// @name         绯月论坛签到助手
// @namespace    https://github.com/luotianyiismywife/Scarletmoon-helper
// @version      0.5.0
// @description  绯月论坛每日签到辅助，自动触发签到；支持自包含单文件版与 ES 模块开发版
// @author       luotianyiismywife
// @match        https://bbs.kfpromax.com/*
// @grant        GM_registerMenuCommand
// @grant        GM_notification
// @grant        GM_getResourceText
// @run-at       document-end
// ==/UserScript==

// @@INLINE_MODULE@@

(async function () {
    'use strict';

    const FORUM_HOST = 'bbs.kfpromax.com';

    // 优先使用构建合并版注入的全局模块；开发版则通过 @resource 动态加载
    const getSignModule = async () => {
        if (window.ScarletmoonSignModule) {
            return window.ScarletmoonSignModule;
        }

        if (typeof GM_getResourceText === 'function') {
            const source = GM_getResourceText('sign_module');
            if (source) {
                const blobUrl = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
                try {
                    return await import(blobUrl);
                } finally {
                    URL.revokeObjectURL(blobUrl);
                }
            }
        }
        return null;
    };

    const init = async () => {
        if (location.host !== FORUM_HOST) {
            return;
        }

        const signModule = await getSignModule();
        if (!signModule) {
            console.error('[绯月签到助手] 无法加载签到模块。');
            return;
        }

        registerMenu(signModule);
        await signModule.autoSignInOnAnyPage();
    };

    const registerMenu = (signModule) => {
        GM_registerMenuCommand('签到助手：打开签到页面', signModule.goToSignPage);
        GM_registerMenuCommand('签到助手：执行签到', signModule.executeSignIn);
    };

    await init();
})();
