# -*- coding: utf-8 -*-
"""翻牌 + 药水 bonus（2026-09-10 拆分自主文件）。"""
import re

from ggzlib.http import read_block, click, strip_tags, show


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
            from ggzlib.battle import pk
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

    state_cards = parse_gift()
    print("翻牌区状态:")
    for s in state_cards:
        mark = f"{s['color']}" if s["flipped"] and s["color"] else ("未翻" if not s["flipped"] else "已翻")
        print(f"  {s['pos']}. {s['name']} [{mark}]")

    # 统计已翻颜色
    counts = {}
    for s in state_cards:
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
                s = state_cards[pos - 1]
                if s["flipped"]:
                    print(f"  ↪ 位置 {pos} 已翻，跳过")
                    continue
                if do_flip(s):
                    return
                state_cards = parse_gift()  # 刷新已翻状态（透视下第 3 张必结算）
            print(f"⚠️ {label} 3 张翻完仍未结算（透视可能过期/失效），改试下一色")
        print("透视定向翻未达成结算，回退普通盲翻策略\n")

    # 无透视 fallback：按序翻未翻的牌，统计追色（原策略）
    for s in state_cards:
        if s["flipped"]:
            continue
        if do_flip(s):
            return
        # 刷新统计
        state_cards = parse_gift()
        counts = {}
        for st in state_cards:
            if st["flipped"] and st["color"]:
                counts[st["color"]] = counts.get(st["color"], 0) + 1
        print(f"  已翻: {counts}")
        if any(v >= 3 for v in counts.values()):
            print("🎉 达成同色 3 张！")
            return
    print("翻牌区已全部翻开但未凑齐同色（异常情况）")
