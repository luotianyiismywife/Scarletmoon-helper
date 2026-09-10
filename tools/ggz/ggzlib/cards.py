# -*- coding: utf-8 -*-
"""角色卡：列表 / 切换出战 / 六维加点（2026-09-10 拆分自主文件）。"""
import re

from ggzlib import state
from ggzlib.http import read_block, click, strip_tags, show, get_active_zid

# 角色 zid 映射（2026-09-05 浏览器实测：舞=3000、绮=3012；3011 空缺=雅占位，
# 未持有雅不显示；其余按 f=8 返回动态获取。2026-09-10 f=8 全表复核：
# 3003艾 3004梦 3005薇 3006伊 3007冥 3008命——07 文档旧表曾整体错位已修）
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

    ⚠️ 切卡后只有**出击目标**跟着切；加点/装备均为账号级共享、不跟着切
    （2026-09-05 六维全卡同步 + 2026-09-10 装备 4 件切卡不变实测）。
    """
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
            mark = " (出战中)" if z == state.ZID else ""
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
        state.ZID = zid
        print(f"✅ 出战角色已切换 zid={zid}")
        return zid
    print("⚠️ 切卡返回异常:", r[:120])
    return None


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
        zid = state.ZID
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
