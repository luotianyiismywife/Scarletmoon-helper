# -*- coding: utf-8 -*-
"""从 Firefox Nightly 配置文件的 cookies.sqlite 中提取论坛 + 咕咕镇游戏 Cookie。

用法:
    python tools/get_cookies.py            # 提取全部（论坛 + 咕咕镇游戏）
    python tools/get_cookies.py --forum    # 仅论坛 bbs.kfpromax.com
    python tools/get_cookies.py --game     # 仅咕咕镇 www.momozhen.com

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
"""
import os
import sqlite3
import sys

PROFILE = "30hfbhjk.default-nightly"
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "cookie.txt")

# 域名关键字 -> 说明
DOMAINS = {
    "forum": ("kfpromax", "论坛认证 (2ed4e_*)"),
    "game": ("momozhen", "咕咕镇游戏 (fyg2019_*)"),
}


def main():
    select = {"forum", "game"}
    if "--forum" in sys.argv:
        select = {"forum"}
    elif "--game" in sys.argv:
        select = {"game"}

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

    if not all_cookies:
        print("[错误] 没有任何 Cookie, 请先在浏览器登录")
        sys.exit(1)

    cookie_str = "; ".join(f"{name}={value}" for name, value in all_cookies)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(cookie_str)

    print(f"[完成] 共 {len(all_cookies)} 个 Cookie, 已写入 {os.path.normpath(OUTPUT)}")


if __name__ == "__main__":
    main()
