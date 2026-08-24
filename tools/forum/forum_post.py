# -*- coding: utf-8 -*-
"""绯月论坛发帖/回帖/查回复工具（PHPWind，2026-08-14 实测）。

用法:
    python tools/forum/forum_post.py new --title "标题" --content-file body.txt [--fid 5]
    python tools/forum/forum_post.py new --title "标题" --content "正文" [--fid 5]
    python tools/forum/forum_post.py new --title-file title.txt --content-file body.txt [--fid 5]  # 标题从文件首行读
    python tools/forum/forum_post.py reply --tid 123456 --content "回复内容" [--fid 5]
    python tools/forum/forum_post.py reply --tid 123456 --content "内容" --floor 8         # 回复 8 楼
    python tools/forum/forum_post.py reply --tid 123456 --content "内容" --floors "8,9"   # 一次回复 8/9 两楼
    python tools/forum/forum_post.py check --tid 123456 --sf abc

标题建议用 --title-file（标题含空格/引号/括号时命令行传参易被 shell 解析坏，
2026-08-14 踩坑教训）；标题文件取第一行，其余行忽略。

接口（详见 docs/网站api资料.md §3.6）:
    发新帖: POST post.php?  action=new&step=2&fid=<版块>&tid=0&atc_title=&atc_content=&verify=
    回帖:   POST post.php?  action=reply&step=2&fid=<版块>&tid=<帖子>&atc_content=&verify=
    verify: 表单页 hidden 字段（会话级防伪令牌），发帖前先 GET 表单页动态提取

⚠️ 编码坑：论坛是 GBK！POST body 必须手工 GBK URL 编码（urllib dict 默认 UTF-8
   会被服务器静默拒绝/跳回首页，与 search.php 同一个坑，2026-08-13 实测）。
依赖: cookie.txt（含论坛 2ed4e_* cookie，tools/get_cookies.py 提取）
"""
import argparse
import os
import re
import sys
import time
import urllib.parse

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://bbs.kfpromax.com"

COOKIE_PATH = os.path.join(os.path.dirname(__file__), "..", "cookie.txt")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def make_session():
    """requests.Session + 域内 cookie（与 fetch_posts.py 一致的可用方案）。

    ⚠️ 必须用 requests.Session 让服务器 Set-Cookie 的 PHPSESSID 自动随后续请求发送，
    否则会话建立不了、所有页面被重定向到 login.php（2026-08-14 实测，urllib 手拼 Cookie 头不行）。
    """
    if not os.path.exists(COOKIE_PATH):
        print("[错误] 缺少 cookie.txt, 请先运行: python tools/get_cookies.py")
        sys.exit(1)
    auth = open(COOKIE_PATH, encoding="utf-8").read().strip()
    s = requests.Session()
    s.headers.update(HEADERS)
    for part in auth.split(";"):
        name, _, val = part.strip().partition("=")
        if name:
            s.cookies.set(name, val, domain="bbs.kfpromax.com", path="/")
    return s


SESSION = make_session()


def gbk_form(fields):
    """把 dict 编码成 GBK 的 application/x-www-form-urlencoded body（bytes）。

    PHPWind 论坛是 GBK 站，UTF-8 提交的中文会变乱码/被拒，必须逐字段
    .encode('gbk') 后 percent-encode。
    """
    parts = []
    for k, v in fields.items():
        v = str(v)
        parts.append(urllib.parse.quote(k) + "=" + urllib.parse.quote(v.encode("gbk", "replace")))
    return "&".join(parts).encode("ascii")


def http(url, data=None, referer=None, retries=4):
    """GET/POST，返回 (最终URL, GBK解码文本)。requests 自动跟随 302、管理 PHPSESSID。

    bbs.kfpromax.com 偶发 SSL 瞬断（UNEXPECTED_EOF_WHILE_READING，限流），带重试。
    """
    headers = {}
    if referer:
        headers["Referer"] = referer
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    last_err = None
    for attempt in range(retries):
        try:
            if data is not None:
                r = SESSION.post(url, data=data, headers=headers, timeout=30)
            else:
                r = SESSION.get(url, headers=headers, timeout=30)
            r.encoding = "gbk"
            return r.url, r.text
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
            last_err = e
            print(f"  ⚠️ 请求重试 {attempt + 1}/{retries}: {type(e).__name__}")
            time.sleep(2)
    raise last_err


def get_verify(form_url):
    """GET 表单页，提取 verify 防伪令牌 + 登录态检查。"""
    _, text = http(form_url, referer=BASE + "/index.php")
    if "login.php?action=quit" not in text:
        print("⚠️ 页面未见退出登录链接，cookie 可能已失效 → 先跑 tools/get_cookies.py")
    m = re.search(r'name="verify"\s+value="([0-9a-f]+)"', text)
    if not m:
        m = re.search(r'name="verify"[^>]*value="([0-9a-f]+)"', text)
    if not m:
        print("[错误] 未找到 verify 令牌，页面结构可能变了。页面前 300 字符:")
        print(text[:300])
        sys.exit(1)
    return m.group(1)


def new_thread(title, content, fid=5):
    """发新帖。返回 (tid, url)。"""
    form_url = f"{BASE}/post.php?action=new&fid={fid}"
    verify = get_verify(form_url)
    print(f"[1] verify={verify} fid={fid}")
    fields = {
        "magicname": "", "magicid": "", "verify": verify,
        "atc_autourl": "1", "atc_usesign": "1", "atc_convert": "1",
        "atc_iconid": "93",
        "atc_title": title,
        "atc_content": content,
        "diy_guanjianci": "",
        "atc_downrvrc1": "0", "atc_desc1": "",
        "step": "2", "pid": "", "action": "new",
        "fid": str(fid), "tid": "0", "article": "", "special": "0",
        "Submit": "确定发表",
    }
    final_url, text = http(BASE + "/post.php?", data=gbk_form(fields), referer=form_url)
    print(f"[2] 提交后落地: {final_url}")
    m = re.search(r"tid=(\d+)", final_url)
    if not m:
        m = re.search(r"tid=(\d+)", text)
    if not m:
        print("[错误] 未在响应中找到 tid，可能发帖失败。响应前 500 字符:")
        print(text[:500])
        sys.exit(1)
    tid = m.group(1)
    # 提取 sf（落地 URL 或页面内链接）
    sf = (re.search(r"[?&]sf=([0-9a-f]+)", final_url) or re.search(r"tid=%s&sf=([0-9a-f]+)" % tid, text))
    sf_v = sf.group(1) if sf else ""
    print(f"✅ 发帖成功 tid={tid} sf={sf_v}")
    print(f"   链接: {BASE}/read.php?tid={tid}&sf={sf_v}")
    return tid, f"{BASE}/read.php?tid={tid}&sf={sf_v}"


def reply(tid, content, fid=None, sf="", floor=None, floors=None):
    """回帖（可选回复指定楼层 / 多楼层）。

    PHPWind 无 article 字段（2026-08-16 实测），"回复楼层" = 正文加
    [quote]回 N楼(作者) 的帖子[/quote] 前缀 + diy_guanjianci 设作者名（@对方），
    与前端 postreply() 行为一致。
    floor: 0=楼主 / 1=第1个回复 / 2=第2个回复 ...（None 不指定楼层）
    floors: 逗号分隔多楼层（如 "8,9"），正文拼多个 [quote] 前缀（一次回复多楼，
            2026-08-16 浏览器实测成功）
    """
    read_url = f"{BASE}/read.php?tid={tid}" + (f"&sf={sf}" if sf else "")
    _, page = http(read_url, referer=BASE + "/index.php")
    if "login.php?action=quit" not in page:
        print("⚠️ 未见退出登录链接，cookie 可能失效 → 先跑 tools/get_cookies.py")
    verify = re.search(r'name="verify"\s+value="([0-9a-f]+)"', page)
    if not verify:
        print("[错误] 未在 read.php 找到 verify（页面结构变了/无权限回帖）。前 300 字符:")
        print(page[:300])
        sys.exit(1)
    verify = verify.group(1)
    if fid is None:
        fm = re.search(r'name="fid"\s+value="(\d+)"', page)
        fid = int(fm.group(1)) if fm else 5

    # 收集所有楼层的 postreply('回 N楼(作者) 的帖子','作者') 模板
    # （每个回复一个；0=楼主）
    floor_authors = {}
    for m in re.finditer(r"postreply\('回 (\d+)楼\(([^)]+)\) 的帖子','([^']*)'\)", page):
        floor_authors[int(m.group(1))] = (m.group(2).strip(), m.group(3).strip())

    targets = []
    if floors:
        targets = [int(x) for x in floors.split(",") if x.strip()]
    elif floor is not None:
        targets = [floor]

    quote_prefix, keyword = "", ""
    if targets:
        parts = []
        authors = []
        for f in targets:
            if f not in floor_authors:
                print(f"[错误] 未找到 {f} 楼（postreply 匹配失败，楼层可能不存在或页面无此格式）")
                sys.exit(1)
            author, kw = floor_authors[f]
            parts.append(f"[quote]回 {f}楼({author}) 的帖子[/quote]")
            authors.append(author)
            print(f"[0] 回复 {f} 楼（作者 {author}）")
        quote_prefix = "\r\n".join(parts) + "\r\n"
        keyword = ",".join(authors)  # 逗号分隔多作者，渲染成多个关键词

    print(f"[1] verify={verify} fid={fid} tid={tid}")
    fields = {
        "diy_guanjianci": keyword,
        "atc_title": "none", "atc_usesign": "1", "atc_convert": "1", "atc_autourl": "1",
        "atc_content": quote_prefix + content,
        "step": "2", "action": "reply",
        "fid": str(fid), "tid": str(tid), "verify": verify,
        "Submit": "回复帖子",
    }
    # 回帖前楼层数（用于成功判定）
    before = len(re.findall(r'id="pid(\d+)"', page))
    final_url, text = http(BASE + "/post.php?", data=gbk_form(fields), referer=read_url)
    print(f"[2] 提交后落地: {final_url}")
    # 成功判定：PHPWind 成功返回 meta-refresh 跳回帖子页（requests 不跟随 meta-refresh）
    meta_ok = re.search(r'http-equiv="refresh"[^>]*url=read\.php\?tid=%s' % tid, text)
    landed = f"tid={tid}" in final_url
    if meta_ok:
        print(f"✅ 回帖成功（服务器返回跳转 read.php?tid={tid} 的成功页）")
    elif landed:
        after = len(re.findall(r'id="pid(\d+)"', text))
        print(f"✅ 回帖应已成功（落地帖子页；楼层 {before} → {after}）")
    else:
        print("⚠️ 回帖结果不确定，响应前 500 字符:")
        print(text[:500])


def check(tid, sf=""):
    """查看帖子回复情况：楼层数 + 最后回复（作者/时间/摘要）。"""
    url = f"{BASE}/read.php?tid={tid}" + (f"&sf={sf}" if sf else "")
    final_url, text = http(url, referer=BASE + "/index.php")
    if "无安全验证" in text or "链接不完整" in text:
        print(f"❌ sf 无效或缺失（当前 sf={sf!r}），请从帖子链接里补 --sf")
        sys.exit(1)
    # 楼主 pidtpc + 回复 pidN
    pids = re.findall(r'id="pid(\d+)"', text)
    title_m = re.search(r"<title>([\s\S]*?)</title>", text)
    title = title_m.group(1).strip() if title_m else "?"
    # 去掉 "|版块名 - 绯月ScarletMoon" 后缀，保留帖子标题
    title = re.split(r"\s*\|\s*", title)[0].strip()
    title = re.sub(r"\s*-\s*绯月.*$", "", title).strip()
    print(f"帖子: {title or '?'}")
    print(f"回复楼层数: {len(pids)}（不含楼主）")
    if not pids:
        print("（暂无回复）")
        return
    # 每层正文：按 pid 锚点切块，正文取「菜单</a>」之后到「</table>」（网站api资料.md §3.2）
    pid_positions = [(m.group(1), m.start()) for m in re.finditer(r'id="pid(\d+)"', text)]
    floors = []
    for i, (pid, start) in enumerate(pid_positions):
        end = pid_positions[i + 1][1] if i + 1 < len(pid_positions) else min(start + 8000, len(text))
        chunk = text[start:end]
        cm = re.search(r'菜单</a>([\s\S]*?)</table>', chunk)
        body = cm.group(1) if cm else chunk
        plain = re.sub(r"<[^>]+>", " ", body)
        plain = re.sub(r"&nbsp;", " ", plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        floors.append((pid, plain))
    for pid, plain in floors[-3:]:
        print(f"  #{pid}: {plain[:200]}")
    # 最后一页可能不是最新回复，提示翻页
    page_m = re.findall(r"page=(\d+)", text)
    if page_m:
        print(f"  （帖子有多页，最大页码见 page={max(int(p) for p in page_m)}；此处仅第 1 页）")


def main():
    ap = argparse.ArgumentParser(description="绯月论坛发帖/回帖/查回复")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="发新帖")
    p_new.add_argument("--title", default=None, help="标题（--title 或 --title-file 二选一）")
    p_new.add_argument("--title-file", default=None, help="标题从文件第一行读（避免命令行特殊字符坑）")
    p_new.add_argument("--content", default=None)
    p_new.add_argument("--content-file", default=None)
    p_new.add_argument("--fid", type=int, default=5, help="版块 id（默认 5 自由讨论区）")

    p_reply = sub.add_parser("reply", help="回帖")
    p_reply.add_argument("--tid", required=True)
    p_reply.add_argument("--content", default=None)
    p_reply.add_argument("--content-file", default=None)
    p_reply.add_argument("--fid", type=int, default=None, help="缺省自动从帖子页提取")
    p_reply.add_argument("--sf", default="")
    p_reply.add_argument("--floor", type=int, default=None, help="回复指定楼层：0=楼主 / 1=第1个回复 / 2=第2个回复...（默认不指定，普通回帖）")
    p_reply.add_argument("--floors", default=None, help="一次回复多个楼层：逗号分隔，如 '8,9'（正文拼多个 [quote] 前缀 + 关键词多作者，2026-08-16 实测）")

    p_check = sub.add_parser("check", help="查回复")
    p_check.add_argument("--tid", required=True)
    p_check.add_argument("--sf", default="")

    args = ap.parse_args()
    if args.cmd == "new":
        content = args.content
        if args.content_file:
            with open(args.content_file, encoding="utf-8") as f:
                content = f.read()
        if not content:
            print("[错误] 需要 --content 或 --content-file")
            sys.exit(1)
        # 标题优先级：--title-file（文件首行） > --title
        title = args.title
        if args.title_file:
            with open(args.title_file, encoding="utf-8") as f:
                title = f.read().strip().split("\n")[0].strip()
        if not title:
            print("[错误] 需要 --title 或 --title-file（标题不能为空）")
            sys.exit(1)
        new_thread(title, content, args.fid)
    elif args.cmd == "reply":
        content = args.content
        if args.content_file:
            with open(args.content_file, encoding="utf-8") as f:
                content = f.read()
        if not content:
            print("[错误] 需要 --content 或 --content-file")
            sys.exit(1)
        reply(args.tid, content, args.fid, args.sf, floor=args.floor, floors=args.floors)
    elif args.cmd == "check":
        check(args.tid, args.sf)


if __name__ == "__main__":
    main()
