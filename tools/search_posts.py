# -*- coding: utf-8 -*-
"""全站搜索论坛帖子标题关键字, 抓取所有结果页, 输出 tid/标题/最后回复时间/URL。

用途:
    为「旧争夺」等新主题建立帖子索引前的探测与批量收集。
    搜索页"发表"列 = 最后回复时间(非发帖时间), 真实发表时间需进帖子页(见 fetch_publish_time.py)。

用法:
    python tools/search_posts.py --kw 争夺                 # 搜索并打印全部结果
    python tools/search_posts.py --kw 争夺 --json out.json # 结果存 JSON
    python tools/search_posts.py --kw 争夺 --pages 1       # 只抓第1页(探测用, 省搜索次数)

输出 JSON 结构: [{tid, title, url, lastreply, forum}]

依赖:
    cookie.txt（由 tools/get_cookies.py 生成, 已 gitignore）
注意:
    每日搜索次数有限(约30次/日), 探测时先用 --pages 1。
"""
import argparse
import html as htmllib
import json
import os
import re
import sys
import time
from urllib.parse import quote

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_PATH = os.path.join(ROOT, "cookie.txt")
BASE = "https://bbs.kfpromax.com"

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
    s = TAG_RE.sub("", s)
    s = htmllib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def make_session():
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


def gbk_quote(kw):
    return quote(kw.encode("gbk"))


def parse_result_rows(html):
    """解析搜索结果表格行: 返回 [{tid,title,url,sf,forum,lastreply}]。

    只认带 keyword= 的 read.php 链接(搜索结果特征), 排除侧栏/最新帖列表。
    逐 <tr> 块解析, 提取标题/版块/最后回复时间。
    """
    rows = []
    for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        seg = tr.group(1)
        m = re.search(
            r'<a href="read\.php\?tid=(\d+)&sf=([0-9a-f]+)[^"]*keyword=[^"]*"[^>]*>(.*?)</a>',
            seg, re.S)
        if not m:
            continue
        tid, sf, raw_title = m.groups()
        title = strip_tags(raw_title)
        if not title:
            continue
        # 最后回复时间: 行内 YYYY-MM-DD HH:MM
        dm = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", seg)
        rows.append({
            "tid": tid,
            "title": title,
            "url": f"{BASE}/read.php?tid={tid}&sf={sf}",
            "sf": sf,
            "lastreply": dm.group(1) if dm else "",
        })
    return rows


def parse_meta(html):
    """提取 总条数 / 剩余搜索次数 / 总页数 / sid。"""
    meta = {}
    m = re.search(r"共搜索到了\s*(\d+)\s*条信息", html)
    meta["total"] = int(m.group(1)) if m else None
    m = re.search(r"本日剩余搜索次数\s*(\d+)\s*次", html)
    meta["left"] = int(m.group(1)) if m else None
    m = re.search(r"step=2&[^\"']*?sid=([0-9a-f]+)", html) or re.search(r"sid=([0-9a-f]{8,})", html)
    meta["sid"] = m.group(1) if m else None
    # 页数: 找 page=N 的最大值
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", html)]
    meta["max_page"] = max(pages) if pages else 1
    return meta


def search(session, kw, max_pages=0, delay=0.8, dump=""):
    """执行搜索, 返回 (rows, meta)。max_pages=0 表示抓全部页。dump=首屏HTML保存路径。"""
    kw_enc = gbk_quote(kw)
    # 1) GET search.php 建立会话
    session.get(f"{BASE}/search.php", timeout=20)
    # 2) POST 搜索 —— keyword 必须 GBK URL 编码(网站api资料.md §3.3),
    #    requests dict 表单默认 UTF-8 会乱码, 因此手工拼 raw body
    body = (
        "step=2&method=AND&sch_area=0&s_type=forum&f_fid=all"
        "&orderway=lastpost&asc=DESC&keyword=" + kw_enc +
        "&pwuser=&submit=" + quote("全站搜索".encode("gbk"))
    )
    r = session.post(
        f"{BASE}/search.php?", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=25)
    r.encoding = "gbk"
    html = r.text
    if dump:
        with open(dump, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[dump] 首屏 HTML 已存: {dump}")
    meta = parse_meta(html)
    all_rows = parse_result_rows(html)

    total_pages = meta.get("max_page", 1)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    sid = meta.get("sid")
    for p in range(2, total_pages + 1):
        time.sleep(delay)
        url = f"{BASE}/search.php?step=2&keyword={kw_enc}&page={p}"
        if sid:
            url += f"&sid={sid}"
        rp = session.get(url, timeout=25)
        rp.encoding = "gbk"
        all_rows.extend(parse_result_rows(rp.text))

    # 去重 (按 tid)
    seen = set()
    uniq = []
    for row in all_rows:
        if row["tid"] not in seen:
            seen.add(row["tid"])
            uniq.append(row)
    return uniq, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", required=True, help="搜索关键字")
    ap.add_argument("--pages", type=int, default=0, help="最多抓几页(0=全部)")
    ap.add_argument("--json", default="", help="结果输出 JSON 路径")
    ap.add_argument("--dump", default="", help="首屏结果页 HTML 保存路径(调试用)")
    ap.add_argument("--delay", type=float, default=0.8)
    args = ap.parse_args()

    session = make_session()
    rows, meta = search(session, args.kw, args.pages, args.delay, dump=args.dump)
    print(f"关键字: {args.kw}")
    print(f"总条数(服务器报告): {meta.get('total')}  剩余搜索次数: {meta.get('left')}  总页数: {meta.get('max_page')}")
    print(f"本次抓到(去重): {len(rows)} 条")
    print("-" * 60)
    for row in rows[:40]:
        print(f"  {row['tid']}  {row['title']}")
    if len(rows) > 40:
        print(f"  ... 及另外 {len(rows)-40} 条")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f"\n已存: {args.json}")


if __name__ == "__main__":
    main()
