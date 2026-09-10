# -*- coding: utf-8 -*-
"""HTTP 层 + 日志 + 基础读写（2026-09-10 拆分自主文件）。"""
import os
import re
import sys
import time
import random
import datetime
import hashlib

try:
    import requests
except ImportError:
    print("[错误] 缺少 requests 库，请先安装: pip install requests")
    sys.exit(1)

from ggzlib import state

# ===== 日志路径脱敏（2026-08-24）=====
# Python traceback 会把脚本绝对路径写入日志，暴露本机目录结构。日志会上传/入库
# → 统一替换：项目 tools/ 绝对路径 → "."（保留相对可读性）、用户主目录 → "~"
PROJECT_ROOT = state.GGZ_DIR  # <root>/tools（与拆分前 ggz_daily.py 的 dirname/.. 一致）
USER_HOME = os.path.expanduser("~")

# ===== 日志脱敏（2026-08-23）=====
# logs/ 会被上传/入库，用户名与 safeid 是敏感字段。
# 策略：终端显示明文（本地方便），日志文件写 MD5 脱敏值（可上传）。
# 由 Tee 在写日志流前统一替换，全局生效，无需改各 print 处。
SECRETS = {}  # {明文: MD5前8位}，main 里 get_user_and_safeid 后填充


def mask_secret(s, length=8):
    """MD5 脱敏：取前 length 位 hex。保证唯一性（同一值每次结果一致）且不可逆。"""
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:length] if s else s


def add_secret(value):
    """登记一个敏感字段，后续写日志自动脱敏。返回脱敏值。"""
    if value and value not in SECRETS:
        SECRETS[value] = mask_secret(value)
    return SECRETS.get(value, value)


class Tee:
    """同时输出到多个流（终端 + 日志文件），保证执行过程完整留档。

    2026-08-17：PowerShell 偶发抽风会吞掉/截断脚本输出，加日志文件兜底。
    2026-08-23：写日志流前对 SECRETS 里的明文做 MD5 脱敏。
    2026-08-24：日志流额外做路径脱敏（PROJECT_ROOT→"."、USER_HOME→"~"）。
    2026-09-05：write 后立即 flush 文件流（跑一点写一点，避免块缓冲）。
    ⚠️ 被本库 import 的模块不得无条件 sys.stdout.reconfigure——stdout 此时
    可能已是 Tee（battle_sim 的教训，见其顶部注释）。
    """

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                if s not in (sys.__stdout__, sys.__stderr__):
                    for plain, masked in SECRETS.items():
                        data = data.replace(plain, masked)
                    # 先替换更具体的项目根（→"."），再兜底用户主目录（→"~"）
                    data = data.replace(PROJECT_ROOT, ".")
                    data = data.replace(USER_HOME, "~")
                s.write(data)
                # 仅文件流立即落盘（终端行缓冲自带，无需手动 flush；2026-09-05）
                if s not in (sys.__stdout__, sys.__stderr__):
                    s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def setup_logging():
    """重定向 stdout/stderr 到 终端 + logs/ggz_YYYYMMDD.log（追加）。
    必须在任何输出前调用。
    """
    log_dir = os.path.join(state.GGZ_DIR, "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "ggz_%s.log" % datetime.date.today().strftime("%Y%m%d"))
    # buffering=1 行缓冲 + Tee.write 内 flush → 跑一点写一点（2026-09-05）
    log_fp = open(log_path, "a", encoding="utf-8", buffering=1)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_fp.write("\n" + "=" * 60 + "\n" + f"[{ts}] 新会话开始\n" + "=" * 60 + "\n")
    log_fp.flush()
    sys.stdout = Tee(sys.__stdout__, log_fp)
    sys.stderr = Tee(sys.__stderr__, log_fp)
    print(f"[日志] 已写入 {os.path.relpath(log_path, state.GGZ_DIR + os.sep + os.pardir)}")


# requests.Session：连接池 + keep-alive 复用连接，规避 urllib 每次新建 TLS
# 握手被服务器限流（SSL 断开/返回空）的问题（2026-08-16 实测，05 文档 §4.5）
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Referer": state.BASE + "/fyg_index.php",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
})


def request(url, data=None, retries=3, xhr=True):
    """HTTP 请求，带重试（requests.Session 连接复用）。

    xhr=True 时带 X-Requested-With: XMLHttpRequest 头——服务器对带此头的请求
    返回 JS 动态加载的内容（如装备页道具栏），静态请求拿不到（2026-08-13 实测）。
    返回 bytes（dec() 解码）。
    """
    # 写操作（POST）前随机 sleep 0.3~1s 打散请求节奏（2026-08-19 学自
    # guguzhen-slack），降低连续请求触发限流概率。
    if data is not None:
        time.sleep(random.uniform(0.3, 1.0))
    last_err = None
    for attempt in range(retries):
        try:
            headers = {"Cookie": state.COOKIE}
            if xhr:
                headers["X-Requested-With"] = "XMLHttpRequest"
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            resp = _SESSION.post(url, data=data, headers=headers, timeout=60) \
                if data is not None else _SESSION.get(url, headers=headers, timeout=60)
            # ⚠️ cookie 失效检测（2026-08-18）：咕咕镇单会话，浏览器重新打开游戏页
            # 会把脚本会话顶掉，此时任何重试都无意义。
            # 2026-08-24：改为自动刷新 cookie（smart_refresh_ggz）后重试一次。
            if "重新登录".encode("utf-8") in resp.content:
                if state.refresh_cookie_auto():
                    continue  # 刷新成功，重试本次请求（用新 COOKIE）
                raise state.AuthExpiredError(
                    "咕咕镇 cookie 已失效（浏览器登录顶掉/每日刷新），"
                    "自动刷新失败，请手动运行 tools/get_cookies.py --game")
            return resp.content
        except state.AuthExpiredError:
            raise
        except Exception as e:
            last_err = e
            # 指数退避 + 随机 jitter，避免重试同步触发限流（05 文档 §4.5）
            wait = (2 ** attempt) + random.uniform(0, 0.5)
            print(f"  ⚠️ 请求重试 {attempt + 1}/{retries}: {e}（{wait:.1f}s 后重试）")
            time.sleep(wait)
    raise last_err


def dec(raw):
    return raw.decode("utf-8", errors="replace")


def read_block(f, **params):
    params["f"] = f
    return dec(request(state.BASE + "/fyg_read.php", params))


def click(c, **params):
    params["c"] = c
    params["safeid"] = state.SAFEID
    return dec(request(state.BASE + "/fyg_click.php", params))


def strip_tags(html_text):
    html_text = re.sub(r"<script.*?</script>", "", html_text, flags=re.S)
    html_text = re.sub(r"<style.*?</style>", "", html_text, flags=re.S)
    html_text = re.sub(r"<[^>]+>", " ", html_text)
    html_text = re.sub(r"\s+", " ", html_text)
    return html_text.strip()


def show(title, text, maxlen=500):
    print(f"\n[{title}] {len(text)} 字符")
    print(text[:maxlen] if len(text) > maxlen else text)


def get_user_and_safeid():
    """主页提取: 用户名 + safeid（全动态，不写死）"""
    text = dec(request(state.BASE + "/fyg_index.php"))
    # 顶部导航: onclick="window.location.href='fyg_index.php'">用户名</button>
    m = re.search(r"fyg_index\.php'\"?>([^<]+)</button>", text)
    user = m.group(1).strip() if m else None
    m2 = re.search(r"&safeid=([^\"']+)", text)
    safeid = m2.group(1) if m2 else None
    return user, safeid


def get_active_zid():
    """f=8 角色卡列表: 找出战中的角色 zid（含 '(出战中)' 标记）"""
    t = read_block(8)
    # 按卡块解析: xxcard(zid) ... (出战中)（2026-09-05 修复: 旧贪婪正则会匹配到第一张卡）
    m = re.search(r"xxcard\((\d+)\)[^>]*>(?:(?!xxcard).)*?\((出战中)\)", t, re.S)
    return int(m.group(1)) if m else None


def ensure_session(refresh_zid=False):
    """确保 state.USER/SAFEID/ZID 就绪（外部模块统一入口，替代各自写全局）。

    USER/SAFEID 缺失才取（主页请求有成本）；ZID 缺失或 refresh_zid=True 才取。
    返回 (user, safeid, zid)。
    """
    if not state.USER or not state.SAFEID:
        state.USER, state.SAFEID = get_user_and_safeid()
    if state.ZID is None or refresh_zid:
        state.ZID = get_active_zid()
    return state.USER, state.SAFEID, state.ZID
