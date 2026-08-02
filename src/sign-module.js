const FORUM_HOST = 'bbs.kfpromax.com';
const SIGN_PAGE = '/kf_growup.php';
const DENY_TEXT = ['已经领过', '请明天继续', '已领过', '今日已领', '今天已领取', '已领取'];
const TARGET_TEXT = ['领取', '签到', '每日奖励', '领取奖励', '今日奖励'];
const SIGN_STATUS_REGEX = /今天的每日奖励已经领过了，请明天继续。|今日奖励已领取|已领过|请明天继续/;

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

export const fetchSignPage = async () => {
    const response = await fetch(`${location.origin}${SIGN_PAGE}`, {
        method: 'GET',
        credentials: 'include',
        headers: {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
    });
    return response.ok ? decodeResponse(response) : null;
};

export const isAlreadySigned = (htmlText) => {
    return SIGN_STATUS_REGEX.test(htmlText);
};

export const parseSignStatus = (htmlText) => {
    return {
        alreadySigned: isAlreadySigned(htmlText),
        hasTryAgainText: /请明天继续/.test(htmlText),
    };
};

export const findSignActionUrl = (htmlText) => {
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlText, 'text/html');
    const linkElements = Array.from(doc.querySelectorAll('a[href], [onclick]'));

    const signLinks = linkElements
        .map((el) => {
            const href = el.getAttribute('href') || '';
            const onclick = el.getAttribute('onclick') || '';
            const text = (el.textContent || '').trim();
            return { href, onclick, text };
        })
        .filter(({ href, onclick, text }) => {
            const candidate = href || extractActionUrlFromOnclick(onclick);
            if (!candidate || /javascript:/i.test(candidate)) {
                return false;
            }
            // 排除 ID 颜色设置链接（ok=2&color=）——它不是签到
            if (isColorSettingLink(candidate)) {
                return false;
            }
            // 排除包含“已领过”等拒绝提示的链接
            if (DENY_TEXT.some((deny) => text.includes(deny))) {
                return false;
            }
            // 优先：链接文本包含签到关键字
            if (TARGET_TEXT.some((target) => text.includes(target)) && /kf_growup\.php\?ok=\d+/i.test(candidate)) {
                return true;
            }
            // 其次：kf_growup.php?ok=1（每日领取签到动作）
            if (/kf_growup\.php\?ok=1(?:&|$)/i.test(candidate)) {
                return true;
            }
            return false;
        });

    if (signLinks.length) {
        return signLinks[0].href || extractActionUrlFromOnclick(signLinks[0].onclick);
    }

    // 正则兜底：排除 ok=2 颜色设置
    const regex = /kf_growup\.php\?ok=(?!2(?:&|$))\d+&safeid=\d+(?:&color=\d+)?/i;
    const match = htmlText.match(regex);
    return match ? match[0] : null;
};

export const sendSignRequest = async (actionUrl) => {
    const normalizedUrl = normalizeActionUrl(actionUrl);
    if (!normalizedUrl) {
        return {
            ok: false,
            status: 0,
            alreadySigned: false,
            actionUrl,
            text: null,
        };
    }

    const response = await fetch(normalizedUrl, {
        method: 'GET',
        credentials: 'include',
        headers: {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        },
    });
    const resultText = await decodeResponse(response);
    return {
        ok: response.ok,
        status: response.status,
        actionUrl: normalizedUrl,
        alreadySigned: isAlreadySigned(resultText),
        text: resultText,
    };
};

export const autoSignInOnAnyPage = async () => {
    const signPageText = await fetchSignPage();
    if (!signPageText) {
        const message = '无法获取签到页面内容。';
        console.log(`[绯月签到助手] ${message}`);
        return {
            success: false,
            message,
            alreadySigned: false,
            actionUrl: null,
        };
    }

    if (isAlreadySigned(signPageText)) {
        const message = '今日奖励已领取，无需重复签到。';
        console.log(`[绯月签到助手] ${message}`);
        return {
            success: true,
            message,
            alreadySigned: true,
            actionUrl: null,
        };
    }

    const actionUrl = findSignActionUrl(signPageText);
    if (!actionUrl) {
        const message = '未能在签到页面找到签到动作链接。';
        console.log(`[绯月签到助手] ${message}`);
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
        console.error(`[绯月签到助手] ${message}`);
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
        console.log(`[绯月签到助手] ${message}`);
        return {
            success: true,
            message,
            alreadySigned: true,
            actionUrl: result.actionUrl,
            responseText: result.text,
        };
    }

    const message = '签到请求发送成功，请检查页面是否已刷新或返回签到结果。';
    console.log(`[绯月签到助手] ${message}`);
    return {
        success: true,
        message,
        alreadySigned: false,
        actionUrl: result.actionUrl,
        responseText: result.text,
    };
};

export const goToSignPage = () => {
    location.href = location.origin + SIGN_PAGE;
};

export const executeSignIn = async () => {
    return await autoSignInOnAnyPage();
};

export const getDefaultSignModule = () => ({
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
