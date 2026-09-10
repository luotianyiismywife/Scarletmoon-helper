# -*- coding: utf-8 -*-
"""出击打野 / 战场状态 / 野怪解析 / 战斗模拟对接 / 额外奖励（2026-09-10 拆分自主文件）。"""
import re
import time

from ggzlib import state
from ggzlib.http import request, dec, read_block, click, strip_tags, show
from ggzlib.cards import list_cards, switch_card, addpoint, CARD_ZIDS

PK_SIM_SWITCH_RATE = 40  # 模拟胜率 <40% → 判定打不过，换卡（否则视为偶发失败）
PK_SIM_ROUNDS = 300      # 每次预测模拟局数（速度/精度平衡，约 2-4 秒）
PK_SIM_QUICK_ROUNDS = 100  # 全卡快筛局数（重排换卡链用，约 1.3s/卡）


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


def parse_monster_level(mon):
    """parse_monster 结果 → 野怪等级 int（'史莱姆（野怪 Lv.88）' → 88），失败 None。"""
    if not mon:
        return None
    m = re.search(r"Lv\.(\d+)", mon.get("name", ""))
    return int(m.group(1)) if m else None


def sim_current_winrate(monster_lvl):
    """模拟当前出战卡 vs 野怪，返回胜率%（0-100）；模拟不可用返回 None。

    数据源 = battle_sim.load_player()（f=18 加点/等级/成长 + f=23 争夺等级 +
    f=6 装备 + f=5 天赋，装备账号级共享 → 任意卡直读）。任何异常都兜底返回 None，
    让 pk() 回退到原盲打策略——模拟是增强，绝不能阻塞日常。
    """
    try:
        import battle_sim as bs
    except Exception as e:
        print(f"  ⚠️ battle_sim 导入失败（{e}），跳过模拟，回退盲打策略")
        return None
    try:
        pc = bs.load_player(verbose=False)
        st = bs.run_matches(pc, bs.ROLE_SHI, monster_lvl, n=PK_SIM_ROUNDS)
        return st["win_rate"]
    except Exception as e:
        print(f"  ⚠️ 模拟失败（{e}），跳过，回退盲打策略")
        return None


def fight(target=1):
    """出击一次，返回 (结果类型, 原始文本)。fyg_v_intel.php 需带 safeid！"""
    r = dec(request(state.BASE + "/fyg_v_intel.php", {"id": target, "safeid": state.SAFEID}))
    if f"{state.USER} 获得了胜利！" in r:
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
    """f=12 → 战场状态 dict（段位/进度/狗牌/出击/连胜/连败）。"""
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

    ⭐ 2026-09-09 接入战斗模拟（battle_sim.py，对拍精度已验证）：
    首场打野战报拿野怪等级 → 之后每次打野失败/平局先**模拟当前卡胜率**，
    胜率 < PK_SIM_SWITCH_RATE 判定"打不过"直接换卡（不再实际出击试错，
    每次 lose 省 1% 进度/出击）；胜率尚可视为偶发失败继续打野。
    换卡后立即模拟新卡预期胜率供参考。模拟不可用时回退原盲打策略。

    ⭐ 2026-09-10 谁强谁站前台：首次需要换卡且已知野怪等级时，全卡
    n=100 快筛按模拟胜率**重排换卡链**（强卡先切；f=18&zid= 免切卡直读
    + 装备账号级共享 → 无需真实切卡即可精确巡检）；快筛失败保持原序。
    换卡闭环（2026-09-10）：切卡 → **换装到新卡推荐套**（equip_loadout）
    → 重排专属加点 → 新卡模拟胜率（读到新装备面板）。
    """
    # 角色轮换列表（打野平局时切换）: 先试其他角色,最后回到当前
    # 2026-09-05: 谁打赢用谁——换角色后不切回原角色（避免每天重复换）
    card_order = list(CARD_ZIDS.values())
    switch_seq = [z for z in card_order if z != state.ZID] + [state.ZID]
    switch_idx = 0
    seq_sorted = False  # 换卡链是否已按模拟胜率重排（当日首次换卡时做一次）
    draw_count = 0
    monster_lvl = None  # 当日野怪等级（首场打野战报得知；同日基本稳定）

    def _sort_seq_by_sim(lvl):
        """按模拟胜率重排 switch_seq（2026-09-10: 谁强谁先切）。

        全卡 n=100 快筛（约 15-20s，当日首次换卡才触发一次）；
        任一卡模拟失败 → 该卡记 -1% 沉底。近似：统一用当前六维模拟
        （六维全角色共享；切卡后 addpoint 重排成新卡策略，对量级判断
        影响远小于角色技能/被动差异）。
        """
        nonlocal switch_seq, seq_sorted
        if seq_sorted or not lvl:
            return
        seq_sorted = True
        try:
            import battle_sim as bs
        except Exception as e:
            print(f"  ⚠️ battle_sim 导入失败（{e}），换卡链保持原序")
            return
        zid2name = {z: n for n, z in CARD_ZIDS.items()}

        def _wr(zid):
            try:
                pc = bs.load_player(zid=zid, verbose=False)
                return bs.run_matches(pc, bs.ROLE_SHI, lvl,
                                      n=PK_SIM_QUICK_ROUNDS)["win_rate"]
            except Exception:
                return -1.0
        scored = [(z, _wr(z)) for z in switch_seq]
        switch_seq = [z for z, _ in sorted(scored, key=lambda x: x[1], reverse=True)]
        row = " > ".join(f"{zid2name.get(z, z)}{wr:.0f}%" for z, wr in scored)
        print(f"  ↪ 换卡链按模拟胜率重排（vs SHI:{lvl}，n={PK_SIM_QUICK_ROUNDS}）:\n     {row}")

    def _switch_next(reason):
        """切换到 switch_seq 下一张卡（含换装+加点同步+新卡预期胜率评估）。

        返回 "ok"（切换成功，mode 已重置 pvp）/ "fail"（切卡失败，mode 不变）/
        "exhausted"（所有角色已试完）。
        """
        nonlocal switch_idx, mode, seq_sorted
        if switch_idx >= len(switch_seq):
            return "exhausted"
        # 首次换卡且野怪等级已知 → 按模拟胜率重排换卡链（谁强谁先切）
        _sort_seq_by_sim(monster_lvl)
        new_zid = switch_seq[switch_idx]
        switch_idx += 1
        print(f"  🔄 {reason} → 切换出战角色 zid={new_zid}")
        r2 = click(5, id=new_zid)
        if "ok" not in r2 and "装备成功" not in r2:
            print(f"  ⚠️ 切卡失败: {strip_tags(r2)[:60]}")
            return "fail"
        state.ZID = new_zid
        # 换卡闭环 ①换装: 装备账号级共享 → 换到新卡推荐套立即全号生效
        # （在模拟之前穿好, 让新卡预期胜率读到新装备面板）
        new_name = next((n for n, z in CARD_ZIDS.items() if z == new_zid), None)
        if new_name:
            try:
                from ggzlib.gearfit import equip_loadout
                equip_loadout(new_name)
            except Exception as e:
                print(f"  ⚠️ 换装异常: {e}")
        # ② 加点: 换角色后按新角色策略切换（2026-09-05: 点数共享,
        # apply 全量覆盖 = 切到该角色专属配置, 只耗 1 次修改）
        try:
            addpoint(zid=new_zid, apply=True)
        except Exception as e:
            print(f"  ⚠️ 加点异常: {e}")
        mode = "pvp"  # 从打人重新开始
        # ③ 新卡预期胜率（换装/加点已生效，读到的就是新卡面板）
        wr = sim_current_winrate(monster_lvl) if monster_lvl else None
        if wr is not None:
            print(f"  ↪ 新卡模拟胜率 {wr:.0f}% vs SHI:{monster_lvl}"
                  f"（阈值 {PK_SIM_SWITCH_RATE}%）")
        return "ok"

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
        # 2026-09-09: 同时抽野怪等级供战斗模拟（首场得知后当日复用）
        if target == 1 and kind in ("win", "draw", "lose"):
            mon = parse_monster(r)
            if mon:
                print(f"  野怪: {mon['name']} 盾{mon['sld']} 血{mon['hp']} 天赋{mon['talents']}")
                lvl = parse_monster_level(mon)
                if lvl:
                    monster_lvl = lvl
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
                wr = sim_current_winrate(monster_lvl) if monster_lvl else None
                tag = f"（模拟胜率 {wr:.0f}%）" if wr is not None else ""
                print(f"  ↪ 打野平局（第 {draw_count} 次）{tag} → 换角色重来")
                if _switch_next("平局打不死") == "exhausted":
                    print("  ⛔ 所有角色都试过了，仍打不过，停止")
                    break
            continue
        if kind == "lose":
            if target == 2:
                print("  ↪ 打人失败，切打野")
                mode = "pve"
            else:
                # 打野失败 → 先模拟当前卡 vs 野怪（2026-09-09 接入 battle_sim）：
                # 胜率过低 = 不是运气差而是打不过 → 直接换卡（省 1-2 次试错出击）；
                # 胜率尚可 = 偶发失败 → 继续打野（连败掉段送狗牌+野怪变弱更好打）
                wr = sim_current_winrate(monster_lvl) if monster_lvl else None
                if wr is not None and wr < PK_SIM_SWITCH_RATE:
                    print(f"  ↪ 打野失败，模拟胜率 {wr:.0f}% < {PK_SIM_SWITCH_RATE}% → 换卡")
                    if _switch_next("模拟判定打不过") == "exhausted":
                        print("  ⛔ 所有角色都试过了，仍打不过，停止")
                        break
                    continue
                why = f"模拟胜率 {wr:.0f}%（偶发失败）" if wr is not None else "模拟不可用"
                print(f"  ↪ 打野失败，继续打野（{why}；连败累计，掉段后野怪变弱更好打）")
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
