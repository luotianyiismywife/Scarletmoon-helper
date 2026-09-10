# -*- coding: utf-8 -*-
"""仓库整理 / 熔炼护身符 / 空格查询（2026-09-10 拆分自主文件）。"""
import re
import time
import random
from collections import defaultdict

from ggzlib.http import read_block, click, strip_tags
from ggzlib.rules import parse_equips


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


def smelt():
    """[4.5c] 熔炼仓库可熔炼装备为护身符（4.5b 决策树）。

    触发条件（2026-08-10 实测）：品质≥3 且 总值≥410% 且 无神秘 且 非橙装（<516%）。
    熔炼 = c=9&id=<仓库装备id>&yz=124，返回新护身符 id；装备消失。
    ⚠️ 神秘装/橙装永不熔炼（巨亏教训：神秘属性消失）。
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


def warehouse_tidy(dry_run=False, green_only=False, clear_beach=False):
    """[4.5d] 仓库整理（2026-09-05 整合，源自 tools/ggz/warehouse_tidy.py）。

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
        #   "已将装备丢弃到沙滩，在它从沙滩上消失前，仍可以捡回。"（08-24 日志 26 条样本）
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
