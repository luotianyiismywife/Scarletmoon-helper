# -*- coding: utf-8 -*-
"""咕咕镇日常执行脚本（按 docs/咕咕镇资料/05-脚本开发.md §4A 流程）。

用法:
    python tools/ggz_daily.py stat          # 汇总状态（战场/工坊/翻牌）
    python tools/ggz_daily.py addpoint      # [0.5] 加点（自动分配剩余点）
    python tools/ggz_daily.py gem           # [1] 工坊收菜+开工（加工中→收工→自动重开）
    python tools/ggz_daily.py gemup         # [1.5] 提升宝石（读道具栏原石持有量→按上限比例低优先）
    python tools/ggz_daily.py halo          # [1.5b] 提升光环（读光环天赋石持有量→c=29）
    python tools/ggz_daily.py wish          # [3] 许愿池（贝壳≥30w 且今日未许愿）
    python tools/ggz_daily.py beach         # [4] 沙滩收取+清理（4.5 规则；空且有箱→自动刷新）
    python tools/ggz_daily.py refresh       # [4.5] 强制刷新沙滩（耗随机装备箱）
    python tools/ggz_daily.py pk [n]        # [5] 出击打野（默认 3 狗牌停；[--full] 打满 n 次）
    python tools/ggz_daily.py gift          # [6] 翻牌（无透视策略，3 同色结算）
    python tools/ggz_daily.py bonus         # [7] 额外奖励（耗 1 体能刺激药水）
    python tools/ggz_daily.py all           # 一键日常（以上全部按序执行）

依赖: cookie.txt（tools/get_cookies.py --login 或提取生成）
"""
import os
import re
import sys
import time
import urllib.request
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://www.momozhen.com"


def load_cookie():
    path = os.path.join(os.path.dirname(__file__), "..", "cookie.txt")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


COOKIE = load_cookie()
USER = None   # 动态: 主页提取
ZID = None    # 动态: f=8 出战中角色


def request(url, data=None, retries=3, xhr=True):
    """HTTP 请求，带重试（momozhen SSL 偶发断开）。

    xhr=True 时带 X-Requested-With: XMLHttpRequest 头——服务器对带此头的请求
    返回 JS 动态加载的内容（如装备页道具栏），静态请求拿不到（2026-08-13 实测）。
    """
    body = urllib.parse.urlencode(data).encode() if data else None
    last_err = None
    for attempt in range(retries):
        try:
            headers = {
                "Cookie": COOKIE,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
                "Referer": BASE + "/fyg_index.php",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            if xhr:
                headers["X-Requested-With"] = "XMLHttpRequest"
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            print(f"  ⚠️ 请求重试 {attempt + 1}/{retries}: {e}")
            time.sleep(2)
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
    策略（用户指定）：数量越大成功率越低 → 优先提升上限比例低的
    （比例=拥有量/上限），比例相同时按 1红2银3金4梦5虚6幻。
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

    # 按上限比例升序（比例低优先），比例相同按 id 顺序
    order = sorted(caps.keys(), key=lambda sid: (own[sid] / caps[sid][0], int(sid)))
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
      <button ... data-content="<p>词条<span class='pull-right bg-*'>&nbsp;150%&nbsp;</span></p>..."
              title="Lv.<span>100</span> 装备名" ...><img src="ys/icon/z2101_4.gif">...
    沙滩版额外含 zbtip('ID','4')。
    返回 [{icon, quality, name, level, total, mystery, bid}]
      icon: 部位码 zXXXX；quality: 品质数字；total: 词条总值(% 之和)；bid: 沙滩拾取 id
    """
    result = []
    for btn in re.findall(r"<button[^>]*>.*?</button>", html, re.S):
        if "ys/icon/z" not in btn:
            continue
        # icon: background-image:url(ys/icon/z/z2402_2.gif)（品质后缀 _2）
        m = re.search(r"ys/icon/z(\d{4})(?:_(\d))?\.gif", btn)
        icon, quality = (m.group(1), int(m.group(2)) if m and m.group(2) else 0) if m else ("", 0)
        # title: Lv.<span>100</span> <span>星级</span><br>装备名
        m = re.search(r'title="Lv\.<span[^>]*>(\d+)</span>[\s\S]*?(?:<br|</span>)([^"<]*?)(?:"|$)', btn)
        level, name = (m.group(1), m.group(2).strip()) if m else ("?", "?")
        total = 0.0
        for m in re.finditer(r"pull-right bg-\w+[^>]*>(?:&nbsp;|\s)*(\d+(?:\.\d+)?)%", btn):
            total += float(m.group(1))
        mystery = "[神秘属性]" in btn or "神秘属性" in btn
        m = re.search(r"zbtip\('(\d+)','4'\)", btn)
        bid = m.group(1) if m else None
        result.append({"icon": icon, "quality": quality, "name": name,
                       "level": level, "total": total, "mystery": mystery, "bid": bid})
    return result


def equip_decision(it, worn):
    """沙滩装备决策（05 §4.5 规则简化版）。返回 'take' / 'clear'。

    ① 橙装（总值≥516%）→ 收（备其他角色）
    ② 含神秘 → 必收
    ③ 品质≥3 → 收（可熔炼为护身符）
    ④ 同部位对比（icon 前 3 位）：沙滩总值 > 身上同部位 → 收；无同部位 → 收
    ⑤ 其余 → 清
    """
    if it["total"] >= 516:
        return "take"
    if it["mystery"]:
        return "take"
    if it["quality"] >= 3:
        return "take"
    # 同部位：icon 前 3 位（z21x武器/z22x手环/z23x衣服/z24x饰品）
    slot = it["icon"][:3] if len(it["icon"]) >= 3 else ""
    same = [w for w in worn if w["icon"][:3] == slot]
    if not same:
        return "take"  # 空部位直接收
    best = max(w["total"] for w in same)
    return "take" if it["total"] > best else "clear"


def beach():
    """[4] 沙滩收取 + 清理（4.5 规则）。

    流程：读 f=1 沙滩 + f=6 身上 → 逐件决策 → 先 c=1 拾取要收的 → 再 c=20 清理剩余。
    沙滩空但有随机装备箱 → 自动强制刷新（c=12）再筛。
    ⚠️ 沙滩 id 拾取后重排：逐件拾取后重新读 f=1 抓新 id。
    """
    t1 = read_block(1)
    items = parse_equips(t1, want_id=True)
    if not items:
        # 沙滩空 → 有装备箱则强制刷新
        boxes = get_items().get("it004", 0)
        print(f"沙滩空，无装备（随机装备箱持有 {boxes}）")
        if boxes > 0:
            print("→ 有装备箱，强制刷新沙滩...")
            r = click(12)
            show("c=12 刷新沙滩返回", r)
            t1 = read_block(1)
            items = parse_equips(t1, want_id=True)
        else:
            return
    worn = parse_equips(read_block(6))
    print(f"沙滩 {len(items)} 件，身上 {len(worn)} 件")
    for it in items:
        print(f"  {it['name']} {it['quality']}等 {it['total']:.0f}% icon={it['icon']} id={it['bid']}"
              f"{' 神秘' if it['mystery'] else ''}")

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


def beach_refresh():
    """[4.5] 强制刷新沙滩（耗 1 随机装备箱，c=12）→ 刷新后按 4.5 规则再筛一轮"""
    boxes = get_items().get("it004", 0)
    print(f"随机装备箱持有: {boxes}")
    if boxes <= 0:
        print("无随机装备箱，跳过强制刷新")
        return
    r = click(12)
    show("c=12 刷新沙滩返回", r)
    beach()


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
    """[5] 出击打野。默认拿满 3 狗牌即停；--full 打满 max_fights 次。"""
    for i in range(1, max_fights + 1):
        st = parse_pk()
        print(f"\n--- 出击 #{i} 前状态: 段位{st['段位']} {st['进度']} 狗牌{st['狗牌']}/{st['出击']} 连胜{st['连胜']} 连败{st['连败']} ---")
        if not full and int(st["狗牌"]) >= 3:
            print("✅ 已拿满 3 狗牌，停止出击（--full 可打满）")
            break
        kind, r = fight(1)
        print(f"出击结果: {kind}")
        if kind == "limit":
            print("出击次数达上限")
            break
        if kind == "retry" or kind == "draw":
            # 不计次数，继续
            continue
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


def all_daily():
    """一键日常（按 05 §4A 顺序，逐步容错）：
    addpoint → gem(收菜+开工) → gemup → halo → wish → beach → pk → gift → bonus
    """
    steps = [("加点", addpoint), ("工坊收菜", gem), ("宝石提升", gemup),
             ("光环提升", halo), ("许愿池", wish), ("沙滩收取", beach),
             ("出击打野", pk), ("翻牌", gift), ("额外奖励", bonus)]
    for name, fn in steps:
        print(f"\n{'=' * 20} [{name}] {'=' * 20}")
        try:
            fn()
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
        beach()
    elif cmd == "refresh":
        beach_refresh()
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
        all_daily()
    else:
        stat()


if __name__ == "__main__":
    main()
