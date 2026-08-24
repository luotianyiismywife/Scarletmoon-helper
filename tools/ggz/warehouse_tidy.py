# -*- coding: utf-8 -*-
"""咕咕镇仓库整理脚本（独立工具，非日常流程）。

用法:
    python tools/ggz/warehouse_tidy.py               # 整理仓库（灰蓝全清 + 同名绿装留最好）
    python tools/ggz/warehouse_tidy.py --dry-run     # 仅预览，不实际操作
    python tools/ggz/warehouse_tidy.py --green-only  # 只整理绿色（品质3），不动灰蓝
    python tools/ggz/warehouse_tidy.py --clear-beach # 丢完后立即 c=20 清理沙滩回收锻造石

整理规则（2026-08-24 用户指定）：
  - 灰/蓝（品质 1/2）：默认全部丢沙滩（c=7，可逆，24h 内可捡回）
    ⚠️ 日常脚本沙滩不会捡灰蓝装备，所以仓库里的灰蓝多为历史遗留/熔炼备料
  - 绿（品质 3）：同名装备只留 4 词条总值（total）最高的一件，其余丢沙滩
    （绿色不会有神秘属性——品质≥4 才可能出神秘，见 03-装备说明.md §2.3）
  - 橙/红（品质 4/5）：不处理（可能含神秘，价值高，留给用户手动决策）

丢沙滩后默认不自动清理（保守，避免误清沙滩原有装备）。
加 --clear-beach 参数则丢完立即 c=20 清理沙滩回收锻造石。
整理后如需手动清理沙滩回收锻造石，运行: python tools/ggz/ggz_daily.py beach

依赖: cookie.txt（tools/get_cookies.py --game 刷新）+ ggz_daily.py（同目录）
"""
import os
import sys
import time
import random
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ggz_daily as g


def tidy(dry_run=False, green_only=False, clear_beach=False):
    """仓库整理主逻辑。"""
    g.setup_logging()
    g.USER, g.SAFEID = g.get_user_and_safeid()
    if not g.SAFEID or not g.USER:
        print("无法获取登录信息（用户名/safeid），请确认 cookie 有效")
        sys.exit(1)
    g.ZID = g.get_active_zid()
    g.add_secret(g.USER)
    g.add_secret(g.SAFEID)
    print(f"[用户={g.USER} safeid={g.SAFEID} 出战角色zid={g.ZID}]")

    t = g.read_block(2)
    items = g.parse_equips(t, want_id=True)
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
    for i, it in enumerate(to_drop, 1):
        r = g.click(7, id=it["bid"])
        msg = g.strip_tags(r)[:80]
        print(f"  [{i}/{len(to_drop)}] c=7 丢弃 {it['name']} {it['quality']}等 {it['total']:.0f}% (id={it['bid']}): {msg}")
        time.sleep(random.uniform(0.5, 1.0))  # 间隔避免限流

    print(f"\n完成：丢弃 {len(to_drop)} 件到沙滩（24h 内可捡回）")

    # --clear-beach：丢完后立即清理沙滩回收锻造石
    if clear_beach:
        print("\n[--clear-beach] 自动清理沙滩回收锻造石...")
        r = g.click(20)
        msg = g.strip_tags(r)[:120]
        print(f"  c=20 清理沙滩: {msg}")
    else:
        print("\n未自动清理沙滩（保留可捡回窗口）")
        print("→ 如需清理沙滩回收锻造石，运行: py tools/ggz/ggz_daily.py beach")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    green_only = "--green-only" in sys.argv
    clear_beach = "--clear-beach" in sys.argv
    tidy(dry_run=dry_run, green_only=green_only, clear_beach=clear_beach)
