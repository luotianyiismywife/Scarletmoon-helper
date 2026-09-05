# -*- coding: utf-8 -*-
"""咕咕镇日常执行脚本（按 docs/咕咕镇-新争夺资料/05-脚本开发.md §4A 流程）。

用法:
    python tools/ggz/ggz_daily.py stat          # 汇总状态（战场/工坊/翻牌）
    python tools/ggz/ggz_daily.py addpoint      # [0.5] 加点（自动分配剩余点）
    python tools/ggz/ggz_daily.py gem           # [1] 工坊收菜+开工（加工中→收工→自动重开）
    python tools/ggz/ggz_daily.py gemup         # [1.5] 提升宝石（B以下只升梦>红>银；比例低优先）
    python tools/ggz/ggz_daily.py halo          # [1.5b] 提升光环（读光环天赋石持有量→c=29）
    python tools/ggz/ggz_daily.py wish          # [3] 许愿池（按 WISH_MODE：combo 300w 十连送1=11次 / single 30w×N）
    python tools/ggz/ggz_daily.py beach         # [4] 沙滩收取+清理（4.5 规则；空且有箱→自动刷新；--no-refresh 禁用自动刷新不耗箱）
    python tools/ggz/ggz_daily.py refresh       # [4.5] 强制刷新沙滩（耗随机装备箱）
    python tools/ggz/ggz_daily.py smelt         # [4.5c] 熔炼仓库可熔炼装备为护身符（手动）
    python tools/ggz/ggz_daily.py pk [n]        # [5] 出击打野（默认 3 狗牌停；[--full] 打满 n 次）
    python tools/ggz/ggz_daily.py gift [--bonus1|--bonus2]  # [6] 翻牌（透视自动检测；--bonus1 耗1药水再领 / --bonus2 耗2药水重置再翻）
    python tools/ggz/ggz_daily.py bonus         # [7] 额外奖励（耗 1 体能刺激药水；手动）
    python tools/ggz/ggz_daily.py all [--bonus1|--bonus2]  # 一键日常（按序执行；--bonus 显式开启翻牌后药水操作）

日志: 每次执行同时输出到终端 + logs/ggz_YYYYMMDD.log（完整留档，
      终端输出被吞/截断时以日志文件为准）。

依赖: cookie.txt（tools/get_cookies.py --login 或提取生成）
"""
import os
import re
import sys
import time
import random
import datetime
import hashlib
import html
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

try:
    import requests
except ImportError:
    print("[错误] 缺少 requests 库，请先安装: pip install requests")
    sys.exit(1)

BASE = "https://www.momozhen.com"

# ===== 日志路径脱敏（2026-08-24）=====
# Python traceback 会把脚本绝对路径（如 C:\Users\xxx\...\ggz_daily.py）写入日志，
# 暴露本机目录结构。日志会上传/入库 → 统一替换：
#   项目根绝对路径 → "."（保留相对路径可读性）
#   用户主目录      → "~"（兜底，防 site-packages 等其它绝对路径泄漏）
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
USER_HOME = os.path.expanduser("~")


class AuthExpiredError(RuntimeError):
    """咕咕镇 cookie 失效（单会话被浏览器顶掉 / 每日刷新）。

    服务器对失效会话返回"请重新登录并刷新！"。此前脚本会把这 9 个字符
    当普通 HTML 解析 → 0 件装备/0 道具 → 静默误判"沙滩空"跳过（2026-08-18 事故）。
    恢复：浏览器走入口链刷新游戏 cookie 后重跑 tools/get_cookies.py --game。
    """


class Tee:
    """同时输出到多个流（终端 + 日志文件），保证执行过程完整留档。

    2026-08-17：PowerShell 偶发抽风会吞掉/截断脚本输出，
    加日志文件兜底（logs/ggz_YYYYMMDD.log）。
    2026-08-23：写日志流前对 SECRETS 里的明文做 MD5 脱敏（终端显示原文，
    日志文件可上传；logs/ 已取消 gitignore 入库）。
    2026-08-24：日志流额外做路径脱敏（PROJECT_ROOT→"."、USER_HOME→"~"），
    Python traceback 里的本机绝对路径不再出现在日志中。
    2026-09-05：write 后立即 flush 文件流（跑一点写一点，避免块缓冲
    导致日志一次性落盘；中途崩溃也能看到已跑部分）。
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
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "ggz_%s.log" % datetime.date.today().strftime("%Y%m%d"))
    # buffering=1 行缓冲 + Tee.write 内 flush → 跑一点写一点（2026-09-05）
    log_fp = open(log_path, "a", encoding="utf-8", buffering=1)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_fp.write("\n" + "=" * 60 + "\n" + f"[{ts}] 新会话开始\n" + "=" * 60 + "\n")
    log_fp.flush()
    sys.stdout = Tee(sys.__stdout__, log_fp)
    sys.stderr = Tee(sys.__stderr__, log_fp)
    print(f"[日志] 已写入 {os.path.relpath(log_path, os.path.dirname(os.path.abspath(__file__)) + os.sep + os.pardir)}")


def load_cookie():
    path = os.path.join(os.path.dirname(__file__), "..", "cookie.txt")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


COOKIE = load_cookie()
USER = None   # 动态: 主页提取
ZID = None    # 动态: f=8 出战中角色

# cookie 自动刷新状态（防 request() 多次触发刷新）
_cookie_refreshed = False


def refresh_cookie_auto():
    """cookie 失效时自动调用 get_cookies.smart_refresh_ggz() 刷新。

    逻辑闭环（2026-08-24 用户设计）：
      - Firefox Nightly 运行中 → 从 cookies.sqlite 提取（浏览器有最新登录态）
      - Firefox Nightly 未运行 → 走入口链刷新（--refreshggz，不依赖浏览器）
    成功后重新加载 COOKIE 全局变量。整个会话只刷新一次（防多请求重复触发）。
    返回 True=刷新成功，False=刷新失败/已刷新过。
    """
    global COOKIE, _cookie_refreshed
    if _cookie_refreshed:
        return False  # 本次会话已刷新过，不再重复
    _cookie_refreshed = True
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        import get_cookies
        print("\n⚠️ 检测到 cookie 失效，自动刷新中 ...")
        if get_cookies.smart_refresh_ggz():
            COOKIE = load_cookie()  # 重新加载刷新后的 cookie
            print("✅ cookie 已自动刷新，重试请求\n")
            return True
        else:
            print("❌ cookie 自动刷新失败，请手动运行: py tools/get_cookies.py --game")
            return False
    except Exception as e:
        print(f"❌ cookie 自动刷新异常: {e}")
        print("请手动运行: py tools/get_cookies.py --game")
        return False

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

# ===== 许愿池策略配置（2026-08-20）=====
# "combo"（默认）: 攒够 300 万贝壳 → c=18&id=10 十连（送 1 次 = 11 次）；<300 万不抽攒着
# "plan"        : 合理规划（每天限一次许愿操作）：
#                   ≥300 万 → 只做一次 10 连（11 次，最划算；600 万也只抽一次，剩的明天抽）
#                   <300 万 → 按剩余贝壳抽 1-9 次（270 万 = 9 次；每天一次机会不浪费）
WISH_MODE = "combo"

# ===== 工坊目标成功率配置（2026-08-28）=====
# 概率型道具（随机装备箱/灵魂药水/宝石原石）面板留档时额外输出
# "预计 X 分钟（折合小时分钟）到 N%"，N 即此配置，默认 100%。
# 用途：定时收工——按预计时长安排下次收菜（注意开工 8 小时内不可收工）。
GEM_TARGET_PCT = 100

# requests.Session：连接池 + keep-alive 复用连接，规避 urllib 每次新建 TLS
# 握手被服务器限流（SSL 断开/返回空）的问题（2026-08-16 实测，05 文档 §4.5）
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Referer": BASE + "/fyg_index.php",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
})


def request(url, data=None, retries=3, xhr=True):
    """HTTP 请求，带重试（requests.Session 连接复用）。

    xhr=True 时带 X-Requested-With: XMLHttpRequest 头——服务器对带此头的请求
    返回 JS 动态加载的内容（如装备页道具栏），静态请求拿不到（2026-08-13 实测）。
    返回 bytes（与旧 urllib 版本接口兼容，dec() 解码）。
    """
    # 写操作（POST）前随机 sleep 0.3~1s 打散请求节奏（2026-08-19 学自
    # guguzhen-slack：`await asyncio.sleep(random.random() * 1)`），
    # 降低连续请求触发限流概率（f=1 读沙滩 0 字符限流事故的缓解手段之一）。
    if data is not None:
        time.sleep(random.uniform(0.3, 1.0))
    last_err = None
    for attempt in range(retries):
        try:
            headers = {"Cookie": COOKIE}
            if xhr:
                headers["X-Requested-With"] = "XMLHttpRequest"
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            resp = _SESSION.post(url, data=data, headers=headers, timeout=60) \
                if data is not None else _SESSION.get(url, headers=headers, timeout=60)
            # ⚠️ cookie 失效检测（2026-08-18）：咕咕镇单会话，浏览器重新打开游戏页
            # 会把脚本会话顶掉，此时任何重试都无意义，直接报错提示重抓 cookie。
            # 2026-08-24：改为自动刷新 cookie（smart_refresh_ggz）后重试一次。
            if "重新登录".encode("utf-8") in resp.content:
                if refresh_cookie_auto():
                    continue  # 刷新成功，重试本次请求（用新 COOKIE）
                raise AuthExpiredError(
                    "咕咕镇 cookie 已失效（浏览器登录顶掉/每日刷新），"
                    "自动刷新失败，请手动运行 tools/get_cookies.py --game")
            return resp.content
        except AuthExpiredError:
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


def get_user_and_safeid():
    """主页提取: 用户名 + safeid（全动态，不写死）"""
    text = dec(request(BASE + "/fyg_index.php"))
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


# 角色 zid 映射（2026-09-05 浏览器实测：舞=3000、绮=3012；3011 空缺=雅占位，
# 未持有雅不显示；其余按 f=8 返回动态获取）
CARD_ZIDS = {
    "舞": 3000, "默": 3001, "琳": 3002, "艾": 3003, "梦": 3004, "薇": 3005,
    "伊": 3006, "冥": 3007, "命": 3008, "希": 3009, "霞": 3010, "绮": 3012,
}


def list_cards():
    """f=8 角色卡列表: 返回 {角色名: zid}（动态解析，不依赖写死的 CARD_ZIDS）"""
    t = read_block(8)
    cards = {}
    # 每张卡: onclick="xxcard(3000)" ... 卡名 ... (出战中)
    for m in re.finditer(r'xxcard\((\d+)\)[^>]*>(?:(?!xxcard).)*?<[^>]*>([^<]{1,4})</', t, re.S):
        zid, name = int(m.group(1)), m.group(2).strip()
        if name and not name.isdigit():
            cards[name] = zid
    return cards


def switch_card(zid=None, name=None):
    """[5.5] 切换出战角色（c=5 upcard）。传 zid 或角色名均可；不带参则列出所有角色。

    ⚠️ 切卡后出击/加点/装备都跟着切（07 文档 §4：角色卡等级/加点/装备独立），
    切卡前确认目标卡已加点且装备可打（2026-09-05：绮与舞属性点相同可试打野）。
    """
    global ZID
    if not zid and name:
        cards = list_cards()
        if name not in cards:
            print(f"❌ 角色 '{name}' 不存在，可用: {list(cards.keys())}")
            return None
        zid = cards[name]
    if not zid:
        cards = list_cards()
        print("可用角色卡:")
        for n, z in cards.items():
            mark = " (出战中)" if z == ZID else ""
            print(f"  {n} zid={z}{mark}")
        return None

    cur = get_active_zid()
    if cur == zid:
        name = name or [n for n, z in (list_cards() or {}).items() if z == zid]
        print(f"已是出战角色 {name}")
        return zid

    r = click(5, id=zid)
    msg = strip_tags(r)[:60]
    print(f"c=5 切卡 zid={zid}: {msg}")
    if "ok" in r or "装备成功" in r:
        ZID = zid
        print(f"✅ 出战角色已切换 zid={zid}")
        return zid
    print("⚠️ 切卡返回异常:", r[:120])
    return None


def read_block(f, **params):
    params["f"] = f
    return dec(request(BASE + "/fyg_read.php", params))


def click(c, **params):
    params["c"] = c
    params["safeid"] = SAFEID
    return dec(request(BASE + "/fyg_click.php", params))


def strip_tags(html):
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def show(title, text, maxlen=500):
    print(f"\n[{title}] {len(text)} 字符")
    print(text[:maxlen] if len(text) > maxlen else text)


# ---------- 步骤 ----------

# 各角色加点策略（2026-09-05，参考 07-角色属性表.md）
# 格式: {主属性: 60%上限优先, 副属性分配: [属性名, ...]（按顺序轮流 +1）}
# 所有策略都保证: 主属性堆到 60% 上限，剩余点按副属性列表轮流分配
#
# ⚠️ 2026-09-05 实测：六维加点**全角色共享**（给默加点→舞同步变化，f=18 验证），
# 当前实际只有一套点数。此表保留作"每角色独立加点"的未来准备：
# 若游戏改版支持独立加点，换角色时按对应策略分配即可。
ADDPOINT_STRATEGY = {
    # 主练：主力量敏捷，靠装备撑血量和技能率，点少量精神叠护盾
    "舞": {"main": "力量", "sub": ["敏捷", "体魄", "意志"]},
    # 剑盾反伤：全精默 / 高穿默（智精）
    "默": {"main": "精神", "sub": ["智力", "意志"]},
    # 当前不推荐（刃琳/剑盾琳）—— 通用力量流
    "琳": {"main": "力量", "sub": ["体魄", "意志"]},
    # 对剑全敏
    "命": {"main": "敏捷", "sub": ["力量", "意志"]},
    # 当前不推荐（星火宝石）—— 通用力量流
    "艾": {"main": "力量", "sub": ["体魄", "意志"]},
    # 打野 T0：主敏捷+精神（第三回合攻击次数≥3次）
    "梦": {"main": "敏捷", "sub": ["精神", "智力"]},
    # 打野 T1：对剑薇（智力至技能率+，余全敏）
    "薇": {"main": "敏捷", "sub": ["智力", "意志"]},
    # 打野 T1：力量 1100/敏 1501/物防100/魔防300
    "伊": {"main": "力量", "sub": ["敏捷", "意志"]},
    # PVP 强势/打野下水道：剑盾冥（力 600-700/敏200/智200/余意志）
    "冥": {"main": "意志", "sub": ["力量", "敏捷", "智力"]},
    # 打野 T0：血系最强（血之狂暴）
    "希": {"main": "体魄", "sub": ["意志", "力量"]},
    # PVP 输出：1300 智/900 精/800 敏
    "霞": {"main": "智力", "sub": ["精神", "敏捷"]},
    # 新卡：沸血+神秘弓 / 高速打护盾
    "绮": {"main": "力量", "sub": ["敏捷", "体魄"]},
}


def addpoint(zid=None, strategy=None, apply=False):
    """[0.5] 加点：读取 f=18 六维，按策略分配。

    2026-09-05 改版:
    - apply=True: 按目标角色策略**全量计算配置并直接提交**（覆盖当前，
      换角色时用——点数共享，直接提交目标配置即"切换加点"；只耗 1 次修改）
    - apply=False(默认): 只分配剩余点（日常加点）
    - c=14 重置接口已废弃（实测返回空、界面无按钮），不再使用
    """
    if zid is None:
        zid = ZID
    t = read_block(18, zid=zid)
    six = {}
    for key, name in [("sjll", "力量"), ("sjmj", "敏捷"), ("sjzl", "智力"),
                      ("sjtp", "体魄"), ("sjjs", "精神"), ("sjyz", "意志")]:
        m = re.search(r'id="%s" value="(\d+)"' % key, t)
        six[name] = int(m.group(1)) if m else 0
    m = re.search(r'id="zuida"[^>]*>(\d+)<', t)
    total = int(m.group(1)) if m else 0
    used = sum(six.values())
    remain = total - used
    print(f"总属性点 {total} | 已分配 {used} | 可分配 {remain}")
    print(f"当前六维: {six}")
    # ⚠️ 限流/页面异常防御（2026-09-05）: total=0 时 apply 模式会算出负数提交,
    # 非 apply 模式 remain=0 静默跳过也会误报"无需加点" → 统一显式拦截
    if total <= 0:
        print("❌ 总属性点解析失败（疑似限流/页面异常），跳过加点")
        return

    # 确定策略: 传入策略 > 当前角色名匹配 > 默认(力量60%+体意1:1)
    cards = list_cards()
    cur_name = [n for n, z in cards.items() if z == zid]
    role_name = cur_name[0] if cur_name else None
    if strategy is None and role_name and role_name in ADDPOINT_STRATEGY:
        strategy = ADDPOINT_STRATEGY[role_name]
    print(f"加点策略: {role_name or zid} → {strategy if strategy else '默认(力量60%+体意1:1)'}")

    if apply:
        # 全量模式: 按策略从头计算目标配置（不依赖当前已分配）
        plan = {"力量": 0, "敏捷": 0, "智力": 0, "体魄": 0, "精神": 0, "意志": 0}
        if strategy:
            cap = int(total * 0.6)
            main_attr = strategy["main"]
            plan[main_attr] = min(total, cap)
            left = total - plan[main_attr]
            subs = strategy.get("sub", [])
            i = 0
            while left > 0 and subs:
                plan[subs[i % len(subs)]] += 1
                left -= 1
                i += 1
            if left > 0:
                plan[main_attr] += left
        else:
            # 默认: 力量60% + 体意1:1
            cap = int(total * 0.6)
            plan["力量"] = min(total, cap)
            left = total - plan["力量"]
            half = left // 2
            plan["体魄"] = half
            plan["意志"] = left - half
        # ⚠️ 服务器不接受 0 值（实测报"请输入正确的数字格式"）→ 0 改成 1,
        # 从主属性扣回（保证总和 = total）
        for k in plan:
            if plan[k] == 0:
                plan[k] = 1
        need_remove = sum(plan.values()) - total
        while need_remove > 0:
            plan[strategy["main"] if strategy else "力量"] -= 1
            need_remove -= 1
        # 目标配置与当前一致则跳过（省 1 次修改）
        if plan == six:
            print("目标配置与当前一致，无需改动")
            return
        print(f"切换加点方案: {plan} (总计{sum(plan.values())})")
        r = click(2, id=zid,
                  add01=plan["力量"], add02=plan["敏捷"], add03=plan["智力"],
                  add04=plan["体魄"], add05=plan["精神"], add06=plan["意志"])
        show("c=2 加点返回", r)
        return

    if remain <= 0:
        print("无需加点")
        return

    plan = dict(six)
    if strategy:
        # 主属性堆到 60% 上限
        cap = int(total * 0.6)
        main_attr = strategy["main"]
        sub_attrs = strategy.get("sub", [])
        main_add = min(remain, cap - six[main_attr])
        plan[main_attr] = six[main_attr] + main_add
        left = remain - main_add
        # 副属性轮流 +1（直到分配完）
        i = 0
        while left > 0 and sub_attrs:
            attr = sub_attrs[i % len(sub_attrs)]
            plan[attr] += 1
            left -= 1
            i += 1
        # 还有剩 → 全给主属性（理论上不会发生, 60% 上限后剩余应能分完）
        if left > 0:
            plan[main_attr] += left
    else:
        # 默认: 力量堆到 60% 上限，剩余体/意 1:1
        cap = int(total * 0.6)
        force = min(six["力量"] + remain, cap)
        plan["力量"] = force
        left = remain - (force - six["力量"])
        half = left // 2
        plan["体魄"] = six["体魄"] + half
        plan["意志"] = six["意志"] + (left - half)

    print(f"加点方案: {plan}")
    r = click(2, id=zid,
              add01=plan["力量"], add02=plan["敏捷"], add03=plan["智力"],
              add04=plan["体魄"], add05=plan["精神"], add06=plan["意志"])
    show("c=2 加点返回", r)


GEM_PANEL_COLS = [
    # (栏名, 宝石名) —— f=21 六栏固定顺序（04 §4.3：1贝壳红石/2装备箱银石/
    # 3灵魂药水金石/4宝石原石梦石/5星沙虚石/6幻影经验幻石）
    ("贝壳", "红石"), ("随机装备箱", "银石"), ("灵魂药水", "金石"),
    ("宝石原石", "梦石"), ("星沙", "虚石"), ("幻影经验", "幻石"),
]


def parse_gem_panel(t):
    """解析 f=21 工坊面板：各栏 加工角色等级/角色名/宝石数/每分钟效率。

    每栏 alert div 结构（2026-08-27 浏览器实测）：
      已拾取<br>67040贝壳<br>Lv.800 伊 (赶海中...)<br>红石4<br>每分钟 +160贝壳
      20.112%概率出产<br>随机装备箱<br>Lv.800 命 (组装中...)<br>银石4<br>每分钟 +0.048%概率
      已开采<br>0星沙(0.3352)<br>Lv.190 默 (挖矿中...)<br>虚石0<br>每分钟 +0.0008星沙
    → 统一按 <br> 切 5 段：[当前值, 道具名, Lv.X 角色 (状态), 宝石N, 每分钟 +Y]
    返回 [(栏名, 宝石名, 等级, 角色, 宝石数, 效率文本, 当前值文本), ...]；解析失败返回 []。
    """
    rows = []
    blocks = re.findall(r'<div class="alert alert-info[^>]*>(.*?)</div>', t, re.S)
    for i, block in enumerate(blocks):
        if i >= len(GEM_PANEL_COLS):
            break
        col_name, gem_name = GEM_PANEL_COLS[i]
        parts = [p.strip() for p in re.split(r"<br\s*/?>", block) if p.strip()]
        if len(parts) < 5:
            continue
        m = re.search(r"Lv\.(\d+)\s*(\S+)", parts[2])
        g = re.search(gem_name + r"(\d+)", parts[3])
        rate = re.sub(r"<[^>]+>", "", parts[4]).strip()
        cur = re.sub(r"<[^>]+>", "", parts[0]).strip()
        if m and g:
            rows.append((col_name, gem_name, int(m.group(1)), m.group(2),
                         int(g.group(1)), rate, cur))
    return rows


def gem_eta_text(rate_text, cur_text):
    """概率型道具：线性外推到 GEM_TARGET_PCT 的预计时长（定时收工用）。

    rate_text 如"每分钟 +0.048%概率"（每分钟增速），cur_text 为面板第 1 段
    当前值（如"20.112%概率出产"）。概率线性增长 → 剩余分钟 = (目标-当前)/增速。
    返回如"预计 2083 分钟（34小时43分）到 100%"；非概率型/解析失败返回 ""。
    已达标返回 ""（无需等待）。
    """
    m = re.search(r"\+([\d.]+)%概率", rate_text)
    if not m:
        return ""
    per_min = float(m.group(1))
    c = re.search(r"([\d.]+)%概率", cur_text)
    cur = float(c.group(1)) if c else 0.0
    target = GEM_TARGET_PCT
    if cur >= target:
        return ""
    if per_min <= 0:
        return f"当前 {cur:g}%（增速 0，无法到 {target:g}%）"
    total = int(round((target - cur) / per_min))
    h, mi = divmod(total, 60)
    txt = f"预计 {total} 分钟（{h}小时{mi:02d}分）到 {target:g}%"
    if total < 480:
        txt += "（⚠️ 8 小时内不可收工）"
    return txt


def show_gem_panel(t, title="工坊面板"):
    """打印工坊面板摘要（各栏角色等级/宝石数/效率），供日志留档与收益核算。

    概率型三栏（随机装备箱/灵魂药水/宝石原石）额外输出到 GEM_TARGET_PCT
    （脚本顶部配置，默认 100%）的预计分钟数与折合小时分钟，用于定时收工安排。
    """
    rows = parse_gem_panel(t)
    if not rows:
        print(f"[{title}] 解析失败（未加工或格式变化）")
        return
    print(f"[{title}] 加工角色等级/宝石数/效率：")
    for col, gem_name, lv, char, gems, rate, cur in rows:
        line = f"  {col}: Lv.{lv} {char} | {gem_name}{gems} | {rate}"
        eta = gem_eta_text(rate, cur)
        if eta:
            line += f" | {eta}"
        print(line)


def gem():
    """[1] 工坊收菜：加工中 → 收工拿收益 → 重新开工；未加工 → 开工。

    c=30 为收工/开工切换（同按钮）。收工返回收益统计，实测收工后自动重新开工，
    但 8-12 出现过开工状态丢失（隔天变"开始加工"），故收工后检查、未自动开工则手动开工。
    2026-08-27：收工前记录各栏加工角色等级/宝石数/每分钟效率（开工随机换人，
    等级系数决定增长率，留档供收益核算——05 §4.4d 公式）。
    2026-08-28：概率型三栏额外输出到 GEM_TARGET_PCT（默认 100%）的预计时长
    （分钟/折合小时分钟），供定时收工安排。
    """
    t = read_block(21)
    if "收工" in t:
        show_gem_panel(t, "收工前")
        print("工坊加工中 → 收工...")
        r = click(30)
        if "8小时" in r:
            print("⏳ 开工不足 8 小时，还不能收工（保持加工）")
            return
        show("c=30 收工返回", r)
        t2 = read_block(21)
        if "开始加工" in t2:
            print("收工后未自动开工 → 手动开工...")
            r2 = click(30)
            show("c=30 开工返回", r2)
            show_gem_panel(read_block(21), "新开工")
        elif "收工" in t2:
            print("✅ 收工完成，工坊仍在加工中（异常）")
        else:
            print("✅ 收工完成，工坊已自动重新开工")
            show_gem_panel(t2, "新开工")
    elif "开始加工" in t:
        print("工坊未加工 → 开工...")
        r = click(30)
        show("c=30 开工返回", r)
        show_gem_panel(read_block(21), "新开工")
    else:
        print("⚠️ 未知工坊状态: " + strip_tags(t)[:200])


def wish():
    """[3] 许愿池：f=19 判断今日是否已许愿 + 主页贝壳 → 按 WISH_MODE 许愿。

    WISH_MODE 配置（脚本顶部，2026-08-20）：
      "combo"（默认）: 攒够 300 万 → c=18&id=10 十连（送 1 次 = 11 次）；<300 万不抽攒着
      "plan"        : 合理规划（每天限一次许愿操作）：
                       ≥300 万 → 只做一次 10 连（11 次，最划算；600 万也只抽一次，剩的明天抽）
                       <300 万 → 按剩余贝壳抽 1-9 次（270 万 = 9 次）
    单次许愿 30 万贝壳；10 连 = 300 万送 1 次（11 次，游戏内明示，02 文档 §4.5）。
    """
    t = read_block(19)
    fields = t.strip().split("#")
    if len(fields) >= 3 and fields[2] != "0":
        print(f"今日已许愿 {fields[2]} 次，跳过")
        return
    # 主页贝壳
    home = dec(request(BASE + "/fyg_index.php"))
    m = re.search(r"贝壳[^>]*>\s*(\d+)", home)
    coins = int(m.group(1)) if m else 0
    print(f"贝壳: {coins}")

    n = coins // 300000  # 按 30 万/次最多能抽的次数
    if WISH_MODE == "plan":
        # 合理规划：能 10 连就 10 连（最划算，每天限一次操作）；不能就按剩余抽 1-9 次
        if n >= 10:
            print(f"[plan] 贝壳≥300w，10 连许愿（送 1 = 11 次，花 300 万；剩 {coins - 3000000} 贝壳明天抽）")
            r = click(18, id=10)
        elif n >= 1:
            print(f"[plan] 贝壳不足 300w，抽 {n} 次（花 {n * 300000} 贝壳）")
            r = click(18, id=n)
        else:
            print("贝壳 < 30w，跳过许愿")
            return
    else:
        # combo（默认）：攒够 300 万才抽 10 连
        if n >= 10:
            print("[combo] 贝壳 ≥300w，10 连许愿（送 1 次 = 11 次）")
            r = click(18, id=10)
        else:
            print("贝壳 < 300w，跳过许愿（combo 模式攒够 300w 一次 10 连）")
            return

    if "已经许愿" in r or "请明天" in r:
        # 服务器权威判定已许愿（f=19 fields[2] 不可靠，2026-08-16 实测）
        print("今日已许愿（服务器确认），跳过")
    else:
        show("c=18 许愿返回", r)


def get_items():
    """读取道具栏持有量（f=7 武器装备区，2026-08-13 实测）。

    f=7 返回 HTML 顶部"我的仓库"含道具按钮：
      <button ... style="background-image:url(ys/icon/i/it005.gif);" ...>6</button>
    数量在按钮文本（如 "6"）或 title（如 title="宝石原石 x6"）。
    icon 文件名: it001药水/it002锻造箱/it003灵魂药水/it004随机装备箱/it005宝石原石/
                 it301蓝锻造石/it302绿锻造石/it310光环天赋石/it309苹果核
    返回 {道具id: 数量}，如 {'it005': 6, 'it004': 5, 'it310': 1}
    """
    html = read_block(7)
    result = {}
    for btn in re.findall(r"<button[^>]*>.*?</button>", html, re.S):
        m = re.search(r"ys/icon/i/(it\d+)\.gif", btn)
        if not m:
            continue
        item_id = m.group(1)
        # 数量：优先按钮文本（如 "6"），否则 title（如 title="宝石原石 x6"）
        n = re.search(r">\s*(\d+)\s*</button>", btn, re.S)
        if not n:
            n = re.search(r'title="[^"]*x(\d+)"', btn)
        result[item_id] = int(n.group(1)) if n else 0
    return result


def gemup():
    """[1.5] 提升宝石：读装备页道具栏宝石原石(it005)持有量 → c=27 提升。

    实测（2026-08-13）：宝石原石是道具（it005，装备页仓库顶部），每次消耗 1 颗。
    提升菜单 omenu(6) 显示各石拥有量：红石2/银石0/金石0/梦石0/虚石0/幻石0。
    策略（2026-08-14 用户确认）：
      - B 段以下（C/CC/CCC）：只升 梦石 > 红石 > 银石，跳过金/虚/幻（到 B 段再考虑）
      - **梦石优先 = 复利**：梦石↑→工坊宝石原石产出↑→更多提升机会（短期难受长期收益）
      - B 段及以上：6 石种全开（金石因近期可能重新启用，排最后观察）
      - 排序：按 拥有量/上限 比例升序（比例低优先），比例相同按上述优先级顺序
    """
    items = get_items()
    stones = items.get("it005", 0)
    print(f"宝石原石持有: {stones}")
    if stones <= 0:
        print("无宝石原石，跳过提升宝石")
        return

    # 各石上限（实测菜单：红50/银50/金30/梦30/虚10/幻10）
    caps = {"1": (50, "红石"), "2": (50, "银石"), "3": (30, "金石"),
            "4": (30, "梦石"), "5": (10, "虚石"), "6": (10, "幻石")}
    # 读提升菜单拿当前拥有量（fyg_menu.php?m=6）
    menu = dec(request(BASE + "/fyg_menu.php", {"m": 6}))
    own = {}
    for sid, (cap, name) in caps.items():
        m = re.search(re.escape(name) + r"\s*已拥有(\d+)", menu)
        own[sid] = int(m.group(1)) if m else 0
    print(f"当前宝石拥有量: " + ", ".join(f"{name}{own[sid]}/{cap}" for sid, (cap, name) in caps.items()))

    # 段位判定：B 段以下（C/CC/CCC）只升梦/红/银（2026-08-14 用户确认：梦石优先 =
    # 复利，梦石↑→原石产出↑→更多提升机会，短期难受长期收益；红石=贝壳+仓库格次之；
    # 虚/幻/金到 B 段再考虑）
    rank = parse_pk()["段位"].strip()
    if rank.startswith("C"):
        prio = ["4", "1", "2"]  # 梦 > 红 > 银
        print(f"段位 {rank}（B 以下）：只升梦/红/银，跳过金/虚/幻")
    else:
        prio = ["4", "1", "2", "5", "6", "3"]  # B 段及以上全开，金石（待重新启用观察）排最后
        print(f"段位 {rank}（B 段及以上）：6 石种全开")

    # 按上限比例升序（比例低优先），比例相同按优先级顺序
    order = sorted(prio, key=lambda sid: (own[sid] / caps[sid][0], prio.index(sid)))
    print(f"提升顺序: " + " → ".join(f"{caps[s][1]}({own[s]}/{caps[s][0]})" for s in order))

    for sid in order:
        if stones <= 0:
            break
        r = click(27, id=sid)
        msg = strip_tags(r)
        print(f"c=27 提升{caps[sid][1]}: {msg[:80]}")
        stones -= 1
        if "宝石原石不足" in msg or "不够" in msg:
            break
    print("提升宝石完成")


def halo():
    """[1.5b] 提升光环：读装备页光环天赋石(3310)持有量 → c=29 提升。

    实测（2026-08-13）：光环天赋页「提升天赋光环」= oclick('29','29','5') → c=29&id=29，
    每次消耗 1 枚光环天赋石，光环值 +（274 前每颗+0.05，280 后衰减）。
    """
    items = get_items()
    stones = items.get("it310", 0)
    print(f"光环天赋石持有: {stones}")
    if stones <= 0:
        print("无光环天赋石，跳过提升光环")
        return
    # 读光环页当前光环值
    # ⚠️ 2026-08-21 修复：光环内容由 JS AJAX 动态加载（eqbp(5) → fyg_read.php POST f=5），
    #    静态 GET fyg_equip.php?eid=5 只返回页面外壳（无"天赋光环"文本）→ 旧代码显示 "?"。
    halo_html = dec(request(BASE + "/fyg_read.php", data="f=5"))
    m = re.search(r"([\d.]+)%\s*天赋光环", halo_html)
    halo_v = m.group(1) if m else "?"
    print(f"当前光环: {halo_v}%")
    for i in range(stones):
        r = click(29, id=29)
        msg = strip_tags(r)
        print(f"c=29 提升光环 #{i + 1}: {msg[:80]}")
        if "天赋石" in msg and ("不足" in msg or "不够" in msg):
            break
    print("提升光环完成")


def parse_equips(html_text, want_id=False):
    """解析装备按钮列表（f=1 沙滩 / f=6 身上通用）。

    每个装备按钮结构（2026-08-12 实测 f=6）：
      <button ... data-content="<p class='fyg_xlxxXXX'>词条名 +N<span class='pull-right bg-*'>&nbsp;150%&nbsp;</span></p>..."
              title="Lv.<span>100</span> 装备名" ...><img src="ys/icon/z2101_4.gif">...
    沙滩版额外含 zbtip('ID','4')。
    返回 [{icon, quality, name, level, total, mystery, bid,
           has_orange, has_red, has_high, affixes}]
      icon: 部位码 zXXXX；quality: 品质数字；total: 词条总值(% 之和)；bid: 沙滩拾取 id
      has_orange/has_red: 橙/红词条；has_high: 含高价值词条
      affixes: 词条明细 [{name, text, pct, color}]（2026-08-23 新增，供装备记录）
      ⚠️ 词条槽固定 4 个（03 文档实测），无 n_affix 字段（恒为 4 无区分度）
    """
    HIGH_AFFIX = ["生命偷取", "附加物伤", "附加魔伤", "附加物穿", "附加魔穿",
                  "技能概率", "暴击概率", "攻击速度"]
    result = []
    for btn_raw in re.findall(r"<button[^>]*>.*?</button>", html_text, re.S):
        if "ys/icon/z" not in btn_raw:
            continue
        # ⚠️ 兼容 HTML 实体转义：fyg_beach.php 页面内 data-content 是转义版
        # (&lt;p class=...&gt;)，f=1 接口返回未转义原生 HTML（2026-08-23 实测）
        btn = html.unescape(btn_raw)
        # icon: background-image:url(ys/icon/z/z2402_2.gif)（品质后缀 _2）
        # ⚠️ 真实路径 icon/ 后带一层 z/ 子目录（2026-08-14 实测）；(?:/z)? 兼容新旧两种写法
        m = re.search(r"ys/icon/z(?:/z)?(\d{4})(?:_(\d))?\.gif", btn)
        icon, quality = (m.group(1), int(m.group(2)) if m and m.group(2) else 0) if m else ("", 0)
        # title: Lv.<span>100</span> <span>星级</span><br>装备名（f=1 沙滩）
        #       Lv.<span class='fyg_f18'>100</span> 装备名（f=6 身上，无 <br>，2026-08-16 实测）
        # ⚠️ 新版沙滩按钮 title 为空、名字在 data-original-title（2026-08-23 实测）
        name = level = "?"
        m = re.search(r'(?:title|data-original-title)="Lv\.<span[^>]*>(\d+)</span>[\s\S]*?(?:<br|</span>)([^"<]*?)(?:"|$)', btn)
        if m:
            level, name = m.group(1), m.group(2).strip()
        else:
            # 旧 f=6 身上: title="Lv.<span class='fyg_f18'>100</span> 探险者之剑"
            m2 = re.search(r'</span>\s*([^"<]+?)"', btn)
            if m2:
                level, name = "?", m2.group(1).strip()
        total = 0.0
        # 词条颜色（2026-08-13 实测 class：danger=红 warning=橙 info=蓝 primary=紫 success=绿）
        has_orange = False
        has_red = False
        has_high = False
        affixes = []  # 词条明细 [{name, text, pct, color}]
        # 每词条: <p class='fyg_xlxxXXX'>词条名 +N<span class='pull-right bg-XXX'>&nbsp;N%&nbsp;</span></p>
        for m in re.finditer(r"<p class='fyg_xlxx(\w+)'>(.*?)</p>", btn, re.S):
            color_cls, affix_html = m.group(1), m.group(2)
            # 词条名+数值文本: <p> 内 <span 前部分（如 "物理攻击 +64.4%" / "附加魔穿 +146"）
            text_m = re.match(r"\s*(.*?)<span", affix_html, re.S)
            affix_text = text_m.group(1).strip() if text_m else ""
            name_m = re.match(r"\s*([^<+\s]+)", affix_text)
            affix_name = name_m.group(1) if name_m else affix_text
            # 评分百分比: pull-right bg-XXX>...NN%（词条总值评分，非词条自身数值）
            val_m = re.search(r"pull-right bg-(\w+)[^>]*>(?:&nbsp;|\s)*(\d+(?:\.\d+)?)%", affix_html)
            if not val_m:
                continue
            color, pct = val_m.group(1), float(val_m.group(2))
            total += pct
            if color == "warning":
                has_orange = True
            elif color == "danger":
                has_red = True
            if any(kw in affix_name for kw in HIGH_AFFIX):
                has_high = True
            affixes.append({"name": affix_name, "text": affix_text,
                            "pct": pct, "color": color})
        mystery = "[神秘属性]" in btn or "神秘属性" in btn
        # bid：沙滩装备 zbtip('ID','4')，仓库装备 zbtip('ID','3')（2026-08-24 修复：
        #   原仅匹配 '4'，导致仓库 f=2 解析 bid 全为 None，smelt/tidy 无法操作）
        m = re.search(r"zbtip\('(\d+)','[34]'\)", btn)
        bid = m.group(1) if m else None
        result.append({"icon": icon, "quality": quality, "name": name,
                       "level": level, "total": total, "mystery": mystery, "bid": bid,
                       "has_orange": has_orange, "has_red": has_red,
                       "has_high": has_high, "affixes": affixes})
    return result


# ═══════════════ 沙滩拾取规则引擎（2026-09-05 重构）═══════════════
# BEACH_RULES 为**用户自定义配置**: 每条 = (名称, 表达式)。
# 表达式 = **字段 + 比较符 + 值**, 用 and / or / not / 括号组装。
# 满足**任一**规则 → 拾取(take), 全部不满足 → 清理(clear)。
# 修改本配置即改规则, 无需命令行参数。
#
# 📖 字段(一级/二级)/比较符/语法/示例/同名硬过滤的**完整说明见**:
#   tools/ggz/装备词条及筛选规则.md（单一事实源）
# 摘要:
#   一级字段(装备原始数据): name / mystery / quality / affix0~3(_name/_pct/_color)
#   二级字段(派生快捷): total_number = affix0_pct+affix1_pct+affix2_pct+affix3_pct
#   比较符: 数字 >= > <= < == != ; 字符串 == != contains startswith endswith(值用引号)
#   BEACH_SAME_NAME_BEST: 同名硬过滤(默认开, 后置拦截, 仓库+沙滩同批, 完全相同都保留)

BEACH_RULES = [
    # 含神秘 → 必收（低品质神秘也收, 神秘价值>>装备本身, 独立于可熔炼）
    ("神秘",   "mystery"),
    # 能熔炼 → 收（品质≥3 且 总值≥410%, 供手动熔炼, 长期规则）
    # ⚠️ 已涵盖橙装（2026-09-05 确认逻辑重复后删除橙装规则）:
    #   橙装 total_number>=516 品质必≥3（品质=总值上限, 516%需品质≥4）且 ≥410
    #   → 任何橙装都满足本规则 → 橙装规则是冗余子集, 删之行为不变;
    #   smelt 命令自身有 total_number<516 / not mystery 兜底, 不会误熔橙装/神秘装
    ("可熔炼", "quality>=3 and total_number>=410"),
]

# ⭐ 同名硬性过滤（2026-09-05, 默认开）: 同名装备不是最好的 → 直接清理。
# 开着 = 仓库+沙滩同批同名只留一件最好的(品质最高, 同品质留总值最高; 完全相同都保留);
# 关掉 = 同名完全交给 BEACH_RULES 表达式决定。
BEACH_SAME_NAME_BEST = True

# 字段注册表（一级字段）: 名称 → 函数(it, ctx) → 值(数字/字符串/布尔/None)
RULE_FIELDS = {}


def _f(name):
    def deco(fn):
        RULE_FIELDS[name] = fn
        return fn
    return deco


# 字段注册表（二级字段）: 名称 → 函数(一级字段求值函数, it, ctx) → 值
# 二级字段 = 由一级字段**派生**（非独立数据），如 total_number = affix0_pct+affix1_pct+...
DERIVED_FIELDS = {}


def _df(name):
    def deco(fn):
        DERIVED_FIELDS[name] = fn
        return fn
    return deco


def _get_field_val(name, it, ctx):
    """统一取字段值: 一级字段直取, 二级字段派生。未知字段返回 None(调用方报错)。"""
    if name in RULE_FIELDS:
        return RULE_FIELDS[name](it, ctx)
    if name in DERIVED_FIELDS:
        return DERIVED_FIELDS[name](it, ctx)
    return None


@_f("name")
def _f_name(it, ctx):
    return it["name"]


@_f("mystery")
def _f_mystery(it, ctx):
    return it["mystery"]


@_f("quality")
def _f_quality(it, ctx):
    return it["quality"]


# ═══════════ 二级字段（2026-09-05 定义：由一级字段派生，非独立数据）═══════════
# total_number = 4 词条评分之和（一级 affix0~3_pct 派生）


@_df("total_number")
def _df_total(it, ctx):
    total = 0.0
    for i in range(4):
        v = RULE_FIELDS[f"affix{i}_pct"](it, ctx)
        if v is not None:
            total += v
    return total


# ═══════════ 词条位置字段（2026-09-05 新增）═══════════
# 装备固定 4 词条，每种装备词条种类固定 → 按位置访问单个词条（affix0~3/_name/_pct/_color）。
# 词条不足 4 个（解析异常/格式变化）→ 字段返回 None，比较结果为 False（不匹配）。
# 字段含义/示例详见 tools/ggz/装备词条及筛选规则.md。
_AFFIX_SUFFIXES = [("", "text"), ("_name", "name"), ("_pct", "pct"), ("_color", "color")]
for _i in range(4):
    for _suffix, _key in _AFFIX_SUFFIXES:
        def _make_affix_field(idx=_i, key=_key):
            @_f(f"affix{idx}{_suffix}")
            def _f_affix(it, ctx, _idx=idx, _key=key):
                affixes = it.get("affixes") or []
                if _idx < len(affixes):
                    return affixes[_idx].get(_key)
                return None  # 词条缺失 → None（比较时按 False 处理）
            return _f_affix
        _make_affix_field()


def _same_name_allow(it, ctx):
    """同名硬性过滤（2026-09-05 修订）。返回 True=允许收 / False=次品同名直接清。

    比较范围 = **仓库 + 沙滩同批**（2026-09-05 用户指定：不再看身上，
    身上装备不参与沙滩决策）。
    判定：品质数字最高才收；**同品质同总值（完全相同）→ 都保留**
    （2026-09-05 用户指定：完全相同的两件不该互斥全清）；同品质不同总值
    → 总值更高才收。
    BEACH_SAME_NAME_BEST=False 时恒 True(不过滤)。"""
    if not BEACH_SAME_NAME_BEST or it["name"] == "?":
        return True
    rivals = [w for w in ctx["store"] + ctx["beach"]
              if w["name"] == it["name"] and w is not it]
    if not rivals:
        return True
    best_q = max(w["quality"] for w in rivals)
    if it["quality"] > best_q:
        return True
    if it["quality"] < best_q:
        return False
    # 同品质: 总值严格更高才收; 完全相同(同总值) → 都保留
    best_t = max(w["total"] for w in rivals if w["quality"] == best_q)
    return it["total"] >= best_t


# ---------- 表达式解析器（递归下降, 支持 and/or/not/括号/数值比较） ----------

def _tokenize(expr):
    tokens = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c in " \t":
            i += 1
            continue
        if expr.startswith("and", i) and not expr[i + 3:i + 4].isalnum():
            tokens.append(("op", "and")); i += 3; continue
        if expr.startswith("or", i) and not expr[i + 2:i + 3].isalnum():
            tokens.append(("op", "or")); i += 2; continue
        if expr.startswith("not", i) and not expr[i + 3:i + 4].isalnum():
            tokens.append(("op", "not")); i += 3; continue
        if c == "(":
            tokens.append(("op", "(")); i += 1; continue
        if c == ")":
            tokens.append(("op", ")")); i += 1; continue
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", expr[i:])
        if not m:
            raise ValueError(f"无法解析位置 {i}: {expr[i:i + 10]!r}")
        name = m.group(0)
        i += len(name)
        cmp_op = cmp_val = None
        # 数字比较: >= <= == != > < 后跟数字（前导空格容错）
        # ⚠️ ==/!= 后跟数字 = 数字比较（2026-09-05 修复：之前 == 只走字符串分支，
        #    total_number==430 会报"不能做字符串比较"）；==/!= 后跟引号 = 字符串比较
        m2 = re.match(r"\s*(>=|<=|==|!=|>|<)\s*(\d+)", expr[i:])
        if m2:
            cmp_op, cmp_val = m2.group(1), int(m2.group(2))
            i += len(m2.group(0))
        else:
            # 字符串比较: == != contains startswith endswith 后跟引号字符串
            # ⚠️ alternation 前必须有 \s*（`name contains '剑'` 的 contains 前有空格,
            #    re.match 从 0 开始, 没有 \s* 会直接失败 → contains 被当字段名吃掉）
            m3 = re.match(r"\s*(==|!=|contains|startswith|endswith)\s*([\"'])(.*?)\2",
                          expr[i:])
            if m3:
                cmp_op, cmp_val = m3.group(1), m3.group(3)
                i += len(m3.group(0))
        tokens.append(("pred", name, cmp_op, cmp_val))
    return tokens


class _RuleParser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self):
        t = self.peek()
        self.pos += 1
        return t

    def parse(self):
        node = self.parse_or()
        if self.peek():
            raise ValueError("表达式末尾有多余内容")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek() and self.peek()[1] == "or":
            self.next()
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.peek() and self.peek()[1] == "and":
            self.next()
            node = ("and", node, self.parse_not())
        return node

    def parse_not(self):
        if self.peek() and self.peek()[1] == "not":
            self.next()
            return ("not", self.parse_not())
        return self.parse_atom()

    def parse_atom(self):
        t = self.next()
        if t is None:
            raise ValueError("表达式不完整（缺谓词）")
        if t[1] == "(":
            node = self.parse_or()
            nxt = self.next()
            if not nxt or nxt[1] != ")":
                raise ValueError("括号不匹配")
            return node
        if t[0] == "pred":
            return ("pred", t[2], t[3], t[1])
        raise ValueError(f"意外的 token: {t[1]!r}")


def _parse_expr(expr):
    return _RuleParser(_tokenize(expr)).parse()


def _eval_rule(node, it, ctx):
    """三值逻辑求值: True/False/None。None = 中性(该规则不做决定),
    为将来可能的"中性字段"保留(如同名比较无同名情形);
    当前字段一级(name/mystery/quality/affixN*)+二级(total_number)均返回
    bool/数字/字符串, None 分支不触发。"""
    kind = node[0]
    if kind == "or":
        l = _eval_rule(node[1], it, ctx)
        if l is True:
            return True
        r = _eval_rule(node[2], it, ctx)
        if r is True:
            return True
        if l is None or r is None:
            return None
        return False
    if kind == "and":
        l = _eval_rule(node[1], it, ctx)
        if l is False:
            return False
        r = _eval_rule(node[2], it, ctx)
        if r is False:
            return False
        if l is None or r is None:
            return None
        return True
    if kind == "not":
        v = _eval_rule(node[1], it, ctx)
        return None if v is None else (not v)
    # ("pred", cmp_op, cmp_val, name)
    _, cmp_op, cmp_val, name = node
    # ⚠️ 统一取字段: 一级(直取) → 二级(派生)
    if name not in RULE_FIELDS and name not in DERIVED_FIELDS:
        raise ValueError(f"未知字段: {name!r}（可用: {', '.join(sorted(RULE_FIELDS) + sorted(DERIVED_FIELDS))}）")
    val = _get_field_val(name, it, ctx)
    if cmp_op is None:
        return bool(val) if val is not None else None
    # 数字比较
    if cmp_op in (">=", "<=", ">", "<"):
        # ⚠️ 词条缺失（affixN 越界返回 None）→ 不匹配（False），不报错
        if val is None:
            return False
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(f"字段 {name} 值 {val!r} 不能做数字比较 {cmp_op}{cmp_val}")
        if cmp_op == ">=":
            return val >= cmp_val
        if cmp_op == "<=":
            return val <= cmp_val
        if cmp_op == ">":
            return val > cmp_val
        return val < cmp_val
    # == / != : 根据值类型智能比较（2026-09-05 修复）
    #   数字值 → 数字比较（total_number==430）；字符串值 → 字符串比较（name=='探险者之剑'）
    if cmp_op in ("==", "!="):
        if val is None:
            return False
        if isinstance(val, bool) or isinstance(val, (int, float)):
            # 数字: cmp_val 已是 int（tokenizer 数字分支）→ 数字比较
            return val == cmp_val if cmp_op == "==" else val != cmp_val
        if isinstance(val, str):
            return val == cmp_val if cmp_op == "==" else val != cmp_val
        raise ValueError(f"字段 {name} 值 {val!r} 无法比较 {cmp_op}")
    # 字符串比较
    if cmp_op in ("contains", "startswith", "endswith"):
        # ⚠️ 词条缺失（affixN 越界返回 None）→ 不匹配（False），不报错
        if val is None:
            return False
        if not isinstance(val, str):
            raise ValueError(f"字段 {name} 值 {val!r} 不能做字符串比较 {cmp_op}")
        if cmp_op == "contains":
            return cmp_val in val
        if cmp_op == "startswith":
            return val.startswith(cmp_val)
        return val.endswith(cmp_val)
    raise ValueError(f"未知比较符 {cmp_op}")


# 表达式编译缓存（每条规则只解析一次）
_RULE_CACHE = {}


def equip_decision(it, worn, store=None, beach_items=None):
    """沙滩装备决策（规则引擎版, 2026-09-05 重构）。返回 (action, reason)。

    action = 'take' / 'clear'；reason = 命中规则名（take 时）或清理原因。
    流程：先求值 BEACH_RULES（任一命中 → take 候选）→ 再套同名硬过滤：
      命中规则 且 通过硬过滤 → take（reason=命中规则名）
      命中规则 但 被硬过滤拦截 → clear（reason=同名硬过滤）⚠️ 硬过滤真正起作用处
      未命中任何规则 → clear（reason=None = 未命中规则）
    ⚠️ 硬过滤后置而非前置（2026-09-05 修正）：前置会把"本来就未命中规则
      的垃圾同名"也归因于"同名硬过滤"，语义误导——垃圾本来就会被清；
      后置才能准确表达"因同名被拦下的是原本会收的装备"。
    """
    ctx = {"worn": worn, "store": store or [], "beach": beach_items or []}
    hit = None
    for name, expr in BEACH_RULES:
        node = _RULE_CACHE.get(expr)
        if node is None:
            try:
                node = _parse_expr(expr)
                _RULE_CACHE[expr] = node
            except ValueError as e:
                print(f"  ⚠️ 规则[{name}] 表达式解析失败: {e} → 跳过该规则")
                continue
        try:
            if _eval_rule(node, it, ctx):
                hit = name
                break
        except ValueError as e:
            print(f"  ⚠️ 规则[{name}] 求值失败: {e} → 跳过该规则")
            continue
    if hit is None:
        return "clear", None  # 未命中任何规则 → 清理
    # 命中规则 → 同名硬性过滤（后置拦截）: 次品同名直接清, 不进仓库
    if not _same_name_allow(it, ctx):
        return "clear", "同名硬过滤（存在更好的同名, 即使命中规则也不收）"
    return "take", hit


def _read_beach(retries=3, interval=3):
    """读取 f=1 沙滩并解析装备列表，带重试和有效性验证。

    返回 (items, raw_text)：
      items: 解析出的装备列表（可能为空 = 沙滩真空）
      raw_text: f=1 原始返回文本（供诊断）

    ⚠️ 2026-08-18 踩坑修复：
      旧代码 read_block(1) 返回空/异常时直接当"沙滩空"处理，
      实际可能是限流（服务器返回空 body）或会话失效（返回"请重新登录"）。
      现在先验证返回内容有效性，无效则重试；重试仍失败则抛异常而非静默跳过。

    验证规则：
      - 返回 <20 字符 → 先用 f=6 探测接口健康度（2026-08-23：f=1 空沙滩
        本身就返回空字符串，与限流同形，必须用 f=6 区分）：
          f=6 也空 → 限流，重试；f=6 正常 → 沙滩真空
      - 含"请重新登录"/"登录" → 会话失效，直接报错（重试无意义）
      - 含 "<button" 但 parse_equips 解析 0 件 → HTML 格式变化，报警但继续
      - 返回正常 HTML 且无 button → 沙滩真空
    """
    last_raw = ""
    for attempt in range(retries):
        raw = read_block(1)
        last_raw = raw
        # 会话失效：不重试，直接报错
        if "请重新登录" in raw or ("登录" in raw and len(raw) < 100):
            raise RuntimeError(
                f"沙滩读取失败：会话已失效（f=1 返回 {len(raw)} 字符: {raw[:80]!r}）。\n"
                "→ 请运行 py tools/get_cookies.py --game 刷新 cookie.txt"
            )
        # 空响应/极短返回：⚠️ 2026-08-23 修复——f=1 空沙滩本身返回空字符串，
        # 不能直接当"限流"。用 f=6（身上装备）探测接口健康度：
        #   - f=6 也空 → 真限流，等待后重试
        #   - f=6 正常 → 沙滩真空（f=1 空 = 文档明确"空沙滩返回空字符串"）
        if len(raw) < 20:
            probe = read_block(6)
            if len(probe) < 20:
                print(f"  ⚠️ f=1 返回异常短（{len(raw)} 字符）且 f=6 也空，疑似限流，"
                      f"{interval}s 后重试 ({attempt+1}/{retries})")
                time.sleep(interval)
                continue
            # f=6 正常 → 确认沙滩真空
            return [], raw
        # 有效 HTML：尝试解析
        items = parse_equips(raw, want_id=True)
        if items:
            return items, raw
        # 有 button 标签但解析 0 件 → 格式可能变了
        if "<button" in raw and "ys/icon/z" in raw:
            print(f"  ⚠️ f=1 含装备按钮但 parse_equips 解析 0 件（HTML 格式可能变化），重试 ({attempt+1}/{retries})")
            time.sleep(interval)
            continue
        # 正常 HTML 但无装备按钮 → 沙滩真空
        return [], raw
    # 重试耗尽（2026-08-19 修复：docstring 承诺"抛异常而非静默跳过"，
    # 旧实现却 return [] 与"沙滩真空"返回值混淆 → beach() 误判空跳过（今日事故）。
    # 现在兑现承诺：抛异常，由调用方决定重试/报错，不再假装沙滩空。）
    raise RuntimeError(f"f=1 读取沙滩失败：重试 {retries} 次仍返回异常内容"
                       f"（最后 {len(last_raw)} 字符: {last_raw[:120]!r}），疑似限流/格式变化")


def get_beach_countdown():
    """读取沙滩自然刷新倒计时（分钟）。

    fyg_beach.php 页面 HTML 服务端渲染：
      <span class="pull-right">距离下次随机装备被冲上沙滩还有 1299 分钟</span>
    ⚠️ f=1 接口**不返回**倒计时（2026-08-24 实测：f=1 只返回装备列表），
       倒计时只能从 fyg_beach.php 页面抓。实测页面 7205 字符、倒计时为静态值。

    返回 int 分钟数；读取失败/格式变化返回 None（调用方自行降级处理）。
    """
    try:
        text = dec(request(BASE + "/fyg_beach.php"))
        m = re.search(r"距离下次随机装备被冲上沙滩还有\s*(\d+)\s*分钟", text)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def beach(allow_refresh=True, wait_after_refresh=True):
    """[4] 沙滩收取 + 清理（4.5 规则）。

    流程：**先检查仓库空格（<10 自动整理腾仓）** → 读 f=1 沙滩 + f=6 身上
    → 逐件决策 → 先 c=1 拾取要收的 → 再 c=20 清理剩余。
    沙滩空但有随机装备箱 → 自动强制刷新（c=12）再筛（allow_refresh=False 时跳过，
    供 beach_refresh 调用避免二次刷新白耗箱子）。
    wait_after_refresh=False：沙滩空时直接跳过不等待（--no-refresh 场景，
    保留装备箱供测试，不耗 c=12）。

    ⚠️ 仓库预整理（2026-09-05 新增）：拾取会占仓库格，空格不足时 c=1 拾取失败
    （装备滞留沙滩）。因此开头读仓库空格，<10（一滩最多拾取 10 件）→ 自动调
    warehouse_tidy(clear_beach=False) 整理腾仓（灰蓝全丢 + 绿装同名去重 → 丢沙滩）。
    ⚠️ 不立即 c=20：否则会误清沙滩上自然刷新的未决策装备。tidy 丢出的装备
    （灰蓝未命中规则 / 低值绿被同名硬过滤拦）由本流程决策后统一 c=20 回收，不捡回。

    ⚠️ 踩坑记录（2026-08-18 重写）：
      1. f=1 返回空/异常 ≠ 沙滩空：旧代码不区分"读取失败"和"真空"，
         限流返回空 body 时误判沙滩空 → 跳过清理。现在 _read_beach() 带重试验证。
      2. 会话失效（"请重新登录"）：cookie 被浏览器顶掉时 f=1 返回 9 字符提示，
         旧代码当"空"处理。现在直接 raise 提示重抓 cookie。
      3. 沙滩 id 拾取后重排：逐件拾取后剩余 id 全变，禁止复用旧 id。
      4. ✅ 定论（2026-09-03 澄清）：c=12 后读 f=1 返回空**不存在服务器"空窗期"**，
         历史"64s/90s/15-21s 空窗"全是误测。两类真实原因：
         a) 脚本缺浏览器 reload 的整页 GET → 先 GET fyg_beach.php 再读 f=1（见下方）；
         b) 高频轮询触发 f=1 专属真限流 → 保持低频（读 1 次 + 10s 间隔兜底）。
      5. 限流：momozhen 对连续请求限流（返回空/SSL 断开），requests.Session 已缓解，
         但高频场景仍需间隔 ≥2s。
    """
    # ⚠️ 仓库预整理（2026-09-05）：拾取占仓库格，空格不足时 c=1 拾取失败。
    #   空格 <10（一滩最多拾取 10 件）→ 自动 tidy 腾仓。
    #   ⚠️ clear_beach 必须为 False：tidy 若立即 c=20 会清掉沙滩上**自然刷新
    #   未决策**的装备（误清！）；tidy 只丢沙滩，后续 beach 正常读滩→决策，
    #   丢出的灰蓝未命中规则/低值绿被同名硬过滤拦 → 由 beach 自己的 c=20 统一回收，
    #   不误伤自然装备。整理失败只告警，不阻塞 beach。
    space = get_store_space()
    if space is not None:
        print(f"仓库剩余空格: {space}")
        if space < 10:
            print("→ 仓库空格不足 10，先整理仓库腾空间（只丢沙滩，清理由 beach 统一处理）...")
            try:
                warehouse_tidy(clear_beach=False)
            except Exception as e:
                print(f"  ⚠️ 仓库整理失败（继续 beach，拾取可能失败）: {e}")
    else:
        # 空格读取失败（限流/格式变化）→ 留日志便于追溯，不阻塞 beach
        print("⚠️ 仓库空格读取失败（疑似限流/格式变化），跳过预整理（拾取可能因空间不足失败）")
    # ⚠️ 自然刷新倒计时（2026-08-24 新增）：每次执行都打印一次，留档便于追溯
    #   "自然刷新时机"。数据源 = fyg_beach.php 页面服务端渲染（f=1 接口不返回）。
    #   1320 分钟倒计时归零后装备**不会立即**冲上沙滩，服务器还有 ≈4~14 分钟
    #   调度延迟。完整实测时间线（2026-08-24）：
    #     08-23 11:28:20  最后一次 c=12 强制刷新 → 倒计时重置 1320 分钟
    #     08-24 09:28:20  倒计时理论归零（1320 分钟走完）
    #     08-24 09:31-32  日常脚本沙滩步骤 → 沙滩仍空（错过自然批次）
    #     08-24 ≈09:42    浏览器确认沙滩已有 10 件
    #     → 实际冲装备落在 09:32~09:42，相对理论归零延迟 ≈4~14 分钟
    countdown = get_beach_countdown()
    if countdown is not None:
        print(f"沙滩自然刷新倒计时: {countdown} 分钟")
    # 装备箱持有量（2026-08-24 提到开头无条件打印，原仅在沙滩空分支打印）
    boxes = get_items().get("it004", 0)
    print(f"随机装备箱持有: {boxes}")
    try:
        items, raw = _read_beach()
    except RuntimeError as e:
        print(f"  ❌ 沙滩读取失败: {e}")
        print("  → 限流通常是暂时的，稍后重跑 beach 即可；若持续失败请检查 cookie")
        return
    if not items:
        # 确认是真空（_read_beach 已排除限流/会话失效）
        # ⚠️ 沙滩空 → c=12 自动刷新（2026-08-16 实测改版）。
        # ✅ 定论（2026-09-03 澄清，作废"空窗期"说法）：c=12 后立即读 f=1
        #   返回空，历史上先后被误记为"64s/90s/15-21s 空窗期"，实际**不存在**
        #   任何服务器空窗期，全是两个可复现原因的误测：
        #   a) 缺 reload：脚本 c=12 后直接 POST f=1，少了浏览器 reload 的整页
        #      GET → f=1 返回空（08-24 实测：脚本 55s 全空、浏览器 reload 立即
        #      有 10 件；补 GET 后第 1 次就读到，08-25 起零失败）；
        #   b) 真限流：2s 密集轮询 f=1 触发专属限流（08-23 实测：f=6/2/12
        #      正常但 f=1 连浏览器同 IP 都空）。
        #   因此策略 = GET-first（治 a）+ 低频重试（防 b），无需任何固定等待。
        print(f"沙滩空，无装备")
        # 若倒计时 ≤15 分钟 → 自然刷新在即，**不耗装备箱**，跳过等自然刷新
        #   （否则 c=12 会重置计时，白耗 1 箱且错过马上要来的自然批次）。
        if countdown is not None and countdown <= 15:
            print(f"→ 自然刷新倒计时仅剩 {countdown} 分钟（归零后约 4~14 分钟延迟），"
                  f"即将自动冲装备 → 跳过本次（保留装备箱）")
            return
        if allow_refresh and boxes > 0:
            print("→ 有装备箱，自动强制刷新沙滩...")
            r = click(12)
            show("c=12 刷新沙滩返回", r)
            # c=12 成功后倒计时重置 1320、装备箱 -1，重新读取打印（与开头一致）
            if r.strip() == "ok":
                boxes = get_items().get("it004", 0)
                print(f"随机装备箱持有: {boxes}（刷新消耗 1 个）")
                cd = get_beach_countdown()
                if cd is not None:
                    print(f"沙滩自然刷新倒计时: {cd} 分钟（c=12 已重置）")
        elif allow_refresh:
            print("→ 无随机装备箱，跳过")
            return
        elif not wait_after_refresh:
            print("→ 用户指定不刷新（--no-refresh），跳过沙滩（保留装备箱）")
            return
        else:
            print("→ 刚刷新过（allow_refresh=False），按 GET-first 策略重读...")
        # ✅ c=12 后读取铁律（2026-08-24 重构，2026-09-03 定稿）：先 GET 后 POST。
        #   浏览器 gx_sxst() 在 c=12 返回 ok 后执行 window.location.reload()
        #   （整页 GET fyg_beach.php），页面加载时 stall() 再 POST f=1 读装备。
        #   接口和脚本完全一样（c=12 + f=1），唯一差异就是浏览器多了 reload。
        #   服务端需要一次页面 GET 确认状态，脚本缺这步 → f=1 返回空
        #   （08-24 实测：c=12 ok 后脚本 55s 内 f=1 全空，浏览器 reload 立即有装备；
        #   同日补上 GET 后第 1 次读取即成功，08-25~08-28 连续 4 天零失败）。
        #   ⚠️ 此规律是普适的，不限于 c=12 后：首次读 f=1 前若从未 GET 过
        #   fyg_beach.php 也可能空（beach() 开头 get_beach_countdown() 的 GET
        #   顺带覆盖了首次读取；单独调 _read_beach 的场景需自行保证先 GET）。
        #   GET 后立即读 1 次；仍空则 10s 间隔重试兜底（最多 4 次覆盖 0-30s），
        #   兜底针对真限流/服务器延迟，勿改为 2s 密集轮询（会触发 f=1 专属限流）。
        try:
            request(BASE + "/fyg_beach.php")  # 模拟浏览器 reload（铁律：先 GET 后 POST f=1）
        except Exception:
            pass  # reload 失败不致命，继续尝试读 f=1
        for attempt in range(4):
            try:
                items, raw = _read_beach(retries=1)  # 单次读取
            except RuntimeError:
                items = []
            if items:
                print(f"  ✅ 刷新后第 {attempt + 1} 次读取到 {len(items)} 件装备")
                break
            if attempt < 3:
                print(f"  ⏳ 刷新后第 {attempt + 1} 次仍空，10s 后重试...")
                time.sleep(10)
        else:
            print("  ⚠️ 刷新后 30s 仍未读到装备（可能服务器延迟，可稍后重跑 beach）")
            return
    worn = parse_equips(read_block(6))
    # 仓库读取（2026-09-05: 同名保留最好规则需要仓库数据）;
    # 读取失败(限流)只告警, 不中断 — 同名规则退化为仅对比沙滩同批
    store = []
    try:
        store = parse_equips(read_block(2))
    except Exception as e:
        print(f"  ⚠️ 仓库读取失败（同名规则可能不准确）: {e}")
    print(f"沙滩 {len(items)} 件，身上 {len(worn)} 件，仓库 {len(store)} 件")
    for it in items:
        mark = []
        if it["has_orange"]:
            mark.append("橙")
        if it["has_red"]:
            mark.append("红")
        if it["mystery"]:
            mark.append("神秘")
        mark_str = f" 词条[{'/'.join(mark)}]" if mark else ""
        print(f"  >{it['name']} {it['quality']}等 {it['total']:.0f}% icon={it['icon']} id={it['bid']}{mark_str}")
        # 词条明细（2026-08-23 新增：装备记录用）
        for a in it["affixes"]:
            color_cn = {"danger": "红", "warning": "橙", "info": "蓝",
                        "primary": "紫", "success": "绿"}.get(a["color"], a["color"])
            print(f"      {a['text']}  [评分{a['pct']:.0f}% 词条{color_cn}]")
        if not it["affixes"]:
            print(f"      （无词条明细）")

    take_ids, clear_count = [], 0
    print("逐件决策（规则引擎）:")
    for it in items:
        if it["bid"] is None:
            continue
        action, reason = equip_decision(it, worn, store, items)
        if action == "take":
            take_ids.append(it["bid"])
            print(f"  ✅ {it['name']} {it['quality']}等 {it['total']:.0f}% → 拾取 [{reason}]")
        else:
            clear_count += 1
            rsn = f"（{reason}）" if reason else "（未命中规则）"
            print(f"  ➖ {it['name']} {it['quality']}等 {it['total']:.0f}% → 清理{rsn}")
    print(f"决策: 拾取 {len(take_ids)} 件, 清理 {clear_count} 件")

    for bid in take_ids:
        r = click(1, id=bid)
        show(f"c=1 拾取 id={bid}", r, 80)
    if clear_count > 0:
        r = click(20)
        show("c=20 清理沙滩", r)


def smelt():
    """[4.5c] 熔炼仓库可熔炼装备为护身符（4.5b 决策树）。

    触发条件（2026-08-10 实测）：品质≥3 且 总值≥410% 且 无神秘 且 非橙装（<516%）。
    熔炼 = c=9&id=<仓库装备id>&yz=124，返回新护身符 id；装备消失。
    ⚠️ 神秘装/橙装永不熔炼（巨亏教训）。
    """
    t = read_block(2)
    items = parse_equips(t, want_id=True)
    if not items:
        print("仓库空，无可熔炼")
        return
    # f=2 仓库装备 id 在 zbtip('id','3')，parse_equips 已兼容（2026-08-24 修复）
    smeltable = []
    for it in items:
        if it["quality"] >= 3 and it["total"] >= 410 and not it["mystery"] and it["total"] < 516:
            smeltable.append(it)
    if not smeltable:
        print("仓库无可熔炼装备（需品质≥3 且总值≥410% 且无神秘 且非橙装）")
        return
    print(f"可熔炼 {len(smeltable)} 件:")
    for it in smeltable:
        print(f"  {it['name']} {it['quality']}等 {it['total']:.0f}% id={it['bid']}")
    for it in smeltable:
        if it["bid"] is None:
            continue
        r = click(9, id=it["bid"], yz=124)
        msg = strip_tags(r)
        print(f"c=9 熔炼 {it['name']}: {msg[:80]}")
        if "至少需要稀有" in msg or "不可熔炼" in msg:
            print("  ⚠️ 熔炼资格判断有误，停止")
            break


def get_store_space():
    """读取仓库剩余空格数（f=2 返回 `剩余 N 仓库空格`，2026-09-05 实测确认）。

    返回 int；读取失败/格式变化返回 None（调用方自行降级，如跳过整理）。
    """
    try:
        t = read_block(2)
        m = re.search(r"剩余\s*(\d+)\s*仓库空格", t)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def warehouse_tidy(dry_run=False, green_only=False, clear_beach=False):
    """[4.5d] 仓库整理（2026-09-05 整合进 ggz_daily，源自 tools/ggz/warehouse_tidy.py）。

    整理规则（2026-08-24 用户指定）：
      - 灰/蓝（品质 1/2）：全部丢沙滩（c=7，可逆，24h 内可捡回）
        ⚠️ 日常脚本沙滩不会捡灰蓝装备，所以仓库里的灰蓝多为历史遗留/熔炼备料
      - 绿（品质 3）：同名装备只留 4 词条总值（total）最高的一件，其余丢沙滩
        （绿色不会有神秘属性——品质≥4 才可能出神秘，见 03-装备说明.md §2.3）
      - 橙/红（品质 4/5）：不处理（可能含神秘，价值高，留给用户手动决策）

    丢沙滩后默认不自动清理（保守，避免误清沙滩原有装备）。
    clear_beach=True 则丢完立即 c=20 清理沙滩回收锻造石。
    用途：beach 前整理仓库腾空间（空格 <10 时自动调用）。
    """
    t = read_block(2)
    items = parse_equips(t, want_id=True)
    if not items:
        print("仓库空，无需整理")
        return
    print(f"仓库共 {len(items)} 件装备")

    # 按品质分组统计
    by_quality = defaultdict(int)
    for it in items:
        by_quality[it["quality"]] += 1
    qnames = {1: "灰", 2: "蓝", 3: "绿", 4: "橙", 5: "红"}
    print("品质分布: " + " / ".join(f"{qnames.get(q, q)}{c}件" for q, c in sorted(by_quality.items())))

    to_drop = []

    # ① 灰/蓝（品质 1/2）：全部丢沙滩（除非 --green-only）
    if not green_only:
        low = [it for it in items if it["quality"] in (1, 2) and it["bid"]]
        if low:
            print(f"\n--- 灰/蓝装备（品质1/2）{len(low)} 件 → 全部丢弃 ---")
            for it in low:
                print(f"  ✗ {it['name']} {it['quality']}等 {it['total']:.0f}% (id={it['bid']})")
            to_drop.extend(low)
        else:
            print("\n无灰/蓝装备")

    # ② 绿（品质 3）：同名只留总值最高
    green = [it for it in items if it["quality"] == 3]
    if green:
        print(f"\n--- 绿色装备（品质3）{len(green)} 件 → 同名留总值最高 ---")
        groups = defaultdict(list)
        for it in green:
            groups[it["name"]].append(it)
        for name, group in sorted(groups.items()):
            group.sort(key=lambda x: x["total"], reverse=True)
            keep = group[0]
            drops = group[1:]
            print(f"  {name}: {len(group)} 件 → 保留 {keep['total']:.0f}% (id={keep['bid']})"
                  + (f"，丢弃 {len(drops)} 件" if drops else ""))
            for d in drops:
                print(f"    ✗ 丢弃 {d['total']:.0f}% (id={d['bid']})")
                if d["bid"]:
                    to_drop.append(d)
    else:
        print("\n无绿色装备")

    # ③ 橙/红（品质 4/5）：仅列出，不处理
    high = [it for it in items if it["quality"] in (4, 5)]
    if high:
        print(f"\n--- 橙/红装备（品质4/5）{len(high)} 件 → 不处理（可能含神秘，手动决策）---")
        for it in high:
            mark = []
            if it["mystery"]:
                mark.append("神秘")
            if it["has_orange"]:
                mark.append("橙词条")
            if it["has_red"]:
                mark.append("红词条")
            mark_str = f" [{','.join(mark)}]" if mark else ""
            print(f"  ✓ 保留 {it['name']} {it['quality']}等 {it['total']:.0f}% (id={it['bid']}){mark_str}")

    # 汇总
    print(f"\n{'=' * 50}")
    print(f"待丢弃: {len(to_drop)} 件")
    if not to_drop:
        print("仓库无需整理")
        return
    if dry_run:
        print("[--dry-run] 仅预览，不实际操作")
        return

    # 逐件丢沙滩（c=7，可逆）
    print("开始丢沙滩...")
    drop_ok = 0
    for i, it in enumerate(to_drop, 1):
        r = click(7, id=it["bid"])
        msg = strip_tags(r)[:80]
        # ⚠️ 成功判定（2026-09-05 实测校准）：c=7 成功返回
        #   "已将装备丢弃到沙滩，在它从沙滩上消失前，仍可以捡回。"（08-24 日志 26 条样本）；
        #   失败（限流/会话失效/id 失效）要显式告警，避免"看似成功实际没丢"
        ok = "丢弃到沙滩" in msg or "已放入" in msg
        mark = "✅" if ok else "❌"
        print(f"  [{i}/{len(to_drop)}] {mark} c=7 丢弃 {it['name']} {it['quality']}等 "
              f"{it['total']:.0f}% (id={it['bid']}): {msg}")
        if ok:
            drop_ok += 1
        time.sleep(random.uniform(0.5, 1.0))  # 间隔避免限流

    print(f"\n完成：成功丢弃 {drop_ok}/{len(to_drop)} 件到沙滩（24h 内可捡回）")
    if drop_ok < len(to_drop):
        print(f"  ⚠️ {len(to_drop) - drop_ok} 件丢弃失败，请检查返回文本（限流/会话/id 失效）")

    # clear_beach：丢完后立即清理沙滩回收锻造石
    if clear_beach:
        print("\n[--clear-beach] 自动清理沙滩回收锻造石...")
        r = click(20)
        msg = strip_tags(r)[:120]
        print(f"  c=20 清理沙滩: {msg}")
    else:
        print("\n未自动清理沙滩（保留可捡回窗口）")
        print("→ 如需清理沙滩回收锻造石，运行: py tools/ggz/ggz_daily.py beach")


def beach_refresh():
    """[4.5] 强制刷新沙滩（耗 1 随机装备箱，c=12）→ 刷新后按 4.5 规则再筛一轮

    ⚠️ 2026-08-16：beach 已恢复自动刷新，这里先 c=12 再调 beach 会二次刷新白耗箱子，
    故传 allow_refresh=False 让 beach 不重复刷（c=12 后由 beach 走 GET-first 读 f=1）。
    """
    boxes = get_items().get("it004", 0)
    print(f"随机装备箱持有: {boxes}")
    if boxes <= 0:
        print("无随机装备箱，跳过强制刷新")
        return
    r = click(12)
    show("c=12 刷新沙滩返回", r)
    beach(allow_refresh=False, wait_after_refresh=True)


def parse_monster(r):
    """从战报解析野怪信息: 名字/等级/护盾/生命/天赋。

    2026-09-05 新增: 用于记录野怪池数据（验证"同一时段野怪池是否固定"），
    并为后续战斗模拟器（calcBattle 公式）提供输入。
    战报结构: alert-info 区块含 <span class="fyg_f18">营养均衡的史莱姆（野怪 Lv.27）</span>
    护盾/生命在 label 里, 天赋在 |复合护盾|圣盾祝福|...| 里。
    非野怪对手（打人）或轮空/上限消息返回 None。
    """
    idx = r.find('alert-info')
    if idx < 0:
        return None
    chunk = r[idx:idx + 1000]
    m = re.search(r'fyg_f18[^>]*>([^<]+)<', chunk)
    name = m.group(1).strip() if m else '?'
    m2 = re.search(r'(\d+) 护盾</span>[^>]*>(\d+) 生命', chunk)
    sld, hp = (m2.group(1), m2.group(2)) if m2 else ('?', '?')
    # 天赋: |复合护盾||圣盾祝福|...|<br>|午时已到||绝对底线| （跨 <br> 多行, 双竖线是分隔）
    raw = re.sub(r'<br>', '|', chunk)  # 先把 <br> 换成 | 统一分隔
    talents = [t.strip() for t in re.findall(r'\|([^|]+)\|', raw) if t.strip()]
    return {"name": name, "sld": sld, "hp": hp, "talents": talents}


def fight(target=1):
    """出击一次，返回 (结果类型, 原始文本)。fyg_v_intel.php 需带 safeid！"""
    r = dec(request(BASE + "/fyg_v_intel.php", {"id": target, "safeid": SAFEID}))
    if f"{USER} 获得了胜利！" in r:
        return "win", r
    # 平局两种文本: 同归于尽 / 100 回合超时强制结束（2026-09-04 实测补上后者，
    # 之前漏判导致 100 回合平局被误判 unknown，出击空转 20 次）
    if "双方同归于尽" in r or "本场不计入胜负场次" in r:
        return "draw", r
    if "不计出击次数，请重试" in r:
        return "retry", r
    if "今日已主动出击20次" in r:
        return "limit", r
    m = re.search(r"([^<>\"']+?) 获得了胜利！", r)
    if m:
        return "lose", r
    return "unknown", r


def parse_pk():
    t = read_block(12)
    seg = re.search(r"font-weight:900;\">(.*?)</span><br>当前所在段位", t)
    prog = re.search(r"font-weight:700;\">(.*?)%</span><br>段位进度", t)
    dog = re.search(r"font-weight:700;\">(\d+) / (\d+)</span><br>今日获得狗牌", t)
    streak = re.search(r"font-weight:700;\">(\d+) \| (\d+)</span><br>连胜场次", t)
    seg_name = seg.group(1) if seg else "?"
    prog_v = prog.group(1) if prog else "?"
    dog_v, out_v = (dog.group(1), dog.group(2)) if dog else ("?", "?")
    win_s, lose_s = (streak.group(1), streak.group(2)) if streak else ("?", "?")
    return {
        "段位": seg_name, "进度": prog_v + "%", "狗牌": dog_v, "出击": out_v,
        "连胜": win_s, "连败": lose_s,
    }


def pk(max_fights=20, full=False):
    """[5] 出击。默认拿满 3 狗牌即停；--full 打满 max_fights 次。

    策略（2026-08-17 用户改版，2026-09-05 修正）：**优先打人**（id=2，胜利 +3% 进度），
    打不过（lose）或轮空（retry）再切打野（id=1，胜利 +1%）。
    打野失败 → **继续打野**（不切回打人：打人匹不到会轮空，来回切 = 死循环；
    留在打野稳定累计连败 → 5 连败掉段送狗牌 + 野怪变弱 → 更好打），
    直到拿满 3 狗牌或打满 max_fights 次。

    打野平局（2026-09-05 用户改版）：平局=100 回合打不死，说明当前角色
    打不过这只怪 → **自动切换出战角色，从打人重新开始**（避免空转 20 次）。
    角色按 CARD_ZIDS 顺序轮换，全部试完仍平局则停止。
    """
    global ZID
    # 角色轮换列表（打野平局时切换）: 先试其他角色,最后回到当前
    # 2026-09-05: 谁打赢用谁——换角色后不切回原角色（避免每天重复换）
    card_order = list(CARD_ZIDS.values())
    switch_seq = [z for z in card_order if z != ZID] + [ZID]  # 先试其他角色,最后回到当前
    switch_idx = 0
    draw_count = 0

    # 当前模式: pvp(打人) / pve(打野)。开局先试打人。
    mode = "pvp"
    for i in range(1, max_fights + 1):
        st = parse_pk()
        print(f"\n--- 出击 #{i} 前状态: 段位{st['段位']} {st['进度']} 狗牌{st['狗牌']}/{st['出击']} 连胜{st['连胜']} 连败{st['连败']} 模式={mode} ---")
        # ⚠️ 狗牌数是服务器端状态, 限流只是暂时读不到, 不是没有（2026-09-05 修正）:
        # 解析失败 → 短间隔重读（限流多为瞬时, 恢复后就能读到真实值）;
        # 重试仍失败 → 保守停止, 绝不假设 0（假设 0 会在已满 3 狗牌时
        # 继续盲打, 白白浪费出击次数）
        dogs = None
        for attempt in range(4):
            try:
                dogs = int(st["狗牌"])
                break
            except (TypeError, ValueError):
                print(f"  ⚠️ 狗牌数解析失败（第 {attempt + 1}/4 次, 疑似限流）, 1s 后重读...")
                time.sleep(1)
                st = parse_pk()
        if dogs is None:
            print("  ⛔ 战场状态连续读取失败（限流）, 停止出击, 稍后重跑")
            break
        if not full and dogs >= 3:
            print("✅ 已拿满 3 狗牌，停止出击（--full 可打满）")
            break
        # 模式决策: 打人失败/轮空 → 切打野; 打野失败 → 切回打人（循环到 3 狗牌/20 次）
        target = 2 if mode == "pvp" else 1
        kind, r = fight(target)
        target_name = "打人" if target == 2 else "打野"
        print(f"出击结果: {kind}（{target_name}）")
        # 记录野怪信息（2026-09-05: 积累野怪池数据, 验证等级/天赋分布）
        if target == 1 and kind in ("win", "draw", "lose"):
            mon = parse_monster(r)
            if mon:
                print(f"  野怪: {mon['name']} 盾{mon['sld']} 血{mon['hp']} 天赋{mon['talents']}")
        if kind == "limit":
            print("出击次数达上限")
            break
        if kind == "retry" or kind == "draw":
            # 不计次数。打人轮空/平局 → 切打野; 打野平局 → 换角色重来（从打人开始）
            if target == 2:
                print("  ↪ 打人轮空/平局，切打野")
                mode = "pve"
            elif kind == "draw":
                # 打野平局: 100 回合打不死 → 换角色重来（从打人开始）
                draw_count += 1
                print(f"  ↪ 打野平局（第 {draw_count} 次）→ 换角色重来")
                if switch_idx >= len(switch_seq):
                    print("  ⛔ 所有角色都试过了，仍打不过，停止")
                    break
                new_zid = switch_seq[switch_idx]
                switch_idx += 1
                print(f"  🔄 切换出战角色 → zid={new_zid}")
                r = click(5, id=new_zid)
                if "ok" not in r and "装备成功" not in r:
                    print(f"  ⚠️ 切卡失败: {strip_tags(r)[:60]}")
                    continue
                ZID = new_zid
                # 换角色后按新角色策略切换加点（2026-09-05: 点数共享,
                # apply 全量覆盖 = 切到该角色专属配置, 只耗 1 次修改）
                try:
                    addpoint(zid=new_zid, apply=True)
                except Exception as e:
                    print(f"  ⚠️ 加点异常: {e}")
                mode = "pvp"  # 从打人重新开始
            continue
        if kind == "lose":
            if target == 2:
                print("  ↪ 打人失败，切打野")
                mode = "pve"
            else:
                # 打野失败 → 留在打野继续（不切回打人）：
                # 打人匹不到会轮空(不计次数)，切回去只会 pvp/pve 来回空转 = 死循环;
                # 留在打野稳定累计连败 → 5 连败掉段送狗牌 + 野怪变弱 → 更好打
                print("  ↪ 打野失败，继续打野（连败累计，掉段后野怪变弱更好打）")
                mode = "pve"
            continue
        # win: 保持当前模式
        st = parse_pk()
        print(f"出击后: 狗牌{st['狗牌']} 出击{st['出击']} 连胜{st['连胜']} 连败{st['连败']}")
    print("\n=== 出击结束 ===")
    print(parse_pk())


def bonus():
    """[7] 额外奖励：c=13&id=1 耗 1 体能刺激药水再领一次翻牌奖励。"""
    r = click(13, id=1)
    show("c=13&id=1 额外奖励", r)


def gift(bonus=0):
    """[6] 翻牌 + 可选药水 bonus（2026-08-12 实测机制 + 2026-08-26 透视自动检测 + bonus 配置项）：
    - f=10 每张牌一个按钮：有 giftop(N) onclick = 未翻；btn-info/success/warning/danger = 已翻
    - 品质→颜色：btn-info=幸运(蓝) btn-success=稀有(绿) btn-warning=史诗(黄) btn-danger=传说(红)
    - c=8 翻牌：成功直接返回结算文本（含"获得"）；"该牌面已翻开" = 该张已翻
    - ⭐ 透视自动检测（2026-08-26 新增）：f=10 返回体若含 `是"品质1,品质2,..."</p>`
      （12 个品质逗号分隔，slack 源码同款正则，兼容全角/半角引号）= 服务端已开翻牌透视
      → 提前泄露全部牌品质 → 定向翻收益最大化：优先 3 传说(红)（档位最高，含全套附加），
      其次 3 史诗(黄)。为什么：透视是服务端按账号下发的条件功能，非人人有（08-26 Nightly
      实测本账号无、未翻牌零泄露、品质纯服务端判定）。哪天账号被动开透视，脚本须当天
      立即吃满红利——盲翻 = 白浪费精准翻红/黄能力。
    - 无透视 → 原策略：按序翻未翻牌 + 统计颜色，某色 3 张即结算停止。
    - ⭐ bonus 配置项（2026-08-26 用户要求；药水默认不自动用，需显式指定）：
        bonus=0（默认）→ 仅翻牌，不耗药水
        bonus=1（--bonus1）→ 翻牌后 c=13&id=1 耗 1 药水再领一次翻牌奖励
          （固定 6000 贝壳+6000 经验，08-19~26 日志一致；药水不足返回"物品不足"零消耗）
        bonus=2（--bonus2）→ 翻牌后 c=13&id=2 耗 2 药水**重置今日狗牌+翻牌**
          （需"出击≥5次"前置，slack 成功判定=返回以"可出击数已刷新"开头）
          → 重新出击拿 3 狗牌（pk）→ 再翻牌一次（第二轮 bonus=0，防递归）
    """
    _gift_flip()
    if bonus == 1:
        r = click(13, id=1)
        show("c=13&id=1 额外奖励(--bonus1 耗1药水)", r)
    elif bonus == 2:
        r = click(13, id=2)
        show("c=13&id=2 重置狗牌+翻牌(--bonus2 耗2药水)", r)
        if r.startswith("可出击数已刷新"):
            print("\n→ 重置成功，重新出击拿 3 狗牌（pk）...")
            pk()
            print("\n→ 重新翻牌（本轮 bonus=0，不再触发）...")
            _gift_flip()
        else:
            print("⚠️ 重置未成功（出击<5 或药水不足，服务器拒绝且不扣药水），跳过第二轮")


def _gift_flip():
    """[6] 翻牌核心（由 gift() 调用，不处理药水）：透视自动检测 + 定向翻红/黄 / 无透视盲翻。"""
    COLOR_CLASS = {"btn-info": "蓝(幸运)", "btn-success": "绿(稀有)",
                   "btn-warning": "黄(史诗)", "btn-danger": "红(传说)"}

    def parse_gift():
        """返回 12 个位置的牌状态: [{pos, name, flipped, color}]"""
        t = read_block(10)
        # 捕获完整 <button ...>...</button>（含 onclick/class 属性）
        btns = re.findall(r"<button[^>]*>.*?</button>", t, re.S)
        result = []
        for i, btn in enumerate(btns, 1):
            cls = re.search(r'class="([^"]*)"', btn)
            classes = cls.group(1) if cls else ""
            text = strip_tags(btn).replace("\u00a0", "").strip()
            can_flip = "giftop" in btn
            color = next((v for k, v in COLOR_CLASS.items() if k in classes), None)
            result.append({"pos": i, "name": text, "flipped": not can_flip, "color": color})
        return result

    def detect_perspective():
        """⭐ 翻牌透视自动检测（2026-08-26 新增）。
        读 f=10 原始 HTML，匹配 slack 源码的透视文本 `是"品质1,品质2,..."</p>`
        （12 个品质按从左到右 12 张牌位置对应；兼容全角/半角引号）。
        返回 12 个品质的 list；未命中（无透视）返回 None。
        为什么要做：透视是服务端按账号下发的条件功能，账号哪天被开启后 f=10 会
        提前泄露全部牌品质。被动开透视当天脚本即自动切定向翻红/黄，无需人工改代码。"""
        t = read_block(10)
        m = re.search(r'是[“"]([^”"]+)[”"]</p>', t)
        if not m:
            return None
        persp = [x.strip() for x in m.group(1).split(",")]
        # 正常 12 张牌恰好 12 个品质；数量异常视为未开透视（防误判乱翻）
        return persp if len(persp) == 12 else None

    state = parse_gift()
    print("翻牌区状态:")
    for s in state:
        mark = f"{s['color']}" if s["flipped"] and s["color"] else ("未翻" if not s["flipped"] else "已翻")
        print(f"  {s['pos']}. {s['name']} [{mark}]")

    # 统计已翻颜色
    counts = {}
    for s in state:
        if s["flipped"] and s["color"]:
            counts[s["color"]] = counts.get(s["color"], 0) + 1
    print(f"已翻颜色计数: {counts}")
    if any(v >= 3 for v in counts.values()):
        print("✅ 已有同色 3 张（今日已结算），翻牌完成")
        return

    def do_flip(s):
        """翻一张牌并处理返回；返回 True = 应停止（已结算/需先拿狗牌），False = 继续。
        ⚠️ 拒翻判定先于结算判定：未拿满 3 狗牌时返回
        "请先在争夺战场拿到3枚狗牌，狗牌在PVP/PVE胜利获得"（含"获得"但非结算，
        2026-08-17 实测踩坑）。真正的结算文本才含"获得"。"""
        r = click(8, id=s["pos"])
        txt = strip_tags(r)
        print(f"\n翻牌 #{s['pos']} ({s['name']}): {txt[:120]}")
        if "狗牌" in txt and ("请先" in txt or "拿到" in txt or "胜利获得" in txt):
            print("⏹ 需先拿满 3 狗牌才能翻牌，停止")
            return True
        if "获得" in txt:  # 结算成功
            print("🎉 结算完成！")
            return True
        return False

    # ⭐ 透视定向翻（2026-08-26 新增）：优红(传说) > 黄(史诗)。每色恰好 3 张，
    # 翻到第 3 张同色即触发服务端结算（返回含"获得"）。未命中透视则跳过本段走盲翻。
    persp = detect_perspective()
    if persp:
        print(f"🔮 检测到翻牌透视: {'/'.join(persp)}")
        for target, label in (("传说", "红(传说)"), ("史诗", "黄(史诗)")):
            positions = [i + 1 for i, q in enumerate(persp) if q == target]
            if len(positions) < 3:
                print(f"  ↪ 透视下{label}仅 {len(positions)} 张，无法凑 3 同色，跳过")
                continue
            print(f"→ 定向翻 {label} 3 张（位置 {positions[:3]}）")
            for pos in positions[:3]:
                s = state[pos - 1]
                if s["flipped"]:
                    print(f"  ↪ 位置 {pos} 已翻，跳过")
                    continue
                if do_flip(s):
                    return
                state = parse_gift()  # 刷新已翻状态（透视下第 3 张必结算）
            print(f"⚠️ {label} 3 张翻完仍未结算（透视可能过期/失效），改试下一色")
        print("透视定向翻未达成结算，回退普通盲翻策略\n")

    # 无透视 fallback：按序翻未翻的牌，统计追色（原策略）
    for s in state:
        if s["flipped"]:
            continue
        if do_flip(s):
            return
        # 刷新统计
        state = parse_gift()
        counts = {}
        for st in state:
            if st["flipped"] and st["color"]:
                counts[st["color"]] = counts.get(st["color"], 0) + 1
        print(f"  已翻: {counts}")
        if any(v >= 3 for v in counts.values()):
            print("🎉 达成同色 3 张！")
            return
    print("翻牌区已全部翻开但未凑齐同色（异常情况）")


def all_daily(no_refresh=False, bonus=0):
    """一键日常（按 05 §4A 顺序，逐步容错）：
    addpoint → gem(收菜+开工) → gemup → halo → wish → beach → pk → gift
    ⚠️ 2026-08-26 药水策略（用户决定）：all 默认不执行 bonus（额外奖励），药水不自动消耗。
    需用时显式带 --bonus1/--bonus2（透传给翻牌步骤 gift(bonus=...)）：
      --bonus1 → 翻牌后 c=13&id=1 耗 1 药水再领（固定 6000 贝壳+6000 经验）
      --bonus2 → 翻牌后 c=13&id=2 耗 2 药水重置狗牌+翻牌 → 重新出击 → 再翻一轮
    理由：bonus 收益固定且低、药水机会成本高（B 段 20 星沙/瓶，1 星沙≈10w 贝壳；
    重置翻牌 c=13&id=2 需 2 瓶，远期价值更高）。
    no_refresh=True 时沙滩空不自动刷新（不耗随机装备箱，供保留装备箱场景）。

    ⚠️ 顺序已知问题（2026-08-20 记录，暂不改）：
      halo（光环提升）在 gift（翻牌）之前执行，而光环天赋石(it310)的主要来源
      是翻牌结算（3 同色必给 1 颗）→ 当天翻牌拿到的石头当天用不上，要等次日。
      实测：08-20 halo 时读 0 颗跳过（翻牌还没跑），翻牌后仓库才有 1 颗。
      后续可把 halo 挪到 gift 之后（gift → halo → bonus），让当天石头当天用。
      用户决定顺序后面再调整，先在此留档。
    """
    steps = [("加点", addpoint), ("工坊收菜", gem), ("宝石提升", gemup),
             ("光环提升", halo), ("许愿池", wish),
             ("沙滩收取", lambda: beach(allow_refresh=not no_refresh,
                                         wait_after_refresh=False)),
             ("出击打野", pk), ("翻牌", lambda: gift(bonus=bonus))]
    # bonus 走 gift(bonus=...) 配置项（2026-08-26 用户设计），默认 0 不耗药水
    for name, fn in steps:
        print(f"\n{'=' * 20} [{name}] {'=' * 20}")
        try:
            fn()
        except AuthExpiredError:
            raise  # cookie 失效不继续（每步都会失败），直接抛出提示重抓
        except Exception as e:
            print(f"⚠️ [{name}] 出错: {e}，继续下一步")
    print("\n=== 今日日常完成 ===")
    stat()


def stat():
    print("=== 战场状态 ===")
    print(parse_pk())
    print("\n=== 工坊 ===")
    t = read_block(21)
    print(strip_tags(t)[:300])
    print("\n=== 翻牌区 ===")
    t = read_block(10)
    print(strip_tags(t)[:300])


def main():
    setup_logging()
    global SAFEID, USER, ZID
    USER, SAFEID = get_user_and_safeid()
    if not SAFEID or not USER:
        print("无法获取登录信息（用户名/safeid），请确认 cookie 有效")
        sys.exit(1)
    ZID = get_active_zid()
    # 登记敏感字段 → Tee 写日志时自动替换为 MD5（终端显示明文，日志文件脱敏可上传）
    add_secret(USER)
    add_secret(SAFEID)
    print(f"[用户={USER} safeid={SAFEID} 出战角色zid={ZID}]")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "stat"
    if cmd == "addpoint":
        addpoint()
    elif cmd == "gem":
        gem()
    elif cmd == "gemup":
        gemup()
    elif cmd == "halo":
        halo()
    elif cmd == "wish":
        wish()
    elif cmd == "beach":
        no_refresh = "--no-refresh" in sys.argv
        beach(allow_refresh=not no_refresh, wait_after_refresh=not no_refresh)
    elif cmd == "refresh":
        beach_refresh()
    elif cmd == "smelt":
        smelt()
    elif cmd == "warehouse_tidy":
        dry_run = "--dry-run" in sys.argv
        green_only = "--green-only" in sys.argv
        clear_beach = "--clear-beach" in sys.argv
        warehouse_tidy(dry_run=dry_run, green_only=green_only, clear_beach=clear_beach)
    elif cmd == "pk":
        full = "--full" in sys.argv
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        n = int(args[0]) if args else 20
        pk(n, full=full)
    elif cmd == "switch":
        # 切卡: switch 绮 / switch 3012 / switch（列出所有角色）
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        if args:
            arg = args[0]
            switch_card(zid=int(arg) if arg.isdigit() else None,
                        name=arg if not arg.isdigit() else None)
        else:
            switch_card()
    elif cmd == "gift":
        bonus = 1 if "--bonus1" in sys.argv else (2 if "--bonus2" in sys.argv else 0)
        gift(bonus=bonus)
    elif cmd == "bonus":
        bonus()
    elif cmd == "all":
        no_refresh = "--no-refresh" in sys.argv
        bonus = 1 if "--bonus1" in sys.argv else (2 if "--bonus2" in sys.argv else 0)
        all_daily(no_refresh=no_refresh, bonus=bonus)
    else:
        stat()


if __name__ == "__main__":
    try:
        main()
    except AuthExpiredError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
