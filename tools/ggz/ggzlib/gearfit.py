# -*- coding: utf-8 -*-
"""配装推荐 × 持有查询 × 一键换装（2026-09-10 拆分自主文件）。

原理: 装备词条按**类型固定**（newkf/battle_sim 同源）→ 推荐只对"类型"；
      同名多件时取词条总分最高（品质/神秘作次序）。
依据: 07-角色属性表.md 各角色构筑；社区简称已还原全名
      （对剑=彩金长剑、冥戒=噬魔戒指、反甲=荆棘重甲、舞戒=海星戒指、
        梦头=占星师的耳饰、狂刃=狂信者的荣誉之刃、神秘袍=旅法师的灵光袍）。
槽位: 武器(0)/手环(1)/防具(2)/耳环(3)（f=6 四部位）。列表=可选替代（任一命中即可）。
"""
from ggzlib import state
from ggzlib.http import read_block, click, strip_tags
from ggzlib.rules import parse_equips
from ggzlib.cards import list_cards, CARD_ZIDS

GEAR_FIT = {
    "舞": ({"武器": ["狂信者的荣誉之刃"], "手环": ["海星戒指"],
          "防具": ["挑战斗篷"], "耳环": ["猎魔耳环"]},
         "三刀流: 刃+海戒+斗篷+猎魔，堆暴击+技能率 3刀秒怪（tid=1079827）；海星戒指=舞专属"),
    "梦": ({"武器": ["幽梦匕首"], "手环": ["秃鹫手环"],
          "防具": ["旅法师的灵光袍"], "耳环": ["占星师的耳饰"]},
         "T0 打野四件套；匕首只看34词条（伤害=被动出手），头饰只看25词条（上限90%最大护盾）"),
    "薇": ({"武器": ["彩金长剑", "荆棘盾剑"], "手环": ["噬魔戒指"],
          "防具": ["荆棘重甲"], "耳环": []},
         "对剑流(彩金长剑)/剑盾流(荆棘盾剑)；冥戒=噬魔、反甲=荆棘重甲；'薇头'对应耳环待核"),
    "伊": ({"武器": ["狂信者的荣誉之刃"], "手环": ["噬魔戒指"],
          "防具": ["荆棘重甲"], "耳环": ["猎魔耳环"]},
         "狂刃/噬魔戒/重甲/猎魔；卷攻速流（面板参考攻速 9866）"),
    "冥": ({"武器": ["荆棘盾剑"], "手环": ["噬魔戒指"],
          "防具": ["荆棘重甲"], "耳环": ["猎魔耳环"]},
         "剑盾冥毕业: 盾剑+噬魔(神秘=命运链接)+重甲(神秘=+25%反弹)+猎魔；PVP 强势/打野下水道"),
    "默": ({"武器": ["荆棘盾剑"], "手环": ["海星戒指"],
          "防具": ["旅法师的灵光袍"], "耳环": ["占星师的耳饰"]},
         "全精默: 神秘剑盾/舞戒(海星)/神秘袍/梦头(占星师)"),
    "命": ({"武器": ["彩金长剑"], "手环": ["折光戒指"],
          "防具": ["荆棘重甲"], "耳环": []},
         "对剑命=全敏捷: 对剑(彩金长剑)+折光+反甲；神弓命能上 S；'薇头'待核"),
    "霞": ({"武器": [], "手环": [],
          "防具": ["旅法师的灵光袍"], "耳环": ["占星师的耳饰"]},
         "袍霞: 神秘霞杖+手环对应装备待核；PVP 输出流 1300智900精800敏"),
    "希": ({"武器": [], "手环": [], "防具": [], "耳环": []},
         "T0 血系（血之狂暴每2000成长+1%最大生命）；构筑装备 07 未载待补"),
    "雅": ({"武器": ["狂信者的荣誉之刃"], "手环": ["折光戒指"],
          "防具": ["旅法师的灵光袍"], "耳环": ["凶神耳环"]},
         "活动限定（本号未持有）；高速高穿神秘刃+折光+神秘袍+凶神耳环(专属)"),
    "绮": ({"武器": ["反叛者的刺杀弓"], "手环": [], "防具": [], "耳环": []},
         "沸血+神秘弓（刺杀弓候选）；新卡当前版本弱，高速打护盾"),
    "琳": ({"武器": [], "手环": [], "防具": [], "耳环": []},
         "当前版本不推荐（刃琳/剑盾琳均弱）"),
    "艾": ({"武器": [], "手环": [], "防具": [], "耳环": []},
         "当前版本不推荐；星火宝石专属（每击降对方1%物/魔伤）"),
}

SLOT_CN = ["武器", "手环", "防具", "耳环"]
GEAR_KEY = lambda it: (it["total"], it["quality"], it["mystery"])


def _fmt_item(it):
    if it["mystery"]:
        tag = "神秘"
    elif it["quality"]:
        tag = f"q{it['quality']}"
    else:
        tag = ""  # f=6 身上件 icon 无品质后缀, 解析不出
    bid = f" id={it['bid']}" if it.get("bid") else ""
    return f"{it['name']} {it['total']:.0f}%{'(' + tag + ')' if tag else ''}{bid}"


def _fit_match(items, names):
    """子串匹配: 推荐名 ⊆ 装备名（兼容别名/前缀, 如 战线支撑者的荆棘重甲）。"""
    return [it for it in items if any(n in it["name"] for n in names)]


def _load_gear_sets():
    """读身上(f=6)+仓库(f=7)装备并归一化（icon 补名/去前缀空格/标签部位）。

    每件附加 slot(0武器/1手环/2防具/3耳环, 由 icon 码前两位定)。
    返回 (worn, store)。
    """
    worn = parse_equips(read_block(6))
    store = parse_equips(read_block(7))
    try:
        import battle_sim as _bs
        icon2name = {}
        for base, types in ((2101, _bs.GEAR_NAME[1:13]), (2201, _bs.GEAR_NAME[13:19]),
                            (2301, _bs.GEAR_NAME[19:26]), (2401, _bs.GEAR_NAME[26:31])):
            for i, t in enumerate(types):
                icon2name[base + i] = _bs.GEAR_CN[t]
        for it in worn + store:
            if it["name"] in ("?", "") and it["icon"]:
                it["name"] = icon2name.get(int(it["icon"]), it["name"])
    except Exception:
        pass
    # 名字归一化: f=7 title 带 '>' 前缀/内嵌空格（'>荆棘盾剑'/'命师的 传承手环'）
    for it in worn + store:
        if it["name"]:
            it["name"] = it["name"].lstrip("> ").replace(" ", "").strip()
        it["slot"] = int(it["icon"]) // 100 - 21 if it["icon"] else -1
    worn.sort(key=GEAR_KEY, reverse=True)
    store.sort(key=GEAR_KEY, reverse=True)
    return worn, store


def gearfit():
    """[8] 配装推荐 × 持有查询：每角色 4 部位推荐装备，身上/仓库中找同名最优件。

    装备词条按类型固定 → 只对类型推荐；同名多件取词条总分最高
    （品质/神秘次序）。仓库件附 id（供 c=3&id= 手动穿戴）。
    """
    worn, store = _load_gear_sets()
    key = GEAR_KEY
    print(f"身上 {len(worn)} 件: " + " / ".join(_fmt_item(i) for i in worn))
    print(f"仓库装备 {len(store)} 件\n")
    cur = next((n for n, z in (list_cards() or {}).items() if z == state.ZID), "?")
    for role, (fit, note) in GEAR_FIT.items():
        mark = "（出战中）" if role == cur else ""
        print(f"【{role}】{mark} {note}")
        for slot, names in fit.items():
            if not names:
                print(f"  {slot}  ⚠️ 待核（构筑未载/简称待还原）")
                continue
            hit_worn = _fit_match(worn, names)
            if hit_worn:
                print(f"  {slot}  ✓身上 {'/'.join(_fmt_item(i) for i in hit_worn)}")
                continue
            cands = _fit_match(store, names)
            if cands:
                best = max(cands, key=key)
                alt = f"（同{'/'.join(names)}×{len(cands)}取最优）" if len(cands) > 1 else ""
                print(f"  {slot}  📦 仓库 {_fmt_item(best)}{alt}")
            else:
                print(f"  {slot}  ❌缺 {'/'.join(names)}（仓库无，沙滩/商店留意）")
        print()


def equip_loadout(role=None, dry_run=False):
    """[7.5] 一键换装：给指定角色（默认出战卡）穿上 GEAR_FIT 推荐套。

    ⚠️ 装备栏账号级共享 → 换装影响全号（所有卡同装）。
    每部位: 推荐名子串匹配仓库 → 最优件(总分/品质/神秘)；身上已匹配推荐
    且评分 ≥ 仓库最优则跳过，否则 c=3&id= 穿上（同部位替换，旧件自动回仓库）。
    待核部位(空列表)不动。返回换装件数。
    """
    if role is None:
        role = next((n for n, z in (list_cards() or {}).items() if z == state.ZID), None)
    fit, _note = GEAR_FIT.get(role, ({}, ""))
    if not fit:
        print(f"[换装] {role}: GEAR_FIT 无推荐，跳过")
        return 0
    worn, store = _load_gear_sets()
    worn_by_slot = {it["slot"]: it for it in worn if it["slot"] >= 0}
    changed = 0
    print(f"[换装] {role}{'（预览）' if dry_run else ''}")
    for slot_idx, slot in enumerate(SLOT_CN):
        names = fit.get(slot) or []
        if not names:
            continue
        cands = [it for it in _fit_match(store, names) if it["slot"] == slot_idx and it.get("bid")]
        best = max(cands, key=GEAR_KEY) if cands else None
        cur_it = worn_by_slot.get(slot_idx)
        cur_ok = (cur_it and any(n in cur_it["name"] for n in names)
                  and (not best or GEAR_KEY(cur_it) >= GEAR_KEY(best)))
        if cur_ok:
            print(f"  {slot}  ✓保留 {_fmt_item(cur_it)}")
            continue
        if not best:
            print(f"  {slot}  ❌缺 {'/'.join(names)}（仓库无可穿，保持现状）")
            continue
        if dry_run:
            print(f"  {slot}  {_fmt_item(cur_it) if cur_it else '（空）'} → {_fmt_item(best)}")
        else:
            r = click(3, id=best["bid"])
            ok = "已装备" in r
            print(f"  {slot}  {'✓' if ok else '❌'} {(_fmt_item(cur_it) + ' → ') if cur_it else ''}"
                  f"{_fmt_item(best)}{'' if ok else ' | ' + strip_tags(r)[:60]}")
            changed += 1 if ok else 0
    if dry_run:
        print("（预览模式，未实际换装；--apply 执行）")
    else:
        print(f"换装完成: {changed} 件（旧件已回仓库，账号级共享全号生效）")
    return changed
