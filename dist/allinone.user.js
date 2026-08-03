// ==UserScript==
// @name         绯月论坛签到助手
// @namespace    https://github.com/luotianyiismywife/Scarletmoon-helper
// @version      0.6.0
// @description  绯月论坛每日签到辅助，自动触发签到；支持自包含单文件版与 ES 模块开发版
// @author       luotianyiismywife
// @match        https://bbs.kfpromax.com/*
// @grant        GM_registerMenuCommand
// @grant        GM_notification
// @grant        GM_getResourceText
// @run-at       document-end
// ==/UserScript==

const FORUM_HOST = 'bbs.kfpromax.com';
const SIGN_PAGE = '/kf_growup.php';
const DENY_TEXT = ['已经领过', '请明天继续', '已领过', '今日已领', '今天已领取', '已领取'];
const TARGET_TEXT = ['领取', '签到', '每日奖励', '领取奖励', '今日奖励'];
const SIGN_STATUS_REGEX = /今天的每日奖励已经领过了，请明天继续。|今日奖励已领取|已领过|请明天继续/;

/** 统一调试日志：带时间戳与前缀，便于 MCP 控制台检索 */
const log = (...args) => console.log(`[绯月签到助手]`, ...args);
const logError = (...args) => console.error(`[绯月签到助手]`, ...args);

/**
 * 按响应头声明的字符集解码响应体。
 * 绯月论坛页面为 GBK 编码，直接 response.text() 会按 UTF-8 解码导致中文乱码。
 */
const decodeResponse = async (response) => {
    const buf = await response.arrayBuffer();
    const charset = (response.headers.get('content-type') || '').match(/charset=([\w-]+)/i)?.[1] || 'utf-8';
    try {
        return new TextDecoder(charset).decode(buf);
    } catch {
        // 未知字符集时回退到 GBK，再不行就用 UTF-8
        try {
            return new TextDecoder('gbk').decode(buf);
        } catch {
            return new TextDecoder('utf-8').decode(buf);
        }
    }
};

const isColorSettingLink = (href) => /kf_growup\.php\?ok=2&safeid=\d+&color=\d+/i.test(href);

const normalizeActionUrl = (actionUrl) => {
    if (!actionUrl) {
        return null;
    }

    if (actionUrl.startsWith('http') || actionUrl.startsWith('https')) {
        return actionUrl;
    }

    return `${location.origin}/${actionUrl.replace(/^\//, '')}`;
};

const extractActionUrlFromOnclick = (htmlText) => {
    const onclickHref = /onclick=["'][^"']*location\.href\s*=\s*["']([^"']+)["']/i.exec(htmlText);
    if (onclickHref?.[1]) {
        return onclickHref[1];
    }

    const inlineUrl = /href=["'](kf_growup\.php\?[^"']+)["']/i.exec(htmlText);
    return inlineUrl?.[1] || null;
};

const fetchSignPage = async () => {
    const url = `${location.origin}${SIGN_PAGE}`;
    log(`[步骤1] 获取签到页面: ${url}`);
    const response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        headers: {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
    });
    log(`[步骤1] 响应状态: ${response.status}, Content-Type: ${response.headers.get('content-type')}`);
    if (!response.ok) {
        logError(`[步骤1] 获取失败，HTTP ${response.status}`);
        return null;
    }
    const text = await decodeResponse(response);
    log(`[步骤1] 页面获取成功，长度 ${text.length} 字符，已解码 GBK 中文`);
    return text;
};

const isAlreadySigned = (htmlText) => {
    return SIGN_STATUS_REGEX.test(htmlText);
};

const parseSignStatus = (htmlText) => {
    return {
        alreadySigned: isAlreadySigned(htmlText),
        hasTryAgainText: /请明天继续/.test(htmlText),
    };
};

const findSignActionUrl = (htmlText) => {
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlText, 'text/html');
    const linkElements = Array.from(doc.querySelectorAll('a[href], [onclick]'));
    log(`[步骤3] 页面中共 ${linkElements.length} 个 a/onclick 元素`);

    const allCandidates = linkElements.map((el) => {
        const href = el.getAttribute('href') || '';
        const onclick = el.getAttribute('onclick') || '';
        const text = (el.textContent || '').trim();
        return { href, onclick, text };
    });

    // 打印所有含 kf_growup 的候选链接，便于调试
    const growupLinks = allCandidates.filter((c) => /kf_growup/i.test(c.href + c.onclick));
    log(`[步骤3] 含 kf_growup 的候选:`, growupLinks.map((c) => `${c.href || c.onclick.slice(0, 40)} | 文本=${c.text.slice(0, 20)}`));

    const signLinks = allCandidates
        .filter(({ href, onclick, text }) => {
            const candidate = href || extractActionUrlFromOnclick(onclick);
            if (!candidate || /javascript:/i.test(candidate)) {
                return false;
            }
            // 排除 ID 颜色设置链接（ok=2&color=）——它不是签到
            if (isColorSettingLink(candidate)) {
                log(`[步骤3] 排除颜色设置链接: ${candidate}`);
                return false;
            }
            // 排除包含“已领过”等拒绝提示的链接
            if (DENY_TEXT.some((deny) => text.includes(deny))) {
                log(`[步骤3] 排除含拒绝提示的链接: ${candidate} (文本含「${text.slice(0, 20)}」)`);
                return false;
            }
            // 优先：链接文本包含签到关键字
            if (TARGET_TEXT.some((target) => text.includes(target)) && /kf_growup\.php\?ok=\d+/i.test(candidate)) {
                log(`[步骤3] 命中文本关键字: ${candidate} (文本含「${text.slice(0, 20)}」)`);
                return true;
            }
            // 其次：kf_growup.php?ok=3（实测每日奖励签到动作，2026-08-03 验证）；ok=1 保留兼容
            if (/kf_growup\.php\?ok=[13](?:&|$)/i.test(candidate)) {
                log(`[步骤3] 命中 ok=1/3 动作链接: ${candidate}`);
                return true;
            }
            return false;
        });

    if (signLinks.length) {
        const chosen = signLinks[0].href || extractActionUrlFromOnclick(signLinks[0].onclick);
        log(`[步骤3] 选定签到链接 (共 ${signLinks.length} 个匹配): ${chosen}`);
        return chosen;
    }

    // 正则兜底：排除 ok=2 颜色设置
    const regex = /kf_growup\.php\?ok=(?!2(?:&|$))\d+&safeid=\d+(?:&color=\d+)?/i;
    const match = htmlText.match(regex);
    if (match) {
        log(`[步骤3] 正则兜底命中: ${match[0]}`);
    } else {
        logError(`[步骤3] 未找到任何签到链接（DOM 与正则均未命中）`);
    }
    return match ? match[0] : null;
};

const sendSignRequest = async (actionUrl) => {
    const normalizedUrl = normalizeActionUrl(actionUrl);
    if (!normalizedUrl) {
        logError(`[步骤4] 无效的签到 URL: ${actionUrl}`);
        return {
            ok: false,
            status: 0,
            alreadySigned: false,
            actionUrl,
            text: null,
        };
    }

    log(`[步骤4] 发送签到请求: ${normalizedUrl}`);
    const response = await fetch(normalizedUrl, {
        method: 'GET',
        credentials: 'include',
        headers: {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
    });
    const resultText = await decodeResponse(response);
    const already = isAlreadySigned(resultText);
    log(`[步骤4] 响应: HTTP ${response.status}, 已领取判定=${already}`);
    // 截取响应文本关键片段，便于判断签到结果
    const snippet = resultText.replace(/\s+/g, ' ').match(/今天的每日奖励[^<]{0,80}|签到[^<]{0,40}|奖励[^<]{0,40}/)?.[0] || '';
    if (snippet) {
        log(`[步骤4] 响应关键文本: ${snippet}`);
    }
    return {
        ok: response.ok,
        status: response.status,
        actionUrl: normalizedUrl,
        alreadySigned: already,
        text: resultText,
    };
};

const autoSignInOnAnyPage = async () => {
    const startedAt = new Date().toISOString();
    log(`===== 自动签到开始 @ ${startedAt} (${location.href}) =====`);

    const signPageText = await fetchSignPage();
    if (!signPageText) {
        const message = '无法获取签到页面内容。';
        logError(message);
        return {
            success: false,
            message,
            alreadySigned: false,
            actionUrl: null,
        };
    }

    const status = isAlreadySigned(signPageText);
    log(`[步骤2] 已领取判定: ${status}`);
    if (status) {
        const message = '今日奖励已领取，无需重复签到。';
        log(message);
        return {
            success: true,
            message,
            alreadySigned: true,
            actionUrl: null,
        };
    }
    log(`[步骤2] 未领取，继续查找签到链接`);

    const actionUrl = findSignActionUrl(signPageText);
    if (!actionUrl) {
        const message = '未能在签到页面找到签到动作链接。';
        logError(message);
        return {
            success: false,
            message,
            alreadySigned: false,
            actionUrl: null,
        };
    }

    const result = await sendSignRequest(actionUrl);
    if (!result.ok) {
        const message = `签到请求失败：${result.status}`;
        logError(message);
        return {
            success: false,
            message,
            alreadySigned: result.alreadySigned,
            actionUrl: result.actionUrl,
            responseText: result.text,
        };
    }

    if (result.alreadySigned) {
        const message = '签到请求已发送，但今天已领取奖励。';
        log(message);
        return {
            success: true,
            message,
            alreadySigned: true,
            actionUrl: result.actionUrl,
            responseText: result.text,
        };
    }

    const message = '签到请求发送成功，请检查页面是否已刷新或返回签到结果。';
    log(message);
    const endedAt = new Date().toISOString();
    log(`===== 自动签到结束 @ ${endedAt} (耗时 ${Date.parse(endedAt) - Date.parse(startedAt)}ms) =====`);
    return {
        success: true,
        message,
        alreadySigned: false,
        actionUrl: result.actionUrl,
        responseText: result.text,
    };
};

const goToSignPage = () => {
    location.href = location.origin + SIGN_PAGE;
};

const executeSignIn = async () => {
    return await autoSignInOnAnyPage();
};

const getDefaultSignModule = () => ({
    FORUM_HOST,
    SIGN_PAGE,
    fetchSignPage,
    isAlreadySigned,
    findSignActionUrl,
    sendSignRequest,
    autoSignInOnAnyPage,
    goToSignPage,
    executeSignIn,
});


window.ScarletmoonSignModule = getDefaultSignModule();


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
        console.log(`[绯月签到助手] 脚本启动 v0.6.0 @ ${location.href}`);
        if (location.host !== FORUM_HOST) {
            console.log(`[绯月签到助手] 非目标域名 ${location.host}，跳过。`);
            return;
        }

        const signModule = await getSignModule();
        if (!signModule) {
            console.error('[绯月签到助手] 无法加载签到模块。');
            return;
        }
        console.log(`[绯月签到助手] 模块加载成功，来源: ${window.ScarletmoonSignModule ? '内联构建版' : 'ES 模块动态加载'}`);

        registerMenu(signModule);
        await signModule.autoSignInOnAnyPage();
    };

    const registerMenu = (signModule) => {
        GM_registerMenuCommand('签到助手：打开签到页面', signModule.goToSignPage);
        GM_registerMenuCommand('签到助手：执行签到', signModule.executeSignIn);
    };

    await init();
})();
