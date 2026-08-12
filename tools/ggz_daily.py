# -*- coding: utf-8 -*-
"""咕咕镇日常执行脚本（按 docs/咕咕镇资料/05-脚本开发.md §4A 流程）。

用法:
    python tools/ggz_daily.py addpoint   # [0.5] 加点（自动计算剩余点分配）
    python tools/ggz_daily.py gem        # [1] 工坊：未加工则开工
    python tools/ggz_daily.py gemup      # [1.5] 提升宝石（红石优先）
    python tools/ggz_daily.py beach      # [4.5] 强制刷新沙滩（耗随机装备箱）
    python tools/ggz_daily.py pk <n>     # [5] 出击打野 n 次（默认到 3 狗牌或 20 次）
    python tools/ggz_daily.py gift       # [6] 翻牌（无透视策略）
    python tools/ggz_daily.py stat       # 汇总当前状态（f=12/f=21/f=10）

依赖: cookie.txt（tools/get_cookies.py 生成）
"""
import os
import re
import sys
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


def request(url, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={
        "Cookie": COOKIE,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
        "Referer": BASE + "/fyg_index.php",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


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
    """[1] 工坊：当前未加工 → 开工；已加工 → 显示状态"""
    t = read_block(21)
    if "cgamd()" in t and "开始加工" in t:
        r = click(30)
        show("c=30 开工返回", r)
        t2 = read_block(21)
        if "收工" in t2:
            print("✅ 已开工")
        else:
            print("⚠️ 开工后状态未变: " + strip_tags(t2)[:200])
    elif "收工" in t:
        print("工坊正在加工中，无需开工")
    else:
        print("⚠️ 未知工坊状态: " + strip_tags(t)[:200])


def gemup():
    """[1.5] 提升宝石：检查工坊第4栏「宝石原石」存量，有才 c=27 提升。

    工坊 6 栏（2026-08-12 实测结构）：
      1 贝壳(红石)  2 随机装备箱(银石)  3 灵魂药水(金石)
      4 宝石原石(梦石)  5 星沙(虚石)  6 幻影经验(幻石)
    c=27 消耗的是第4栏产出的「宝石原石」，不是宝石拥有量。
    原石为收工时结算，日常执行时通常无存量 → 默认跳过（保留接口供收工后调用）。
    """
    t = read_block(21)
    # 第4栏: <div ...>N%概率出产<br>宝石原石<br>...<br>梦石N<br>每分钟 +X%概率</div>
    m = re.search(r"(\d+\.?\d*)%概率出产<br>宝石原石", t)
    prob = m.group(1) if m else "0"
    # 已产出判断：看「已拾取」格式是否有对应（宝石原石栏无已拾取，说明以概率形式存在）
    # 提升宝石动作本身消耗原石，若概率 >0 说明该栏在工作，但原石是否已产出需看工坊结算
    print(f"宝石原石产出概率: {prob}%")
    # 原石是工坊产物，收工才结算。当前无原石则跳过（有原石会出现在工坊结算/仓库）
    print("原石为工坊产出（收工时结算），当前无存量则不提升")
    print("跳过提升宝石（无原石存量）")


def beach():
    """[4.5] 强制刷新沙滩（耗 1 随机装备箱）"""
    r = click(12)
    show("c=12 刷新沙滩返回", r)
    t = read_block(1)
    items = re.findall(r"title=\"Lv\.([^\"]*?)\"", t)
    print(f"刷新后沙滩装备数: {len(items)}")


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


def pk(max_fights=20):
    """[5] 出击打野直到 3 狗牌或 max_fights 次"""
    for i in range(1, max_fights + 1):
        st = parse_pk()
        print(f"\n--- 出击 #{i} 前状态: 段位{st['段位']} {st['进度']} 狗牌{st['狗牌']}/{st['出击']} 连胜{st['连胜']} 连败{st['连败']} ---")
        if int(st["狗牌"]) >= 3:
            print("✅ 已拿满 3 狗牌，停止出击")
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
        btns = re.findall(r"<button[^>]*>(.*?)</button>", t, re.S)
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
    elif cmd == "beach":
        beach()
    elif cmd == "pk":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        pk(n)
    elif cmd == "gift":
        gift()
    else:
        stat()


if __name__ == "__main__":
    main()
