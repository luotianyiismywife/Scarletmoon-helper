# -*- coding: utf-8 -*-
"""一键日常编排 + 状态汇总（2026-09-10 拆分自主文件）。"""
from ggzlib.http import read_block, strip_tags
from ggzlib.cards import addpoint
from ggzlib.workshop import gem, gemup, halo, shop, wish
from ggzlib.beach import beach
from ggzlib.battle import pk, parse_pk
from ggzlib.gift import gift


def all_daily(no_refresh=False, bonus=0):
    """一键日常（按 05 §4A 顺序，逐步容错）：
    addpoint → gem(收菜+开工) → gemup → halo → shop → wish → beach → pk → gift
    ⚠️ 2026-09-07 新增商店步骤（在许愿池之前）：星沙日限换贝壳（1 星沙=10w），
    先于 wish 执行让贝壳尽早够 300w 十连阈值；默认 SHOP_MODE="daily" 只买日限，
    批量/药水不自动买（太亏，需要时手动 `shop --full`）。
    ⚠️ 2026-08-26 药水策略（用户决定）：all 默认不执行 bonus（额外奖励），药水不自动消耗。
    需用时显式带 --bonus1/--bonus2（透传给翻牌步骤 gift(bonus=...)）：
      --bonus1 → 翻牌后 c=13&id=1 耗 1 药水再领（固定 6000 贝壳+6000 经验）
      --bonus2 → 翻牌后 c=13&id=2 耗 2 药水重置狗牌+翻牌 → 重新出击 → 再翻一轮
    理由：bonus 收益固定且低、药水机会成本高（B 段 20 星沙/瓶，1 星沙≈10w 贝壳；
    重置翻牌 c=13&id=2 需 2 瓶，远期价值更高）。
    no_refresh=True 时沙滩空不自动刷新（不耗随机装备箱，供保留装备箱场景）。

    ⚠️ 顺序已知问题（2026-08-20 记录，暂不改）：
      halo（光环提升）在 gift（翻牌）之前执行，而光环天赋石(it310)的主要来源
      是翻牌结算（3 同色必给 1 颗）→ 当天翻牌拿到的石头当天用不上，要等次日。
      后续可把 halo 挪到 gift 之后（gift → halo → bonus），让当天石头当天用。
      用户决定顺序后面再调整，先在此留档。
    """
    from ggzlib.state import AuthExpiredError

    steps = [("加点", addpoint), ("工坊收菜", gem), ("宝石提升", gemup),
             ("光环提升", halo), ("商店", shop), ("许愿池", wish),
             ("沙滩收取", lambda: beach(allow_refresh=not no_refresh,
                                         wait_after_refresh=False)),
             ("出击打野", pk), ("翻牌", lambda: gift(bonus=bonus))]
    # bonus 走 gift(bonus=...) 配置项（2026-08-26 用户设计），默认 0 不耗药水
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
