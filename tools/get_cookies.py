# -*- coding: utf-8 -*-
"""从 Firefox Nightly 配置文件的 cookies.sqlite 中提取论坛 + 咕咕镇游戏 Cookie。

用法:
    python tools/get_cookies.py                    # 提取全部（论坛 + 咕咕镇游戏）
    python tools/get_cookies.py --forum            # 仅论坛 bbs.kfpromax.com
    python tools/get_cookies.py --game             # 仅咕咕镇 www.momozhen.com
    python tools/get_cookies.py --login            # 账号密码登录论坛并刷新 cookie

输出:
    cookie.txt (项目根目录, 已 gitignore) —— 一行 "name=value; name=value" 格式,
    供 fetch_posts.py / 咕咕镇接口调用作为 Cookie 头使用。

说明:
    - 论坛认证 Cookie (2ed4e_*) 与咕咕镇游戏 Cookie (fyg2019_*) 均为 HttpOnly,
      document.cookie 拿不到, 因此直接读取 Firefox 的 cookies.sqlite (只读模式,
      浏览器运行中也可读)。
    - 咕咕镇实际运行在 www.momozhen.com（经 bbs.kfpromax.com 跳转链进入）,
      认证 Cookie: fyg2019_gameuid/gamepw/endtime/logme; PHPSESSID 为会话
      Cookie 不落盘, 由服务器在首次请求时自动补发。
    - --login 模式：通过论坛账号密码模拟登录（PHPWind 登录表单, 无验证码）,
      登录后自动走入口跳转链, 让服务器下发新的游戏 Cookie（含新 endtime）。
      账号密码从环境变量 KF_USER / KF_PASS 读取（不写入代码与文件）。
"""
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

PROFILE = "30hfbhjk.default-nightly"
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "cookie.txt")

FORUM_BASE = "https://bbs.kfpromax.com"
GAME_BASE = "https://www.momozhen.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0"

# 域名关键字 -> 说明
DOMAINS = {
    "forum": ("kfpromax", "论坛认证 (2ed4e_*)"),
    "game": ("momozhen", "咕咕镇游戏 (fyg2019_*)"),
}


def extract_from_firefox(select):
    """从 Firefox cookies.sqlite 提取 cookie（浏览器登录态）"""
    db = os.path.join(os.environ["APPDATA"], "Mozilla", "Firefox", "Profiles", PROFILE, "cookies.sqlite")
    if not os.path.exists(db):
        print(f"[错误] 找不到 cookies 数据库: {db}")
        sys.exit(1)

    uri = "file:" + db.replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)

    all_cookies = []
    for key, (keyword, desc) in DOMAINS.items():
        if key not in select:
            continue
        rows = con.execute(
            "SELECT name, value, host, path, isSecure, isHttpOnly FROM moz_cookies WHERE host LIKE ?",
            (f"%{keyword}%",),
        ).fetchall()
        print(f"[{desc}] 共 {len(rows)} 个")
        if not rows:
            print(f"  ⚠️ 未找到 {keyword} 的 Cookie, 请先在浏览器登录")
        for name, value, host, path, secure, httponly in rows:
            all_cookies.append((name, value))
            shown = value if len(value) <= 12 else value[:6] + "..." + value[-4:]
            print(f"  {host}{path}  {name}={shown}  (HttpOnly={bool(httponly)})")
    con.close()
    return all_cookies


def login_and_refresh():
    """账号密码登录论坛 + 走入口链刷新游戏 cookie。

    返回 (cookie 列表)
    """
    user = os.environ.get("KF_USER", "").strip()
    pwd = os.environ.get("KF_PASS", "").strip()
    if not user or not pwd:
        print("[错误] --login 需要环境变量 KF_USER / KF_PASS（论坛账号/密码）")
        sys.exit(1)

    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def http(url, data=None, referer=None, retries=3):
        headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                   "Accept-Language": "zh-CN,zh;q=0.9"}
        if referer:
            headers["Referer"] = referer
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, data=data, headers=headers)
                resp = opener.open(req, timeout=20)
                return resp.geturl(), resp.read()
            except Exception as e:
                print(f"  重试 {attempt + 1}/{retries}: {e}")
                time.sleep(2)
        return None, None

    # 1. 登录论坛（PHPWind 表单）
    form = {
        "forward": "", "jumpurl": FORUM_BASE + "/index.php", "step": "2",
        "lgt": "1", "hideid": "0", "cktime": "31536000",
        "pwuser": user, "pwpwd": pwd, "submit": "登录",
    }
    print(f"[1] 登录论坛 {user} ...")
    final_url, body = http(FORUM_BASE + "/login.php?", urllib.parse.urlencode(form).encode(),
                           referer=FORUM_BASE + "/login.php")
    text = body.decode("gbk", errors="replace") if body else ""
    if not body:
        print("[错误] 登录请求失败（网络/SSL）")
        sys.exit(1)

    # 登录成功判定：跳回主页 / 无"登录失败"提示
    if "登录失败" in text or "密码错误" in text or "请重试" in text[:500]:
        print(f"[错误] 登录失败: {text[:200]}")
        sys.exit(1)
    print(f"[2] 登录完成 → {final_url}")

    # 2. 走入口链刷新游戏 cookie
    print("[3] 走入口链刷新咕咕镇 cookie ...")
    time.sleep(1)
    url2, body2 = http(FORUM_BASE + "/fyg_sjcdwj.php?go=play&xl=2", referer=FORUM_BASE + "/index.php")
    if body2:
        # 若跳到 fyg_login.php?m=li 自动登录链
        if url2 and "fyg_login" in url2:
            time.sleep(1)
            url2, body2 = http(url2, referer=FORUM_BASE + "/fyg_sjcdwj.php?go=play&xl=2")
        # meta refresh 跳转
        if body2 and b"url=" in body2.lower():
            m = re.search(rb"url=([^\"']+)", body2)
            if m:
                time.sleep(1)
                url2, body2 = http(m.group(1).decode(), referer=url2 or GAME_BASE + "/")
    time.sleep(1)
    _, body3 = http(GAME_BASE + "/fyg_index.php", referer=url2 or GAME_BASE + "/")
    if body3:
        ok = ("个人信息".encode("utf-8") in body3) or (user.encode("utf-8") in body3)
        print(f"[4] 游戏主页: ({len(body3)} 字节) 登录{'成功' if ok else '可能失败'}")

    # 3. 收集 jar 中的 cookie
    all_cookies = []
    for c in jar:
        all_cookies.append((c.name, c.value))
    print(f"[5] 会话共 {len(all_cookies)} 个 Cookie")
    return all_cookies


def main():
    if "--login" in sys.argv:
        cookies = login_and_refresh()
    else:
        select = {"forum", "game"}
        if "--forum" in sys.argv:
            select = {"forum"}
        elif "--game" in sys.argv:
            select = {"game"}
        cookies = extract_from_firefox(select)

    if not cookies:
        print("[错误] 没有任何 Cookie")
        sys.exit(1)

    cookie_str = "; ".join(f"{name}={value}" for name, value in cookies)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(cookie_str)

    print(f"[完成] 共 {len(cookies)} 个 Cookie, 已写入 {os.path.normpath(OUTPUT)}")


if __name__ == "__main__":
    main()
