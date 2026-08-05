# -*- coding: utf-8 -*-
"""从 Firefox Nightly 配置文件的 cookies.sqlite 中提取论坛 Cookie。

用法:
    python tools/get_cookies.py

输出:
    cookie.txt (项目根目录, 已 gitignore) —— 一行 "name=value; name=value" 格式,
    供 fetch_posts.py 等脚本作为 Cookie 头使用。

说明:
    论坛认证 Cookie 是 HttpOnly 的, document.cookie 拿不到,
    因此直接读取 Firefox 的 cookies.sqlite (只读模式, 浏览器运行中也可读)。
"""
import os
import sqlite3
import sys

PROFILE = "30hfbhjk.default-nightly"
HOST_KEYWORD = "kfpromax"
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "cookie.txt")


def main():
    db = os.path.join(os.environ["APPDATA"], "Mozilla", "Firefox", "Profiles", PROFILE, "cookies.sqlite")
    if not os.path.exists(db):
        print(f"[错误] 找不到 cookies 数据库: {db}")
        sys.exit(1)

    uri = "file:" + db.replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    rows = con.execute(
        "SELECT name, value, host, path, isSecure, isHttpOnly FROM moz_cookies WHERE host LIKE ?",
        (f"%{HOST_KEYWORD}%",),
    ).fetchall()
    con.close()

    if not rows:
        print(f"[错误] cookies 数据库中没有 {HOST_KEYWORD} 的 Cookie, 请先在浏览器登录论坛")
        sys.exit(1)

    cookie_str = "; ".join(f"{name}={value}" for name, value, *_ in rows)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(cookie_str)

    print(f"[完成] 共 {len(rows)} 个 Cookie, 已写入 {os.path.normpath(OUTPUT)}")
    for name, value, host, path, secure, httponly in rows:
        shown = value if len(value) <= 12 else value[:6] + "..." + value[-4:]
        print(f"  {host}{path}  {name}={shown}  (HttpOnly={bool(httponly)})")


if __name__ == "__main__":
    main()
