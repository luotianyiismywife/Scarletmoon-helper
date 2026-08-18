# -*- coding: utf-8 -*-
"""咕咕镇日常执行脚本（按 docs/咕咕镇-新争夺资料/05-脚本开发.md §4A 流程）。

用法:
    python tools/ggz_daily.py stat          # 汇总状态（战场/工坊/翻牌）
    python tools/ggz_daily.py addpoint      # [0.5] 加点（自动分配剩余点）
    python tools/ggz_daily.py gem           # [1] 工坊收菜+开工（加工中→收工→自动重开）
    python tools/ggz_daily.py gemup         # [1.5] 提升宝石（B以下只升梦>红>银；比例低优先）
    python tools/ggz_daily.py halo          # [1.5b] 提升光环（读光环天赋石持有量→c=29）
    python tools/ggz_daily.py wish          # [3] 许愿池（贝壳≥30w 且今日未许愿）
    python tools/ggz_daily.py beach         # [4] 沙滩收取+清理（4.5 规则；空且有箱→自动刷新；--no-refresh 禁用自动刷新不耗箱）
    python tools/ggz_daily.py refresh       # [4.5] 强制刷新沙滩（耗随机装备箱）
    python tools/ggz_daily.py smelt         # [4.5c] 熔炼仓库可熔炼装备为护身符（手动）
    python tools/ggz_daily.py pk [n]        # [5] 出击打野（默认 3 狗牌停；[--full] 打满 n 次）
    python tools/ggz_daily.py gift          # [6] 翻牌（无透视策略，3 同色结算）
    python tools/ggz_daily.py bonus         # [7] 额外奖励（耗 1 体能刺激药水）
    python tools/ggz_daily.py all           # 一键日常（以上全部按序执行）

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

sys.stdout.reconfigure(encoding="utf-8")

try:
    import requests
except ImportError:
    print("[错误] 缺少 requests 库，请先安装: pip install requests")
    sys.exit(1)

BASE = "https://www.momozhen.com"


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
    """

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
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
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "ggz_%s.log" % datetime.date.today().strftime("%Y%m%d"))
    log_fp = open(log_path, "a", encoding="utf-8")
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

# requests.Session：连接池 + keep-alive 复用连接，规避 urllib 每次新建 TLS
# 握手被服务器限流（SSL 断开/返回空）的问题（2026-08-16 实测，05 文档 §4.5）
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
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
            if "重新登录".encode("utf-8") in resp.content:
                raise AuthExpiredError(
                    "咕咕镇 cookie 已失效（浏览器登录顶掉/每日刷新），"
                    "请刷新游戏 cookie 后重跑 tools/get_cookies.py --game")
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
    m = re.search(r"xxcard\((\d+)\)[^>]*>.*?\((出战中)\)", t, re.S)
    return int(m.group(1)) if m else None


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

def addpoint():
    """[0.5] 加点：读取 f=18 六维，把剩余点分配（主属性力量→60%上限，副属性体/意 1:1）"""
    t = read_block(18, zid=ZID)
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
    if remain <= 0:
        print("无需加点")
        return

    # 策略：力量堆到 60% 上限，剩余体/意 1:1
    cap = int(total * 0.6)
    plan = dict(six)
    force = min(six["力量"] + remain, cap)
    plan["力量"] = force
    left = remain - (force - six["力量"])
    # 剩余按 体/意 1:1
    half = left // 2
    plan["体魄"] = six["体魄"] + half
    plan["意志"] = six["意志"] + (left - half)
    if left % 2:
        plan["意志"] += 0
    print(f"加点方案: {plan}")
    r = click(2, id=ZID,
              add01=plan["力量"], add02=plan["敏捷"], add03=plan["智力"],
              add04=plan["体魄"], add05=plan["精神"], add06=plan["意志"])
    show("c=2 加点返回", r)


def gem():
    """[1] 工坊收菜：加工中 → 收工拿收益 → 重新开工；未加工 → 开工。

    c=30 为收工/开工切换（同按钮）。收工返回收益统计，实测收工后自动重新开工，
    但 8-12 出现过开工状态丢失（隔天变"开始加工"），故收工后检查、未自动开工则手动开工。
    """
    t = read_block(21)
    if "收工" in t:
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
        elif "收工" in t2:
            print("✅ 收工完成，工坊仍在加工中（异常）")
        else:
            print("✅ 收工完成，工坊已自动重新开工")
    elif "开始加工" in t:
        print("工坊未加工 → 开工...")
        r = click(30)
        show("c=30 开工返回", r)
    else:
        print("⚠️ 未知工坊状态: " + strip_tags(t)[:200])


def wish():
    """[3] 许愿池：f=19 判断今日是否已许愿 + 主页贝壳≥30w → c=18 许愿 1 次。"""
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
    if coins >= 300000:
        r = click(18, id=1)
        if "已经许愿" in r or "请明天" in r:
            # 服务器权威判定已许愿（f=19 fields[2] 不可靠，2026-08-16 实测）
            print("今日已许愿（服务器确认），跳过")
        else:
            show("c=18 许愿返回", r)
    else:
        print("贝壳 < 30w，跳过许愿")


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
    halo_html = dec(request(BASE + "/fyg_equip.php?eid=5"))
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


def parse_equips(html, want_id=False):
    """解析装备按钮列表（f=1 沙滩 / f=6 身上通用）。

    每个装备按钮结构（2026-08-12 实测 f=6）：
      <button ... data-content="<p class='fyg_xlxxXXX'>词条名 +N<span class='pull-right bg-*'>&nbsp;150%&nbsp;</span></p>..."
              title="Lv.<span>100</span> 装备名" ...><img src="ys/icon/z2101_4.gif">...
    沙滩版额外含 zbtip('ID','4')。
    返回 [{icon, quality, name, level, total, mystery, bid, n_affix, has_orange, has_red, has_high}]
      icon: 部位码 zXXXX；quality: 品质数字；total: 词条总值(% 之和)；bid: 沙滩拾取 id
      has_orange/has_red: 橙/红词条；has_high: 含高价值词条
    """
    HIGH_AFFIX = ["生命偷取", "附加物伤", "附加魔伤", "附加物穿", "附加魔穿",
                  "技能概率", "暴击概率", "攻击速度"]
    result = []
    for btn in re.findall(r"<button[^>]*>.*?</button>", html, re.S):
        if "ys/icon/z" not in btn:
            continue
        # icon: background-image:url(ys/icon/z/z2402_2.gif)（品质后缀 _2）
        # ⚠️ 真实路径 icon/ 后带一层 z/ 子目录（2026-08-14 实测）；(?:/z)? 兼容新旧两种写法
        m = re.search(r"ys/icon/z(?:/z)?(\d{4})(?:_(\d))?\.gif", btn)
        icon, quality = (m.group(1), int(m.group(2)) if m and m.group(2) else 0) if m else ("", 0)
        # title: Lv.<span>100</span> <span>星级</span><br>装备名（f=1 沙滩）
        #       Lv.<span class='fyg_f18'>100</span> 装备名（f=6 身上，无 <br>，2026-08-16 实测）
        m = re.search(r'title="Lv\.<span[^>]*>(\d+)</span>[\s\S]*?(?:<br|</span>)([^"<]*?)(?:"|$)', btn)
        if m:
            level, name = m.group(1), m.group(2).strip()
        else:
            # f=6 身上: title="Lv.<span class='fyg_f18'>100</span> 探险者之剑"
            m2 = re.search(r'</span>\s*([^"<]+?)"', btn)
            level, name = ("?", m2.group(1).strip()) if m2 else ("?", "?")
        total = 0.0
        # 词条颜色统计（2026-08-13 实测 class：danger=红 warning=橙 info=蓝 primary=紫 success=绿）
        has_orange = False
        has_red = False
        has_high = False
        n_affix = 0
        # 每词条: <p class='fyg_xlxxXXX'>名称 +N<span class='pull-right bg-XXX'>&nbsp;N%&nbsp;</span></p>
        for m in re.finditer(r"<p class='fyg_xlxx\w+'>(.*?)</p>", btn, re.S):
            affix_html = m.group(1)
            # 词条名称: <p> 后到 <span 前的文本（如 "附加物伤 +1290"）
            name_m = re.match(r"\s*([^<\s]+)", affix_html)
            affix_name = name_m.group(1) if name_m else ""
            # 百分比
            val_m = re.search(r"pull-right (bg-\w+)[^>]*>(?:&nbsp;|\s)*(\d+(?:\.\d+)?)%", affix_html)
            if not val_m:
                continue
            color, val = val_m.group(1), float(val_m.group(2))
            total += val
            n_affix += 1
            if color == "bg-warning":
                has_orange = True
            elif color == "bg-danger":
                has_red = True
            if any(kw in affix_name for kw in HIGH_AFFIX):
                has_high = True
        mystery = "[神秘属性]" in btn or "神秘属性" in btn
        m = re.search(r"zbtip\('(\d+)','4'\)", btn)
        bid = m.group(1) if m else None
        result.append({"icon": icon, "quality": quality, "name": name,
                       "level": level, "total": total, "mystery": mystery, "bid": bid,
                       "n_affix": n_affix, "has_orange": has_orange, "has_red": has_red,
                       "has_high": has_high})
    return result


def equip_decision(it, worn):
    """沙滩装备决策（05 §4.5 规则，2026-08-13 更新）。返回 'take' / 'clear'。

    收取条件（并集，满足任一即收）：
      ① 橙装（总值≥516%）→ 无脑收（备其他角色）
      ② 含神秘 → 必收
      ③ 能熔炼（品质≥3 且总值≥410% 且无神秘 且非橙装）→ 收（供手动熔炼，长期规则）
      ④ 红/橙词条(bg-danger/bg-warning)：
         含高价值词条 → 四词条总数≥450% 无脑收
         无高价值词条 → 四词条总数≥500% 无脑收
      ⑤ 同部位对比：沙滩总值 > 身上同部位 → 收；无同部位（空部位）→ 收
      ⑥ 其余 → 清
    """
    # ① 橙装
    if it["total"] >= 516:
        return "take"
    # ② 神秘
    if it["mystery"]:
        return "take"
    # ③ 能熔炼：品质≥3 且 总值≥410%（熔炼线 2026-08-10 实测），留供手动熔炼
    if it["quality"] >= 3 and it["total"] >= 410:
        return "take"
    # ④ 红/橙词条：有高价值→450，无高价值→500（2026-08-13 用户指定）
    if it["has_orange"] or it["has_red"]:
        threshold = 450 if it["has_high"] else 500
        if it["total"] >= threshold:
            return "take"
    # ⑤ 同部位对比：icon 数字前 2 位 = 部位类（21武器/22手环/23衣服/24饰品）
    # ⚠️ 2026-08-16 实测修复：旧代码 icon[:3] 把武器内部子类(2101/2111)当不同部位，
    #    导致 211x 武器 vs 身上 210x 武器误判“空部位”→ 白板/蓝装武器全入仓。
    slot = it["icon"][:2] if len(it["icon"]) >= 2 else ""
    same = [w for w in worn if w["icon"][:2] == slot]
    if not same:
        return "take"  # 空部位直接收
    best = max(w["total"] for w in same)
    return "take" if it["total"] > best else "clear"


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
      - 返回 <20 字符 → 大概率空响应/限流，重试
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
        # 空响应/极短返回：大概率限流，等待后重试
        if len(raw) < 20:
            print(f"  ⚠️ f=1 返回异常短（{len(raw)} 字符），疑似限流，{interval}s 后重试 ({attempt+1}/{retries})")
            time.sleep(interval)
            continue
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
    # 重试耗尽
    print(f"  ⚠️ f=1 重试 {retries} 次仍无法读取沙滩（最后返回 {len(last_raw)} 字符: {last_raw[:120]!r}）")
    return [], last_raw


def beach(allow_refresh=True, wait_after_refresh=True):
    """[4] 沙滩收取 + 清理（4.5 规则）。

    流程：读 f=1 沙滩 + f=6 身上 → 逐件决策 → 先 c=1 拾取要收的 → 再 c=20 清理剩余。
    沙滩空但有随机装备箱 → 自动强制刷新（c=12）再筛（allow_refresh=False 时跳过，
    供 beach_refresh 调用避免二次刷新白耗箱子）。
    wait_after_refresh=False：沙滩空时直接跳过不等待（--no-refresh 场景，
    保留装备箱供测试，不耗 c=12）。

    ⚠️ 踩坑记录（2026-08-18 重写）：
      1. f=1 返回空/异常 ≠ 沙滩空：旧代码不区分"读取失败"和"真空"，
         限流返回空 body 时误判沙滩空 → 跳过清理。现在 _read_beach() 带重试验证。
      2. 会话失效（"请重新登录"）：cookie 被浏览器顶掉时 f=1 返回 9 字符提示，
         旧代码当"空"处理。现在直接 raise 提示重抓 cookie。
      3. 沙滩 id 拾取后重排：逐件拾取后剩余 id 全变，禁止复用旧 id。
      4. c=12 刷新后空窗期 ~64s：45×2s=90s 重试窗口覆盖。
      5. 限流：momozhen 对连续请求限流（返回空/SSL 断开），requests.Session 已缓解，
         但高频场景仍需间隔 ≥2s。
    """
    items, raw = _read_beach()
    if not items:
        # 确认是真空（_read_beach 已排除限流/会话失效）
        # ⚠️ 沙滩空 → c=12 自动刷新（2026-08-16 实测改版）：
        # c=12 后 f=1 空窗期实测仅 ~64 秒（探针 2s 轮询：+64s 读到 10 件），
        # 旧代码 12 秒重试窗口不够 → 误判"读不到"。重试窗口改为 45×2s=90s 覆盖。
        # （2026-08-14 曾误判空窗几秒→无效修复；08-16 又误判 10 分钟→过度禁止，
        #   本次探针实测纠正：64 秒即可。）
        boxes = get_items().get("it004", 0)
        print(f"沙滩空，无装备（随机装备箱持有 {boxes}）")
        if allow_refresh and boxes > 0:
            print("→ 有装备箱，自动强制刷新沙滩...")
            r = click(12)
            show("c=12 刷新沙滩返回", r)
        elif allow_refresh:
            print("→ 无随机装备箱，跳过")
            return
        elif not wait_after_refresh:
            print("→ 用户指定不刷新（--no-refresh），跳过沙滩（保留装备箱）")
            return
        else:
            print("→ 刚刷新过（allow_refresh=False），等待空窗期过去...")
        # 空窗期 ~64s：45 次 × 2s = 90s，覆盖空窗 + 限流重试
        for attempt in range(45):
            time.sleep(2)
            items, raw = _read_beach(retries=1)  # 轮询中单次读取即可
            if items:
                print(f"  刷新后第 {attempt + 1} 次（{(attempt + 1) * 2}s）读取到 {len(items)} 件装备")
                break
        else:
            print("  ⚠️ 刷新后 90s 仍未读到装备（可能服务器延迟/限流，可稍后重跑 beach）")
            return
    worn = parse_equips(read_block(6))
    print(f"沙滩 {len(items)} 件，身上 {len(worn)} 件")
    for it in items:
        mark = []
        if it["has_orange"]:
            mark.append("橙")
        if it["has_red"]:
            mark.append("红")
        if it["mystery"]:
            mark.append("神秘")
        mark_str = f" 词条[{'/'.join(mark)}]" if mark else ""
        print(f"  {it['name']} {it['quality']}等 {it['total']:.0f}% icon={it['icon']} id={it['bid']}{mark_str}")

    take_ids, clear_count = [], 0
    for it in items:
        if it["bid"] is None:
            continue
        if equip_decision(it, worn) == "take":
            take_ids.append(it["bid"])
        else:
            clear_count += 1
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
    # f=2 仓库装备 id 在 zbtip('id','3')，需补充解析
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


def beach_refresh():
    """[4.5] 强制刷新沙滩（耗 1 随机装备箱，c=12）→ 刷新后按 4.5 规则再筛一轮

    ⚠️ 2026-08-16：beach 已恢复自动刷新，这里先 c=12 再调 beach 会二次刷新白耗箱子，
    故传 allow_refresh=False 让 beach 只等空窗期不重复刷。
    """
    boxes = get_items().get("it004", 0)
    print(f"随机装备箱持有: {boxes}")
    if boxes <= 0:
        print("无随机装备箱，跳过强制刷新")
        return
    r = click(12)
    show("c=12 刷新沙滩返回", r)
    beach(allow_refresh=False, wait_after_refresh=True)


def fight(target=1):
    """出击一次，返回 (结果类型, 原始文本)。fyg_v_intel.php 需带 safeid！"""
    r = dec(request(BASE + "/fyg_v_intel.php", {"id": target, "safeid": SAFEID}))
    if f"{USER} 获得了胜利！" in r:
        return "win", r
    if "双方同归于尽" in r:
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

    策略（2026-08-17 用户改版）：**优先打人**（id=2，胜利 +3% 进度），
    打不过（lose）或轮空（retry）再切打野（id=1，胜利 +1%）。
    打野失败 → 切回打人继续循环，直到拿满 3 狗牌或打满 max_fights 次
    （不再"打野失败即停止"——2026-08-16 旧策略已废弃，用户指定
     目标 = 3 狗牌或 20 次）。
    """
    # 当前模式: pvp(打人) / pve(打野)。开局先试打人。
    mode = "pvp"
    for i in range(1, max_fights + 1):
        st = parse_pk()
        print(f"\n--- 出击 #{i} 前状态: 段位{st['段位']} {st['进度']} 狗牌{st['狗牌']}/{st['出击']} 连胜{st['连胜']} 连败{st['连败']} 模式={mode} ---")
        if not full and int(st["狗牌"]) >= 3:
            print("✅ 已拿满 3 狗牌，停止出击（--full 可打满）")
            break
        # 模式决策: 打人失败/轮空 → 切打野; 打野失败 → 切回打人（循环到 3 狗牌/20 次）
        target = 2 if mode == "pvp" else 1
        kind, r = fight(target)
        target_name = "打人" if target == 2 else "打野"
        print(f"出击结果: {kind}（{target_name}）")
        if kind == "limit":
            print("出击次数达上限")
            break
        if kind == "retry" or kind == "draw":
            # 不计次数。打人轮空/平局 → 切打野; 打野轮空/平局 → 保持(继续试)
            if target == 2:
                print("  ↪ 打人轮空/平局，切打野")
                mode = "pve"
            continue
        if kind == "lose":
            if target == 2:
                print("  ↪ 打人失败，切打野")
                mode = "pve"
            else:
                print("  ↪ 打野失败，切回打人（继续打到 3 狗牌/20 次）")
                mode = "pvp"
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


def gift():
    """[6] 翻牌（2026-08-12 实测机制）：
    - f=10 每张牌一个按钮：有 giftop(N) onclick = 未翻；btn-info/success/warning/danger = 已翻
    - 品质→颜色：btn-info=幸运(蓝) btn-success=稀有(绿) btn-warning=史诗(黄) btn-danger=传说(红)
    - c=8 翻牌：成功直接返回结算文本（含"获得"）；"该牌面已翻开" = 该张已翻
    - 策略：统计已翻颜色，某色 3 张即结算停止；否则按序翻未翻牌追色
    """
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

    # 按序翻未翻的牌
    for s in state:
        if s["flipped"]:
            continue
        r = click(8, id=s["pos"])
        txt = strip_tags(r)
        print(f"\n翻牌 #{s['pos']} ({s['name']}): {txt[:120]}")
        # ⚠️ 拒翻判定先于结算判定：未拿满 3 狗牌时返回
        # "请先在争夺战场拿到3枚狗牌，狗牌在PVP/PVE胜利获得"（含"获得"但非结算，
        # 2026-08-17 实测踩坑）。结算文本才含"获得"。
        if "狗牌" in txt and ("请先" in txt or "拿到" in txt or "胜利获得" in txt):
            print("⏹ 需先拿满 3 狗牌才能翻牌，停止")
            return
        if "获得" in txt:  # 结算成功
            print("🎉 结算完成！")
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


def all_daily(no_refresh=False):
    """一键日常（按 05 §4A 顺序，逐步容错）：
    addpoint → gem(收菜+开工) → gemup → halo → wish → beach → pk → gift → bonus
    no_refresh=True 时沙滩空不自动刷新（不耗随机装备箱，供保留装备箱场景）。
    """
    steps = [("加点", addpoint), ("工坊收菜", gem), ("宝石提升", gemup),
             ("光环提升", halo), ("许愿池", wish),
             ("沙滩收取", lambda: beach(allow_refresh=not no_refresh,
                                         wait_after_refresh=False)),
             ("出击打野", pk), ("翻牌", gift), ("额外奖励", bonus)]
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
    elif cmd == "pk":
        full = "--full" in sys.argv
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        n = int(args[0]) if args else 20
        pk(n, full=full)
    elif cmd == "gift":
        gift()
    elif cmd == "bonus":
        bonus()
    elif cmd == "all":
        all_daily(no_refresh="--no-refresh" in sys.argv)
    else:
        stat()


if __name__ == "__main__":
    try:
        main()
    except AuthExpiredError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
