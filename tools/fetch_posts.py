# -*- coding: utf-8 -*-
"""批量抓取咕咕镇帖子正文并保存到 咕咕镇资料/raw/。

用法:
    python tools/fetch_posts.py            # 抓取 咕咕镇资料/咕咕镇.md 中所有 ⬜ 未读帖子
    python tools/fetch_posts.py --limit 5  # 只抓前 5 篇（调试用）
    python tools/fetch_posts.py --tid 1052153   # 只抓指定 tid

依赖:
    cookie.txt（由 tools/get_cookies.py 生成, 已 gitignore）
"""
import argparse
import html as htmllib
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "咕咕镇资料", "raw")
MD_PATH = os.path.join(ROOT, "咕咕镇资料", "咕咕镇.md")
COOKIE_PATH = os.path.join(ROOT, "cookie.txt")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(s):
    s = re.sub(r"<script.*?</script>", "", s, flags=re.S)
    s = re.sub(r"<style.*?</style>", "", s, flags=re.S)
    s = TAG_RE.sub("", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\u3000]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def make_session():
    """带认证 Cookie 的 Session, 服务器会自动补 PHPSESSID。"""
    if not os.path.exists(COOKIE_PATH):
        print("[错误] 缺少 cookie.txt, 请先运行: python tools/get_cookies.py")
        sys.exit(1)
    auth = open(COOKIE_PATH, encoding="utf-8").read().strip()
    s = requests.Session()
    s.headers.update(HEADERS)
    for part in auth.split(";"):
        name, _, val = part.strip().partition("=")
        s.cookies.set(name, val, domain="bbs.kfpromax.com", path="/")
    return s


def fetch_html(session, url):
    r = session.get(url, timeout=25)
    r.encoding = "gbk"
    if "action=quit" not in r.text:
        raise RuntimeError("未登录 (无退出链接)")
    return r.text


def parse_post(html):
    """解析帖子页: 返回 {title, floors: [{id, author, date, text}]}"""
    m = re.search(r"<title>(.*?)</title>", html)
    title = m.group(1).strip() if m else "?"
    title = re.sub(r"\|.*?- 绯月ScarletMoon$", "", title).strip()

    floors = []
    # 楼主
    m = re.search(r'<div class="readtext"[^>]*id="pidtpc"[^>]*>(.*?)(?=<div class="readtext"|$)', html, re.S)
    # 通用: 匹配所有 readtext 块
    blocks = list(re.finditer(r'<div class="readtext"[^>]*id="pid([^"]+)"[^>]*>(.*?)(?=<div class="readtext"|$)', html, re.S))
    if not blocks:
        return {"title": title, "floors": []}
    for b in blocks:
        pid = b.group(1)
        seg = b.group(2)
        # 作者
        am = re.search(r'<a href="profile\.php\?action=show&uid=\d+[^"]*"[^>]*>([^<]+)</a>', seg)
        author = am.group(1).strip() if am else "?"
        # 日期
        dm = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", seg)
        date = dm.group(1) if dm else ""
        # 正文: 取 "菜单" 链接之后、楼层表格结束(</table>)之前的内容
        cm = re.search(r"class=\"readcza\">菜单</a>\s*(.*?)(?=</table>)", seg, re.S)
        text = strip_tags(cm.group(1)) if cm else ""
        if not text:
            text = strip_tags(seg)[-500:]
        floors.append({"id": pid, "author": author, "date": date, "text": text})
    return {"title": title, "floors": floors}


def unread_posts():
    """从 咕咕镇.md 表格解析未读帖子。"""
    md = open(MD_PATH, encoding="utf-8").read()
    rows = re.findall(
        r"^\| ([\d-]+) \| (.+?) \| (https://bbs\.kfpromax\.com/read\.php\?tid=\d+[^|]*) \| (⬜) \|",
        md, re.M)
    return [(d, t, u) for d, t, u, _ in rows]  # (date, title, url)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tid", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.8, help="请求间隔秒")
    args = ap.parse_args()

    posts = unread_posts()
    if args.tid:
        posts = [p for p in posts if str(args.tid) in p[2]]
    if args.limit:
        posts = posts[: args.limit]

    print(f"待抓取: {len(posts)} 篇")
    os.makedirs(RAW_DIR, exist_ok=True)

    session = make_session()
    ok = fail = 0
    for date, title, url in posts:
        tid = re.search(r"tid=(\d+)", url).group(1)
        out_path = os.path.join(RAW_DIR, f"{tid}.txt")
        if os.path.exists(out_path):
            print(f"  [跳过] {tid} 已存在")
            ok += 1
            continue
        try:
            html = fetch_html(session, url)
            post = parse_post(html)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"标题: {post['title']}\n日期: {date}\nURL: {url}\n\n")
                for fl in post["floors"]:
                    f.write(f"--- 楼层[{fl['id']}] {fl['author']} {fl['date']} ---\n")
                    f.write(fl["text"] + "\n\n")
            n = len(post["floors"])
            first = post["floors"][0]["text"][:60].replace("\n", " ") if post["floors"] else ""
            print(f"  [OK] {tid} 楼层={n} 楼主: {first}")
            ok += 1
        except Exception as e:
            print(f"  [FAIL] {tid} {title}: {e}")
            fail += 1
        time.sleep(args.delay)

    print(f"\n完成: 成功 {ok}, 失败 {fail}, 输出目录: {RAW_DIR}")


if __name__ == "__main__":
    main()
