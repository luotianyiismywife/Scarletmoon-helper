# -*- coding: utf-8 -*-
"""对拍校准：真实舞配置 vs 今日实测野怪，对照实战胜负（用完即删）。

实测基准（2026-09-09 上午实战，ggz_daily pk）:
    vs SHI:88 → 1胜3负；vs SHI:91 → 0胜3负；vs SHI:52 → 1胜0负
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import battle_sim as bs

G = bs.GEAR_NAME_TO_TYPE

# ---- 真实数据（2026-09-09 f=5/f=6/f=18/f=23 探测）----
pc = bs.Player(
    role=bs.ROLE_WU,
    lvl=803,            # 卡片等级（f18: Lv.803）
    kfLvl=86,           # 总争夺等级 = 29基础+57幻影（f23）
    attr=[1560, 348, 1, 347, 1, 347],   # f18 六维
    gear=[
        bs.Gear(G["SWORD"], 100, (150, 139, 145, 150)),          # 探险者之剑
        bs.Gear(G["BRACELET"], 100, (148, 132, 111, 111)),       # 命师的传承手环
        bs.Gear(G["WOOD"], 100, (143, 85, 109, 150), True),      # 复苏战衣(神秘)
        bs.Gear(G["TIARA"], 100, (150, 114, 136, 98), True),     # 占星师的耳饰(神秘)
    ],
    # 佩戴天赋（f5 halotfmr 勾选 101/102/205/307）
    auraSkl=bs.AURA_SHI | bs.AURA_XIN | bs.AURA_XIAO | bs.AURA_WU,
    growth=924,          # f18 角色技能成长点数
    # 假设: 许愿池/护身符加成为 0（待补数据源）
)

me = bs.prepare_pc_bstat(pc)
print("我方面板:", me.brief())
print(f"  物攻{me.pAtkB:.0f}+{me.pAtkA:.0f} 魔攻{me.mAtkB:.0f}+{me.mAtkA:.0f}"
      f" 绝伤{me.aAtk:.0f} 攻速{me.spdB + me.spdA:.0f}"
      f" 物防{me.pDefB:.1f}+{me.pDefA:.0f} 魔防{me.mDefB:.1f}+{me.mDefA:.0f}"
      f" 物减伤{me.pRdc:.0f} 魔减伤{me.mRdc:.0f}")
print(f"  技能率{me.sRateP:.1f}% 暴击率{me.cRateP:.1f}% 物穿{me.pBrcP:.1f}%+{me.pBrcA:.0f}"
      f" 吸血{me.lchP:.1f}% 反伤{me.rflP:.0f}% 神秘位={me.myst}")

print("\n=== 对拍（n=500）===")
for lvl, expect in [(88, "实战 1胜3负"), (91, "实战 0胜3负"), (52, "实战 1胜0负")]:
    st = bs.run_matches(pc, bs.ROLE_SHI, lvl, n=500)
    print(f"vs SHI:{lvl} → 胜{st['win']} 负{st['lose']} 平{st['draw']}"
          f" 胜率{st['win_rate']:.1f}%（{expect}）")
