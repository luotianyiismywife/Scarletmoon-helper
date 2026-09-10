# -*- coding: utf-8 -*-
"""工坊 / 宝石提升 / 光环 / 许愿池 / 商店 / 道具栏（2026-09-10 拆分自主文件）。"""
import re

from ggzlib import state
from ggzlib.http import request, dec, read_block, click, strip_tags, show

# ===== 许愿池策略配置（2026-08-20）=====
# "combo"（默认）: 攒够 300 万贝壳 → c=18&id=10 十连（送 1 次 = 11 次）；<300 万不抽攒着
# "plan"        : 合理规划（每天限一次许愿操作）：
#                   ≥300 万 → 只做一次 10 连（11 次，最划算；600 万也只抽一次，剩的明天抽）
#                   <300 万 → 按剩余贝壳抽 1-9 次（270 万 = 9 次；每天一次机会不浪费）
WISH_MODE = "combo"

# ===== 商店策略配置（2026-09-07，B 段开放商店，接口实测见 02 文档 §3.2b）=====
# "daily"（默认）: 只买日限 10W 贝壳（c=5，1 星沙=10w 贝壳最优价），其余不自动买
# "full"        : 日限(c=5) → 批量清仓(c=4，50 星沙=100w，仅日限价 1/5) → 剩≥20 星沙买 1 瓶体能药水(c=7)
# ⚠️ 批量兑换太亏（2w/粒 vs 日限 10w/粒）；需要更多贝壳/药水请手动跑 `shop --full` 或网页买
SHOP_MODE = "daily"

# ===== 工坊目标成功率配置（2026-08-28）=====
# 概率型道具（随机装备箱/灵魂药水/宝石原石）面板留档时额外输出
# "预计 X 分钟（折合小时分钟）到 N%"，N 即此配置，默认 100%。
GEM_TARGET_PCT = 100

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
    """打印工坊面板摘要（各栏角色等级/宝石数/效率），供日志留档与收益核算。"""
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

    单次许愿 30 万贝壳；10 连 = 300 万送 1 次（11 次，游戏内明示，02 文档 §4.5）。
    """
    t = read_block(19)
    fields = t.strip().split("#")
    if len(fields) >= 3 and fields[2] != "0":
        print(f"今日已许愿 {fields[2]} 次，跳过")
        return
    # 主页贝壳
    home = dec(request(state.BASE + "/fyg_index.php"))
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


def shop_click(c, **params):
    """商店动作：POST fyg_shop_click.php c=<值>&safeid=（02 文档 §3.2b）。"""
    params["c"] = c
    params["safeid"] = state.SAFEID
    return dec(request(state.BASE + "/fyg_shop_click.php", params))


def shop(full=False):
    """[2] 商店日限（B 段开放；2026-09-07 接口实测见 02 文档 §3.2b）。

    策略：先领免费 BVIP 打卡包（c=11，不吃段位门禁），再买日限 10W 贝壳（c=5）；
    其余商品不自动买。⚠️ c=11 成功文案同时含"已获得"和"每日限1次"，解析必须先判"已获得"。
    """
    # 0) 免费日限 BVIP 打卡包（2026-09-09 实测 c=11：得 1星沙+2W贝壳！）
    r = shop_click(11)
    if "已获得" in r:
        print(f"c=11 BVIP打卡包(免费): {strip_tags(r)[:80]}")
    elif "每日限1次" in r:
        print("c=11 BVIP打卡包: 今日已领过（零消耗）")
    else:
        print(f"c=11 BVIP打卡包: {strip_tags(r)[:80] or '(空返回)'}")

    # 段位检测：未到 B 打一行日志就跳过（用户要求，省请求）
    rank = str(parse_pk_rank())
    if rank.startswith("C"):
        print(f"当前段位: {rank} → 未到 B，商店（星沙购买项）未开放，跳过")
        return
    page = dec(request(state.BASE + "/fyg_shop.php"))
    if "zshopts" not in page:
        print(f"当前段位: {rank or '?'} → 商店页返回 shop_err（未开放），跳过")
        return
    print(f"当前段位: {rank} → 商店开放，执行")

    def xs_now():
        # 资源栏是 AJAX 动态加载：POST f=16 才有（2026-09-07 实测）
        m = re.search(r"我的星沙\s*(\d+)\s*颗", read_block(16))
        return int(m.group(1)) if m else 0

    xs = xs_now()
    mode = "full" if full else SHOP_MODE
    print(f"星沙: {xs} | 模式: {mode}")

    # 1) 日限 10W 贝壳（每天 1 次，1 星沙=10w 最优价）
    r = shop_click(5)
    if "已获得" in r:
        xs -= 1
        print(f"c=5 日限10W贝壳: 已获得 100000 贝壳（剩 {xs} 星沙）")
    elif "每日限1次" in r:
        print("c=5 日限10W贝壳: 今日已买过（零消耗）")
    elif "不足" in r:
        print("c=5 日限10W贝壳: 星沙不足，跳过")
        return
    else:
        print(f"c=5 日限10W贝壳: {strip_tags(r)[:100] or '(空返回)'}")

    if mode != "full":
        print(f"剩余星沙: {xs}（daily 模式到此为止，批量/药水不自动买）")
        return

    # 2) full 模式：批量清仓（50 星沙=100w，2w/粒）
    while True:
        xs = xs_now()
        if xs < 50:
            break
        r = shop_click(4)
        print(f"c=4 100W贝壳(批量): {strip_tags(r)[:80] or '(空返回)'}")
        if "已获得" not in r:
            break

    # 3) 剩 ≥20 买 1 瓶体能药水（每日限 4 瓶，full 只补 1 瓶）
    if xs >= 20:
        r = shop_click(7)
        print(f"c=7 体能药水: {strip_tags(r)[:80] or '(空返回)'}")
    print(f"剩余星沙: {xs_now()}")


def parse_pk_rank():
    """读战场段位（供 shop 段位门禁）。延迟 import 防 battle↔workshop 循环。"""
    from ggzlib.battle import parse_pk
    return parse_pk().get("段位", "?")


def get_items():
    """读取道具栏持有量（f=7 武器装备区，2026-08-13 实测）。

    icon 文件名: it001药水/it002锻造箱/it003灵魂药水/it004随机装备箱/it005宝石原石/
                 it301蓝锻造石/it302绿锻造石/it310光环天赋石/it309苹果核
    返回 {道具id: 数量}，如 {'it005': 6, 'it004': 5, 'it310': 1}
    """
    html_text = read_block(7)
    result = {}
    for btn in re.findall(r"<button[^>]*>.*?</button>", html_text, re.S):
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

    策略（2026-08-14 用户确认）：
      - B 段以下（C/CC/CCC）：只升 梦石 > 红石 > 银石（梦石优先 = 复利）
      - B 段及以上：6 石种全开（金石排最后观察）
      - 排序：按 拥有量/上限 比例升序（比例低优先）
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
    menu = dec(request(state.BASE + "/fyg_menu.php", {"m": 6}))
    own = {}
    for sid, (cap, name) in caps.items():
        m = re.search(re.escape(name) + r"\s*已拥有(\d+)", menu)
        own[sid] = int(m.group(1)) if m else 0
    print(f"当前宝石拥有量: " + ", ".join(f"{name}{own[sid]}/{cap}" for sid, (cap, name) in caps.items()))

    # 段位判定：B 段以下只升梦/红/银（2026-08-14 用户确认）
    rank = parse_pk_rank().strip()
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
    """[1.5b] 提升光环：读装备页光环天赋石(it310)持有量 → c=29 提升。

    实测（2026-08-13）：每次消耗 1 枚光环天赋石，光环值 +（274 前每颗+0.05，280 后衰减）。
    ⚠️ 光环内容由 JS AJAX 动态加载（f=5），静态 GET fyg_equip.php 只返回页面外壳。
    """
    items = get_items()
    stones = items.get("it310", 0)
    print(f"光环天赋石持有: {stones}")
    if stones <= 0:
        print("无光环天赋石，跳过提升光环")
        return
    halo_html = dec(request(state.BASE + "/fyg_read.php", data="f=5"))
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
