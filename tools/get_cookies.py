# -*- coding: utf-8 -*-
"""从 Firefox Nightly 配置文件的 cookies.sqlite 中提取论坛 + 咕咕镇游戏 Cookie。

用法:
    python tools/get_cookies.py                    # 提取全部（论坛 + 咕咕镇游戏）
    python tools/get_cookies.py --forum            # 仅论坛 bbs.kfpromax.com
    python tools/get_cookies.py --game             # 仅咕咕镇 www.momozhen.com
    python tools/get_cookies.py --login            # 账号密码登录论坛并刷新 cookie
    python tools/get_cookies.py --refreshggz       # 用现有论坛 cookie 走入口链刷新咕咕镇 cookie

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
    - --refreshggz 模式：读 cookie.txt 现有论坛 Cookie（2ed4e_*）走入口链刷新游戏
      Cookie，无需账号密码；论坛登录态仍有效即可。若论坛 Cookie 也失效，入口链
      不会下发有效 endtime（与当前时间几乎相同），会报错提示先重新登录。
    - ⚠️ UA 必须与浏览器当前版本一致：PHPWind 论坛会校验 UA，用旧版 rv:137.0
      请求会被强制登出（重定向 login.php）。本脚本动态读取 Firefox Nightly
      application.ini 的版本号生成 UA，升级浏览器后无需改代码。
"""
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from http.cookiejar import Cookie, CookieJar

PROFILE = "30hfbhjk.default-nightly"
OUTPUT = os.path.join(os.path.dirname(__file__), "cookie.txt")

FORUM_BASE = "https://bbs.kfpromax.com"
GAME_BASE = "https://www.momozhen.com"
FORUM_DOMAIN = "bbs.kfpromax.com"
GAME_DOMAIN = "www.momozhen.com"
UA_FALLBACK = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0"


def _current_ua():
    """动态读取 Firefox Nightly 实际版本生成 UA。

    2026-08-23 血泪教训：UA 写死 rv:137.0 导致 PHPWind 论坛强制登出
    （重定向 login.php），入口链刷新游戏 cookie 全部失败；浏览器实际
    发送 rv:156.0 就正常。PHPWind 会校验 UA 版本，必须与浏览器一致。

    注意：Firefox 发送的 UA 版本是"规范化"的——application.ini 里
    Version=156.0a1，但浏览器实际发 rv:156.0（去掉 a1/b1 等后缀）。
    """
    try:
        import configparser
        import re
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", ""), "Firefox Nightly", "application.ini"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Firefox Nightly", "application.ini"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Firefox Nightly", "application.ini"),
        ]
        for ini in candidates:
            if ini and os.path.exists(ini):
                cp = configparser.ConfigParser()
                cp.read(ini, encoding="utf-8")
                ver = cp.get("App", "Version", fallback="")
                ver = re.sub(r"[a-zA-Z].*$", "", ver)  # 156.0a1 → 156.0
                if ver:
                    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{ver}) "
                            f"Gecko/20100101 Firefox/{ver}")
    except Exception:
        pass
    return UA_FALLBACK


UA = _current_ua()

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


def load_existing():
    """读取现有 cookie.txt 的 (name, value) 列表（用于合并，避免 --game/--forum
    单独提取时把另一个域的清掉——2026-08-16 踩坑：--game 覆盖后论坛 2ed4e_* 丢失，
    forum_post.py 全被重定向 login.php）。"""
    if not os.path.exists(OUTPUT):
        return []
    raw = open(OUTPUT, encoding="utf-8").read()
    result = []
    for part in raw.split(";"):
        name, _, val = part.strip().partition("=")
        if name:
            result.append((name, val))
    return result


def _seed_jar(jar, cookies):
    """把 cookie.txt 的 (name, value) 按域预加载进 CookieJar。

    2026-08-23 修复：refresh_ggz 原实现在 http() 里每次都手动带
    `Cookie: cookie.txt` 头，urllib 中手动 Cookie 头优先级高于
    CookieJar，导致入口链 302 响应 Set-Cookie 的**新 fyg2019_***
    （会话刷新值）被旧值覆盖——fyg_index.php 用旧 cookie 请求被拒
    （"请重新登录并刷新！"）。预加载后由 jar 统一管理，重定向
    Set-Cookie 自动生效，与浏览器行为一致。
    """
    for name, value in cookies:
        domain = FORUM_DOMAIN if name.startswith("2ed4e_") else GAME_DOMAIN
        c = Cookie(version=0, name=name, value=value,
                   port=None, port_specified=False,
                   domain=domain, domain_specified=True, domain_initial_dot=False,
                   path="/", path_specified=True, secure=True, expires=None,
                   discard=True, comment=None, comment_url=None, rest={}, rfc2109=False)
        jar.set_cookie(c)


def refresh_ggz_via_forum():
    """用现有论坛 cookie（cookie.txt 的 2ed4e_*）走入口链刷新咕咕镇游戏 cookie。

    适用：论坛登录态有效但游戏会话失效时，无需重新输入账号密码，
    直接复用论坛 cookie 走入口链，让服务器刷新游戏会话并下发新
    fyg2019_* cookie。

    机制（2026-08-23 实测）：
      入口链 fyg_sjcdwj.php 302 → next.php 时，服务器 Set-Cookie
      新的 fyg2019_*（刷新会话）；随后 fyg_index.php 必须带这批
      新 cookie + 有效 PHPSESSID 才能登录成功。旧 cookie（cookie.txt
      里存的）会被拒（"请重新登录并刷新！"），endtime 本身≈当前
      时间并非有效/失效标志。

    返回新下发的 (name, value) cookie 列表（已合并写回 cookie.txt）。
    """
    existing = dict(load_existing())
    if not any(k.startswith("2ed4e_") for k in existing):
        print("[错误] cookie.txt 中没有论坛 cookie（2ed4e_*），请先 --login 或浏览器提取")
        sys.exit(1)

    jar = CookieJar()
    _seed_jar(jar, existing.items())
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def http(url, data=None, referer=None, retries=3):
        # ⚠️ 不手动带 Cookie 头：CookieJar 已预加载 + 自动吸收重定向 Set-Cookie
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

    # 1. 验证论坛登录态（PHPWind 已登录页通常含"退出"链接）
    url, body = http(FORUM_BASE + "/index.php")
    text = body.decode("gbk", errors="replace") if body else ""
    if not body or ("退出" not in text and "logout" not in text.lower()):
        print(f"[警告] 论坛登录态可能已失效（页面 {len(body or b'')} 字节，未检测到已登录标识）")
        print("        仍尝试走入口链，但大概率下发无效 endtime")
    else:
        print(f"[1] 论坛登录态有效（{len(body)} 字节）")

    # 2. 走入口链刷新游戏 cookie
    print("[2] 走入口链刷新咕咕镇 cookie ...")
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
    url3, body3 = http(GAME_BASE + "/fyg_index.php", referer=url2 or GAME_BASE + "/")
    logged = bool(body3) and ("个人信息".encode("utf-8") in body3)
    if body3:
        print(f"[3] 游戏主页: ({len(body3)} 字节) 刷新{'成功' if logged else '可能失败'}")

    # 3. 收集新下发的游戏 cookie（PHPSESSID 只留游戏域，cookie.txt 单值不分域，
    #    写回论坛域 PHPSESSID 会让后续游戏请求会话失效）
    new_cookies = [(c.name, c.value) for c in jar
                   if c.name.startswith("fyg2019_")
                   or (c.name == "PHPSESSID" and c.domain == GAME_DOMAIN)]
    print(f"[4] 新下发 {len(new_cookies)} 个: {[n for n, _ in new_cookies]}")

    # 4. 以 fyg_index.php 是否返回个人信息为成功标志（2026-08-23 实测：
    #    endtime 值≈当前时间并非失败标志，服务器会话状态在 PHPSESSID 里，
    #    端到端请求成功才是硬指标）
    if not logged:
        print("[错误] 游戏主页未返回个人信息（论坛会话可能已失效/入口链被拒），"
              "请先在浏览器登录论坛后重试 --login 或浏览器提取")
        sys.exit(1)
    et = dict(new_cookies).get("fyg2019_endtime")
    if et:
        try:
            et_i = int(et)
        except ValueError:
            et_i = 0
        print(f"[5] 新 endtime: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(et_i))}")
    else:
        print("[警告] 未拿到新 fyg2019_endtime")

    # 5. 合并写回
    merged = dict(existing)
    merged.update(new_cookies)
    out = "; ".join(f"{k}={v}" for k, v in merged.items())
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"[完成] 共更新 {len(new_cookies)} 个 Cookie（合并后共 {len(merged)} 个），"
          f"已写入 {os.path.normpath(OUTPUT)}")
    return new_cookies


def is_firefox_running():
    """检测 Firefox Nightly 是否在运行（Windows：查进程路径含 'Firefox Nightly'）。

    用于 smart_refresh_ggz 决定从 sqlite 提取还是走入口链刷新。
    """
    try:
        import subprocess
        # wmic 比 Get-Process 快且不依赖 PowerShell；按路径过滤只匹配 Nightly
        r = subprocess.run(
            ["wmic", "process", "where", "name='firefox.exe'", "get", "ExecutablePath"],
            capture_output=True, text=True, timeout=10)
        return "Firefox Nightly" in r.stdout
    except Exception:
        return False  # 检测失败按"未运行"处理，走入口链刷新（更稳妥）


def smart_refresh_ggz():
    """智能刷新咕咕镇 cookie（供 ggz_daily.py / warehouse_tidy.py 自动调用）。

    逻辑闭环（2026-08-24 用户设计）：
      1. Firefox Nightly 运行中 → 从 cookies.sqlite 提取游戏 cookie（--game 逻辑）
         （浏览器有最新登录态，直接读最准）
      2. Firefox Nightly 未运行 → 用 cookie.txt 现有论坛 cookie 走入口链刷新
         （--refreshggz 逻辑，不依赖浏览器）

    返回 True 表示刷新成功（cookie.txt 已更新），False 表示失败。
    调用方刷新成功后应重新 load_cookie() 并重试请求。
    """
    print("[cookie 失效] 自动刷新咕咕镇 cookie ...")
    if is_firefox_running():
        print("  → 检测到 Firefox Nightly 运行中，从 cookies.sqlite 提取")
        try:
            cookies = extract_from_firefox({"game"})
        except SystemExit:
            return False
    else:
        print("  → Firefox Nightly 未运行，走入口链刷新（--refreshggz 逻辑）")
        try:
            cookies = refresh_ggz_via_forum()
        except SystemExit:
            return False

    if not cookies:
        print("  ❌ 刷新失败：未获取到任何 cookie")
        return False

    # 合并写入 cookie.txt（extract_from_firefox / refresh_ggz_via_forum 只返回列表，
    # 写文件逻辑在 main() 里，smart_refresh_ggz 必须自己写，否则 cookie.txt 不更新
    # → 调用方 load_cookie() 读到的还是旧 cookie，白刷新）
    merged = dict(load_existing())
    merged.update(cookies)
    cookie_str = "; ".join(f"{name}={value}" for name, value in merged.items())
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(cookie_str)
    print(f"  ✅ 刷新成功，{len(cookies)} 个 cookie 已合并写入 cookie.txt（共 {len(merged)} 个）")
    return True


def main():
    if "--login" in sys.argv:
        cookies = login_and_refresh()
    elif "--refreshggz" in sys.argv:
        cookies = refresh_ggz_via_forum()
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

    # 合并写入：新提取的覆盖同名 cookie，未提取的域保留（防丢失）
    merged = dict(load_existing())
    merged.update(cookies)
    cookie_str = "; ".join(f"{name}={value}" for name, value in merged.items())
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(cookie_str)

    print(f"[完成] 共 {len(cookies)} 个 Cookie 更新（合并后共 {len(merged)} 个），已写入 {os.path.normpath(OUTPUT)}")


if __name__ == "__main__":
    main()
