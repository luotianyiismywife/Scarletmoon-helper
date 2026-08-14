# -*- coding: utf-8 -*-
"""批量抓取帖子正文并保存到 docs/<资料目录>/raw/。

用法:
    python tools/fetch_posts.py            # 抓取 咕咕镇-新争夺资料/06-论坛帖子索引.md 中所有 ⬜ 未读帖子
    python tools/fetch_posts.py --limit 5  # 只抓前 5 篇（调试用）
    python tools/fetch_posts.py --tid 1052153   # 只抓指定 tid
    python tools/fetch_posts.py --dir 旧争夺资料 --index 03-论坛帖子索引.md   # 抓旧争夺
    python tools/fetch_posts.py --fid 5 --max-pages 3   # 板块扫描模式（kf-analysis 参考实现）

付费帖处理（2026-08-12）:
    - 检测 "此帖售价 N KFB" 特征
    - 售价 ≤10 KFB → 自动购买（job.php?action=buytopic）后重抓正文
    - 售价 >10 KFB → 跳过并提示（SKIP-PAID）

依赖:
    cookie.txt（由 tools/get_cookies.py 生成, 已 gitignore）
"""
import argparse
import html as htmllib
import json
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = "咕咕镇-新争夺资料"
DEFAULT_INDEX = "06-论坛帖子索引.md"
RAW_DIR = os.path.join(ROOT, "docs", DEFAULT_DIR, "raw")
MD_PATH = os.path.join(ROOT, "docs", DEFAULT_DIR, DEFAULT_INDEX)
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


# 发表时间提取(与 fetch_publish_time.py 相同规则): 抓正文时顺手记录, 省一轮请求
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


def load_pub_times():
    """载入 docs/<dir>/publish_time.json (断点续传用), 不存在返回 {}。"""
    p = os.path.join(os.path.dirname(RAW_DIR), "publish_time.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8")), p
    return {}, p


def save_pub_times(times, path):
    json.dump(times, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


PAID_RE = re.compile(r"此帖售价 (\d+(?:\.\d+)?) KFB,已有 (\d+) 人购买")
MAX_BUY_KFB = 10  # ≤10 KFB 的付费帖自动购买（用户规则 2026-08-12）


def check_paid(html, tid):
    """检测付费帖: 返回 (是否付费, 价格KFB, 购买URL或None)。
    判定依据（2026-08-12 实测）:
    - read.php 响应始终含 '此帖售价 N KFB' fieldset（无论是否已购买, 由 JS 控制显示）
    - **未购买** → 楼主正文(pidtpc)被替换, 不含实质内容
    - **已购买** → 楼主正文完整可见（正文里没有 fieldset 的替换, 纯文本存在）
    - 关键: 用 'pidtpc' 楼主区块是否含实质文本判断是否已购买
    返回: (是否付费, 价格, 需购买时的URL); 已购买或非付费 → buy_url=None
    """
    m = PAID_RE.search(html)
    if not m:
        return False, 0, None
    price = float(m.group(1))

    # 提取楼主正文区(pidtpc): 取 readtext 块内、菜单之后、table 之前
    lm = re.search(r'<div class="readtext"[^>]*id="pidtpc"[^>]*>(.*?)(?=<div class="readtext"|$)', html, re.S)
    tpc_text = ""
    if lm:
        seg = lm.group(1)
        cm = re.search(r'class="readcza">菜单</a>\s*(.*?)(?=</table>)', seg, re.S)
        tpc_text = strip_tags(cm.group(1)) if cm else ""
    tpc_text = tpc_text.strip()

    # 未购买: 楼主区无实质内容（只有购买提示/空）
    if len(tpc_text) < 20:
        btn = re.search(r"action=buytopic&tid=(\d+)&pid=(\w+)&verify=([0-9a-f]+)", html)
        if btn:
            buy_url = f"job.php?action=buytopic&tid={btn.group(1)}&pid={btn.group(2)}&verify={btn.group(3)}"
            return True, price, buy_url
        return True, price, None  # 无按钮(异常), 无法购买

    # 已购买: 正文可见
    return True, price, None


def buy_paid(session, buy_url, tid):
    """执行购买, 返回 (是否成功, 响应文本首段)。"""
    url = "https://bbs.kfpromax.com/" + buy_url
    r = session.get(url, timeout=25)
    r.encoding = "gbk"
    return r.status_code == 200, strip_tags(r.text)[:120]


# ---------- 图片提取 ----------
# PHPWind 的 <img> onclick 含 this.width>800, ">" 会破坏标签结构, 需多模式捕获
IMG_SRC_RE = re.compile(r'<img[^>]*?\bsrc\s*=\s*["\']?([^"\'\s>]+)', re.I)
WINOPEN_RE = re.compile(r"window\.open\(\s*['\"]([^'\"]+)['\"]")
IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|bmp)(?:[?#]|$)", re.I)
SMILIES_RE = re.compile(r"(?:post/smile|images/post/smile)/", re.I)


def resolve_img_url(u):
    """相对路径补全为绝对 URL; 无效返回 None。"""
    u = u.strip()
    if not u or u.startswith("data:"):
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http"):
        return u
    if u.startswith("/"):
        return BASE + u
    return BASE + "/" + u


def extract_images(html):
    """提取帖子页所有图片 URL(去重保序, 排除表情图)。"""
    urls = []
    seen = set()

    def add(u):
        full = resolve_img_url(u)
        if full and full not in seen and not SMILIES_RE.search(full):
            seen.add(full)
            urls.append(full)

    for m in IMG_SRC_RE.finditer(html):
        add(m.group(1))
    for m in WINOPEN_RE.finditer(html):
        u = m.group(1)
        if IMG_EXT_RE.search(u) or "/Mon_" in u or "attachment" in u:
            add(u)
    return urls


def download_images(session, urls, img_dir, tid):
    """下载图片到 img_dir, 返回 (成功数, 失败数)。已存在则跳过。"""
    os.makedirs(img_dir, exist_ok=True)
    ok = fail = 0
    for i, u in enumerate(urls, 1):
        ext_m = re.search(r"\.(jpe?g|png|gif|webp|bmp)(?:[?#]|$)", u, re.I)
        ext = "." + ext_m.group(1).lower() if ext_m else ".img"
        out = os.path.join(img_dir, f"{i:02d}{ext}")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            ok += 1
            continue
        try:
            r = session.get(u, timeout=20)
            if r.status_code == 200 and len(r.content) > 100:
                with open(out, "wb") as f:
                    f.write(r.content)
                ok += 1
            else:
                fail += 1
                print(f"    [IMG-FAIL] {tid} #{i} HTTP {r.status_code} {u[:80]}")
        except Exception as e:
            fail += 1
            print(f"    [IMG-FAIL] {tid} #{i} {type(e).__name__} {u[:80]}")
        time.sleep(0.3)
    return ok, fail


def parse_post(html):
    """解析帖子页: 返回 {title, floors: [{id, author, date, text}], images: [url]}"""
    m = re.search(r"<title>(.*?)</title>", html)
    title = m.group(1).strip() if m else "?"
    title = re.sub(r"\|.*?- 绯月ScarletMoon$", "", title).strip()

    floors = []
    images = []
    # 楼主
    m = re.search(r'<div class="readtext"[^>]*id="pidtpc"[^>]*>(.*?)(?=<div class="readtext"|$)', html, re.S)
    # 通用: 匹配所有 readtext 块
    blocks = list(re.finditer(r'<div class="readtext"[^>]*id="pid([^"]+)"[^>]*>(.*?)(?=<div class="readtext"|$)', html, re.S))
    if not blocks:
        return {"title": title, "floors": [], "images": []}
    seen_img = set()
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
        body_html = cm.group(1) if cm else seg
        text = strip_tags(body_html)
        if not text:
            text = strip_tags(seg)[-500:]
        # 图片: 仅从楼层正文块提取
        for u in extract_images(body_html):
            if u not in seen_img:
                seen_img.add(u)
                images.append(u)
        floors.append({"id": pid, "author": author, "date": date, "text": text})
    return {"title": title, "floors": floors, "images": images}


def unread_posts():
    """从 06-论坛帖子索引.md 表格解析未读帖子。"""
    md = open(MD_PATH, encoding="utf-8").read()
    rows = re.findall(
        r"^\| ([\d-]+) \| (.+?) \| (https://bbs\.kfpromax\.com/read\.php\?tid=\d+[^|]*) \| (⬜) \|",
        md, re.M)
    return [(d, t, u) for d, t, u, _ in rows]  # (date, title, url)


def scan_board(session, fid, max_pages=10, disp=False):
    """板块扫描（扒自 kf-analysis 的 parse_board_page + get_oneboard_url）。

    GET thread.php?fid=<fid>&orderway=lastpost&page=N → 解析该页所有主题 (tid, sf, reply_count)。
    翻 max_pages 页 + 最后重抓第 1 页去重（防翻页期间帖子浮动导致遗漏）。
    返回 [(tid, sf, reply_count, title, url)]，按 tid 去重保序。

    用途：比全站搜索（30次/日限制）更全的板块级扫描；每日扫咕咕镇相关板块替代搜索。
    参考项目：github.com/kisaragizen/kf-analysis（README §CLI/包内函数）
    """
    def get_onepage(page):
        url = f"{BASE}/thread.php?fid={fid}&orderway=lastpost&page={page}"
        html = fetch_html(session, url)
        rows = []
        for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            seg = tr.group(1)
            tit = re.search(r'class="threadtit1"', seg)
            if not tit:
                continue
            # 标题: threadtit1 div 内第一个 <a title="...">（title 属性即完整标题）
            tm = re.search(r'class="threadtit1"[\s\S]*?<a href="read\.php\?tid=(\d+)&sf=([0-9a-f]+)" title="([^"]*)"', seg)
            if not tm:
                continue
            tid, sf, title = tm.group(1), tm.group(2), tm.group(3).strip()
            # 回复数: b_tit6 的 li 内第一个数字（后跟浏览数）
            b6 = re.search(r'class="b_tit6"[\s\S]*?<li>[\s\S]*?(\d+)<br', seg)
            reply_count = int(b6.group(1)) if b6 else 0
            rows.append((int(tid), sf, reply_count, title[:80]))
        if disp:
            print(f"  板块 {fid} 第 {page} 页: {len(rows)} 条")
        return rows

    result = []
    for page in range(1, max_pages + 1):
        try:
            result += get_onepage(page)
        except Exception as e:
            print(f"  ⚠️ 板块 {fid} 第 {page} 页失败: {e}")
        time.sleep(0.8)
    # 翻页结束后重抓第 1 页（防浮动遗漏）→ 插入最前去重
    try:
        result = get_onepage(1) + result
    except Exception:
        pass
    seen, out = {}, []
    for x in result:
        if x[0] not in seen:
            seen[x[0]] = x
            out.append(x)
    return [(tid, sf, rc, title, f"{BASE}/read.php?tid={tid}&sf={sf}") for tid, sf, rc, title in out]


def main():
    global RAW_DIR, MD_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tid", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.8, help="请求间隔秒")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="资料目录名(docs/下)")
    ap.add_argument("--index", default=DEFAULT_INDEX, help="索引文件名")
    ap.add_argument("--images", action="store_true",
                    help="提取图片URL写入正文文件, 并下载到 raw/img/<tid>/ (已抓过的帖也会回填)")
    ap.add_argument("--fid", type=int, default=0,
                    help="板块扫描模式：扫 thread.php?fid=<fid> 全部分页主题并打印（替代搜索，无30次/日限制）")
    ap.add_argument("--max-pages", type=int, default=10, help="板块扫描最大页数（默认10）")
    args = ap.parse_args()
    RAW_DIR = os.path.join(ROOT, "docs", args.dir, "raw")
    MD_PATH = os.path.join(ROOT, "docs", args.dir, args.index)

    # 板块扫描模式（kf-analysis 参考实现）
    if args.fid:
        session = make_session()
        rows = scan_board(session, args.fid, max_pages=args.max_pages, disp=True)
        print(f"\n板块 {args.fid} 共 {len(rows)} 个主题（去重）:")
        for tid, sf, rc, title, url in rows:
            print(f"  {tid} [{rc:>4}回] {title}  {url}")
        return

    posts = unread_posts()
    if args.tid:
        posts = [p for p in posts if str(args.tid) in p[2]]
    if args.limit:
        posts = posts[: args.limit]

    print(f"待抓取: {len(posts)} 篇")
    os.makedirs(RAW_DIR, exist_ok=True)

    session = make_session()
    pub_times, pub_path = load_pub_times()
    ok = fail = img_total = 0
    for date, title, url in posts:
        tid = re.search(r"tid=(\d+)", url).group(1)
        out_path = os.path.join(RAW_DIR, f"{tid}.txt")
        if os.path.exists(out_path):
            # 已抓过: 若开启 --images 且尚未记录图片, 回填图片信息
            existed = open(out_path, encoding="utf-8").read()
            if args.images and "\nImages:\n" not in existed:
                try:
                    html = fetch_html(session, url)
                    imgs = parse_post(html)["images"]
                    with open(out_path, "a", encoding="utf-8") as f:
                        f.write("\nImages:\n")
                        for i, u in enumerate(imgs, 1):
                            f.write(f"  [{i}] {u}\n")
                    if imgs:
                        img_dir = os.path.join(RAW_DIR, "img", tid)
                        iok, ifail = download_images(session, imgs, img_dir, tid)
                        img_total += iok
                        print(f"  [回填] {tid} 图片 {iok} 张 (失败 {ifail})")
                    time.sleep(args.delay)
                except Exception as e:
                    print(f"  [回填FAIL] {tid}: {e}")
            else:
                print(f"  [跳过] {tid} 已存在")
            ok += 1
            continue
        try:
            html = fetch_html(session, url)
            # 顺手记录真实发表时间(省一轮 fetch_publish_time)
            pt = extract_publish_time(html)
            if pt:
                pub_times[tid] = pt
            # 付费帖检测 + 自动购买
            is_paid, price, buy_url = check_paid(html, tid)
            if is_paid and buy_url:
                if price <= MAX_BUY_KFB:
                    ok_buy, resp = buy_paid(session, buy_url, tid)
                    if ok_buy:
                        print(f"  [BUY] {tid} 付费 {price} KFB 已购买, 重抓正文")
                        html = fetch_html(session, url)
                    else:
                        print(f"  [BUY-FAIL] {tid} 购买失败: {resp}")
                else:
                    print(f"  [SKIP-PAID] {tid} 售价 {price} KFB > {MAX_BUY_KFB}, 跳过")
                    fail += 1
                    continue
            elif is_paid:
                print(f"  [PAID-OK] {tid} 已购买过 ({price} KFB)")
            post = parse_post(html)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"标题: {post['title']}\n日期: {date}\nURL: {url}\n\n")
                for fl in post["floors"]:
                    f.write(f"--- 楼层[{fl['id']}] {fl['author']} {fl['date']} ---\n")
                    f.write(fl["text"] + "\n\n")
                if post["images"]:
                    f.write("\nImages:\n")
                    for i, u in enumerate(post["images"], 1):
                        f.write(f"  [{i}] {u}\n")
            if args.images and post["images"]:
                img_dir = os.path.join(RAW_DIR, "img", tid)
                iok, ifail = download_images(session, post["images"], img_dir, tid)
                img_total += iok
            n = len(post["floors"])
            first = post["floors"][0]["text"][:60].replace("\n", " ") if post["floors"] else ""
            print(f"  [OK] {tid} 楼层={n} 楼主: {first}")
            ok += 1
            if ok % 20 == 0:
                save_pub_times(pub_times, pub_path)
        except Exception as e:
            print(f"  [FAIL] {tid} {title}: {e}")
            fail += 1
        time.sleep(args.delay)

    save_pub_times(pub_times, pub_path)
    print(f"\n完成: 成功 {ok}, 失败 {fail}, 输出目录: {RAW_DIR}")
    if args.images:
        print(f"图片下载: {img_total} 张 → {os.path.join(RAW_DIR, 'img')}")
    print(f"发表时间已记录 {len(pub_times)} 条 → {pub_path}")


if __name__ == "__main__":
    main()
