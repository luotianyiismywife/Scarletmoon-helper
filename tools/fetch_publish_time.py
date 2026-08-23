# -*- coding: utf-8 -*-
"""从 docs/<资料目录>/<索引>.md 表格提取所有 tid+URL, 逐个 GET 帖子页提取真实发表时间。

用法:
    python tools/fetch_publish_time.py --limit 5   # 调试: 只处理前5篇
    python tools/fetch_publish_time.py             # 全部(咕咕镇-新争夺资料/06-论坛帖子索引.md)
    python tools/fetch_publish_time.py --dir 旧争夺资料 --index 03-论坛帖子索引.md

输出:
    docs/<资料目录>/publish_time.json  {tid: "YYYY-MM-DD HH:MM"}
"""
import argparse
import json
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = "咕咕镇-新争夺资料"
DEFAULT_INDEX = "06-论坛帖子索引.md"
MD_PATH = os.path.join(ROOT, "docs", DEFAULT_DIR, DEFAULT_INDEX)
OUT_PATH = os.path.join(ROOT, "docs", DEFAULT_DIR, "publish_time.json")
COOKIE_PATH = os.path.join(ROOT, "cookie.txt")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# 发表时间: 楼主信息区 "楼主 YYYY-MM-DD HH:MM" 或 "发表时间：YYYY-MM-DD HH:MM"
TIME_PATTERNS = [
    re.compile(r"楼主\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})"),
    re.compile(r"发表时间[：:]\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})"),
]


def extract_publish_time(html):
    for pat in TIME_PATTERNS:
        m = pat.search(html)
        if m:
            return m.group(1)
    return None


def make_session():
    auth = open(COOKIE_PATH, encoding="utf-8").read().strip()
    s = requests.Session()
    s.headers.update(HEADERS)
    for part in auth.split(";"):
        name, _, val = part.strip().partition("=")
        s.cookies.set(name, val, domain="bbs.kfpromax.com", path="/")
    return s


def get(session, url, tries=2):
    for i in range(tries):
        try:
            r = session.get(url, timeout=12)
            r.encoding = "gbk"
            return r.text
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5)


def collect_tids():
    md = open(MD_PATH, encoding="utf-8").read()
    rows = re.findall(
        r"^\| ([\d-]+) \| (.+?) \| https://bbs\.kfpromax\.com/read\.php\?tid=(\d+)&sf=([0-9a-f]+)[^|]* \|",
        md, re.M)
    # 去重保留顺序
    seen = set()
    out = []
    for date, title, tid, sf in rows:
        if tid not in seen:
            seen.add(tid)
            out.append((tid, title, sf))
    return out


def main():
    global MD_PATH, OUT_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.6)
    ap.add_argument("--dir", default=DEFAULT_DIR, help="资料目录名(docs/下)")
    ap.add_argument("--index", default=DEFAULT_INDEX, help="索引文件名")
    args = ap.parse_args()
    MD_PATH = os.path.join(ROOT, "docs", args.dir, args.index)
    OUT_PATH = os.path.join(ROOT, "docs", args.dir, "publish_time.json")

    posts = collect_tids()
    if args.limit:
        posts = posts[: args.limit]
    print(f"共 {len(posts)} 篇待抓")

    # 已有结果 (断点续传)
    results = {}
    if os.path.exists(OUT_PATH):
        results = json.load(open(OUT_PATH, encoding="utf-8"))
        print(f"已有 {len(results)} 条结果, 跳过已抓的")

    session = make_session()
    ok = fail = 0
    for n, (tid, title, sf) in enumerate(posts, 1):
        if tid in results:
            continue
        try:
            html = get(session, f"https://bbs.kfpromax.com/read.php?tid={tid}&sf={sf}")
            t = extract_publish_time(html)
            if t:
                results[tid] = t
                ok += 1
                if ok % 10 == 0:
                    print(f"  [进度] {n}/{len(posts)} 成功{ok} 失败{fail}", flush=True)
            else:
                # 未找到: 可能是被删/关闭的帖子
                results[tid] = ""
                fail += 1
                print(f"  [无时间] {tid} {title[:30]}", flush=True)
        except Exception as e:
            results[tid] = ""
            fail += 1
            print(f"  [FAIL] {tid} {title[:30]}: {type(e).__name__}", flush=True)
        if (ok + fail) % 20 == 0:
            json.dump(results, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(args.delay)

    json.dump(results, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n完成: 成功 {ok}, 无时间/失败 {fail}, 输出: {OUT_PATH}")


if __name__ == "__main__":
    main()
