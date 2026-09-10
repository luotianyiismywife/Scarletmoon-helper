# -*- coding: utf-8 -*-
"""咕咕镇战斗模拟器（guguzhen-calculator 的 newkf.cpp Python 移植，独立工具）。

用法:
    python tools/ggz/battle_sim.py demo            # 内置示例: 舞 vs 史莱姆
    python tools/ggz/battle_sim.py npc --role SHI --lvl 88          # 查看野怪模板属性
    python tools/ggz/battle_sim.py fight --pc demo.json --npc SHI:88 --n 1000
                                     # 从 JSON 读我方配置，跑 n 次对局报胜率
    (供 ggz_daily.py import 调用: sim_vs_slime(...)/run_matches(...))

移植来源（2026-09-09 精读）:
    github.com/ilusrdbb/guguzhen-calculator  newkf.cpp（5633 行）
    - calcBattle()    2601 行  100 回合战斗循环
    - calcDefRate()   2517 行  减伤公式
    - preparePcBStat() 2203 行  六维+装备+许愿+护符 → 战斗属性
    - prepareNpcBStat() 1720 行 野怪模板（木人/蛛/灯/兽/史莱姆...）
    - prepareLiuStat() 2051 行  六边形战士随机属性
    公式详解见 docs/咕咕镇-新争夺资料/04-规则细节.md §4.7b

⚠️ 数值语义对齐（移植最易踩的坑）:
    - C++ `int(x)` 向零截断 → Python int() 同为向零截断，但 x//y 是向负无穷取整，
      负数时两者不同 → 统一用 cdiv()/ctrunc()
    - C++ round() 四舍五入（.5 远离零）→ Python round() 是银行家舍入 → 用 cround()
    - 全部随机数用 C++ 同款 MINSTD（Park-Miller, A=48271）保证可复现

依赖: 无第三方库（纯标准库，独立可运行）。
"""
import argparse
import json
import math
import sys

# ⚠️ 不能无条件 reconfigure：被 ggz_daily import 时 stdout 已替换为 Tee 日志对象
# （无 reconfigure 方法，2026-09-09 实测导入即炸 'Tee' object has no attribute
# 'reconfigure'）→ 仅在仍是原生终端流时设置编码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ═══════════════════════ 整数语义辅助 ═══════════════════════

def ctrunc(x):
    """C++ (int)x / int(x)：向零截断。Python int() 对 float 同为向零，直接复用。"""
    return int(x)


def cdiv(a, b):
    """C++ 整数除法：向零截断（Python // 向负无穷，负数时不同）。"""
    q = a // b
    if (a % b != 0) and ((a < 0) != (b < 0)):
        q += 1
    return q


def cround(x):
    """C round()：四舍五入，.5 远离零（Python round 是银行家舍入，不能直接用）。"""
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


# ═══════════════════════ 角色枚举 ═══════════════════════

# NPC（ROLE_NPC=0 起）
ROLE_MU, ROLE_ZHU, ROLE_DENG, ROLE_SHOU = 0, 1, 2, 3
ROLE_MU2, ROLE_ZHU2, ROLE_DENG2, ROLE_SHOU2, ROLE_YU2, ROLE_HAO2 = 4, 5, 6, 7, 8, 9
ROLE_LIU, ROLE_SHI = 10, 11
NPC_COUNT_OLD, NPC_COUNT_OLD2, NPC_COUNT = 4, 10, 12

# PC（ROLE_PC=20 起）
ROLE_PC = 20
ROLE_MO, ROLE_LIN, ROLE_AI, ROLE_MENG, ROLE_WEI = 20, 21, 22, 23, 24
ROLE_YI, ROLE_MING, ROLE_MIN, ROLE_WU, ROLE_XI = 25, 26, 27, 28, 29
ROLE_XIA, ROLE_YA, ROLE_QI = 30, 31, 32
PC_COUNT = 13

NPC_NAME = ["MU", "ZHU", "DENG", "SHOU", "MU2", "ZHU2", "DENG2",
            "SHOU2", "YU2", "HAO2", "LIU", "SHI"]
# 中文名对照（游戏内叫法）
NPC_CN = {"MU": "铁皮木人", "ZHU": "迅捷蛛", "DENG": "魔灯之灵", "SHOU": "食铁兽",
          "SHI": "史莱姆", "LIU": "六边形战士"}
PC_NAME = ["MO", "LIN", "AI", "MENG", "WEI", "YI", "MING", "MIN",
           "WU", "XI", "XIA", "YA", "QI"]
PC_CN = {"MO": "默", "LIN": "琳", "AI": "艾", "MENG": "梦", "WEI": "薇",
         "YI": "伊", "MING": "冥", "MIN": "命", "WU": "舞", "XI": "希",
         "XIA": "霞", "YA": "雅", "QI": "绮"}
NAME_TO_ROLE = {name: ROLE_PC + i for i, name in enumerate(PC_NAME)}
NPC_TO_ROLE = {name: i for i, name in enumerate(NPC_NAME)}

# 六维
ATTR_STR, ATTR_AGI, ATTR_INT, ATTR_VIT, ATTR_SPR, ATTR_MND = range(6)
ATTR_COUNT = 6

# ═══════════════════════ 光环（天赋）位定义 ═══════════════════════

(AURA_SHI, AURA_XIN, AURA_BI, AURA_MO, AURA_DUN, AURA_XUE, AURA_XIAO,
 AURA_SHENG, AURA_E, AURA_SHANG, AURA_SHEN, AURA_CI, AURA_REN, AURA_RE,
 AURA_DIAN, AURA_WU, AURA_ZHI, AURA_SHAN, AURA_FEI, AURA_BO, AURA_JU,
 AURA_HONG, AURA_JUE, AURA_HOU, AURA_DUNH, AURA_ZI, AURA_ZOU, AURA_PIAO,
 AURA_PEN, AURA_DI) = [1 << i for i in range(30)]

AURA_COUNT = 30
FLAG_STAT = 1 << AURA_COUNT

AURA_NAME = ["SHI", "XIN", "BI", "MO", "DUN", "XUE", "XIAO", "SHENG", "E",
             "SHANG", "SHEN", "CI", "REN", "RE", "DIAN", "WU", "ZHI", "SHAN",
             "FEI", "BO", "JU", "HONG", "JUE", "HOU", "DUNH", "ZI",
             "ZOU", "PIAO", "PEN", "DI"]
# 天赋中文名（游戏内文案）
AURA_CN = {"SHI": "启程之誓", "XIN": "启程之心", "BI": "破壁之心", "MO": "破魔之心",
           "DUN": "复合护盾", "XUE": "鲜血渴望", "XIAO": "削骨之痛", "SHENG": "圣盾祝福",
           "E": "恶意抽奖", "SHANG": "伤口恶化", "SHEN": "精神创伤", "CI": "铁甲尖刺",
           "REN": "忍无可忍", "RE": "热血战魂", "DIAN": "点到为止", "WU": "午时已到",
           "ZHI": "纸薄命硬", "SHAN": "不动如山", "FEI": "沸血之志", "BO": "波澜不惊",
           "JU": "飓风之力", "HONG": "红蓝双刺", "JUE": "荧光护盾", "HOU": "后发制人",
           "DUNH": "钝化锋芒", "ZI": "自信回头", "ZOU": "致命节奏", "PIAO": "往返车票",
           "PEN": "天降花盆", "DI": "绝对底线"}
AURA_NAME_TO_BIT = {n: b for n, b in zip(AURA_NAME, [1 << i for i in range(30)])}

# ═══════════════════════ 神秘属性位定义 ═══════════════════════

MYST_BLADE = 0x000001      # 暴击时附带(物攻*50%)的绝对伤害
MYST_ASSBOW = 0x000002     # 攻击附带(对方当前护盾*18%)物伤
MYST_DAGGER = 0x000004     # 星火翻倍
MYST_WAND = 0x000008       # 魔力压制+40%技能伤，第一击必技能
MYST_SHIELD = 0x000010     # 削弱对方40%回血回盾
MYST_CLAYMORE = 0x000020   # 暴击率100%
MYST_SPEAR = 0x000040      # 攻击附带(对方当前生命*18%)魔伤
MYST_COLORFUL = 0x000080   # 彩金对剑同时物魔
MYST_LIMPIDWAND = 0x000100 # 澄空之心+15%对方魔防的附加穿透
MYST_BRACELET = 0x000200   # 20%几率特殊暴击(魔伤*2)
MYST_VULTURE = 0x000400    # 额外20%对护盾吸血转生命
MYST_RING = 0x000800       # 舞:锦上添花伤害20%转普通伤
MYST_DEVOUR = 0x001000     # 命运链接护盾回复50%转生命
MYST_REFRACT = 0x002000    # 攻击满血满盾对手恢复先兆感知
MYST_CLOAK = 0x004000      # 护盾最大值+50%
MYST_THORN = 0x008000      # +25%固定反弹
MYST_WOOD = 0x010000       # 被攻击回5%最大生命
MYST_CAPE = 0x020000       # 被攻击时对方50%物伤转魔伤
MYST_TIARA = 0x040000      # 星芒之盾45%/减速4%
MYST_RIBBON = 0x080000     # 元气无限锁定30%血判定
MYST_HUNT = 0x100000       # 圣银弩箭30%物攻转绝伤
MYST_FIERCE = 0x200000     # 雅:日夜双效果

# ═══════════════════════ 许愿池/护符索引 ═══════════════════════

(WISH_HP_POT, WISH_SLD_POT, WISH_SHI_BUF, WISH_XIN_BUF, WISH_FENG_BUF,
 WISH_PATKA, WISH_MATKA, WISH_HPM, WISH_SLDM, WISH_SPDA,
 WISH_PBRCA, WISH_MBRCA, WISH_PDEFA, WISH_MDEFA) = range(14)
WISH_COUNT = 14

(AMUL_STR, AMUL_AGI, AMUL_INT, AMUL_VIT, AMUL_SPR, AMUL_MND,
 AMUL_PATK, AMUL_MATK, AMUL_SPD, AMUL_REC, AMUL_HP, AMUL_SLD,
 AMUL_LCH, AMUL_RFL, AMUL_CRT, AMUL_SKL, AMUL_PDEF, AMUL_MDEF,
 AMUL_AAA, AMUL_CRTR, AMUL_SKLR) = range(21)
AMUL_COUNT = 21

# ═══════════════════════ 战斗常量 ═══════════════════════

SPEED_REDUCE_MAX = 80   # 减速上限%（newkf.cpp 514 行）
REDUCE_RATE_A = 3       # 暴击/技能率对冲系数 A（526 行，config 可覆盖）
REDUCE_RATE_B = 10      # 暴击/技能率对冲系数 B（527 行）

# ═══════════════════════ 随机数（C++ MINSTD 同款，保证可复现）═══════════════════════

class Minstd:
    """Park-Miller MINSTD：newkf.cpp 676 行 myrand() 逐行对拍。

    C++: *rseed = M*(*rseed%Q) - R*(*rseed/Q); if(<0)+=0x7FFFFFFF; return *rseed%m
    与 Python random 无关——用同款生成器才能逐回合复现 C++ 模拟结果。
    """

    M = 48271
    Q = 0x7FFFFFFF // M
    R = 0x7FFFFFFF % M

    def __init__(self, seed):
        self.seed = seed & 0x7FFFFFFF or 1  # C++ 侧 seed=0 会有问题，最少 1

    def rand(self, m):
        s = self.seed
        self.seed = self.M * (s % self.Q) - self.R * (s // self.Q)
        if self.seed < 0:
            self.seed += 0x7FFFFFFF
        return self.seed % m

    def rand100(self):
        return self.rand(100)


# ═══════════════════════ 战斗状态 BStat（newkf.cpp 399 行结构体）═══════════════════════

class BStat:
    """战斗双方单侧状态。字段名与 C++ 一一对应，便于对照源码。"""

    __slots__ = (
        "role", "lvl", "hp", "hpM", "hpRecP", "hpRecA", "hpRecRR",
        "pAtkB", "pAtkA", "mAtkB", "mAtkA", "aAtk",
        "spdB", "spdA", "spdRR", "spdC",
        "pBrcP", "pBrcA", "mBrcP", "mBrcA", "cBrcP",
        "sRateB", "sRateP", "sRateR", "cRateB", "cRateP",
        "lchP", "pDefB", "pDefA", "mDefB", "mDefA", "pRdc", "mRdc",
        "sld", "sldM", "sldRecP", "sldRecA", "sldRecRR", "rflP",
        "cDef", "sDef", "psvSkl", "myst", "sklC", "houC", "qiAbsorb",
        "wish", "amul",
        "hpPot", "sldPot", "ziFlag", "minFlag", "piaoFlag",
        "growth", "pAtkR", "mAtkR", "hpMR", "sldMR",
        "tStr", "tAgi", "tInt", "tVit", "tSpr", "tMnd",
        "mode", "atkLvl", "defLvl", "hpS", "sldS", "rankLevel",
        "renCounter", "bugPoint", "alias",
    )

    def __init__(self, **kw):
        self.role = 0
        self.lvl = 1
        self.hp = self.hpM = 0.0
        self.hpRecP = 0
        self.hpRecA = 0.0
        self.hpRecRR = 0
        self.pAtkB = self.pAtkA = 0.0
        self.mAtkB = self.mAtkA = 0.0
        self.aAtk = 0.0
        self.spdB = self.spdA = 0.0
        self.spdRR = 0
        self.spdC = 0.0
        self.pBrcP = self.pBrcA = 0.0
        self.mBrcP = self.mBrcA = 0.0
        self.cBrcP = 0.0
        self.sRateB = self.sRateP = 0.0
        self.sRateR = 100.0
        self.cRateB = self.cRateP = 0.0
        self.lchP = 0.0
        self.pDefB = self.pDefA = 0.0
        self.mDefB = self.mDefA = 0.0
        self.pRdc = self.mRdc = 0.0
        self.sld = self.sldM = 0.0
        self.sldRecP = 0
        self.sldRecA = 0.0
        self.sldRecRR = 0
        self.rflP = 0.0
        self.cDef = 0
        self.sDef = 0
        self.psvSkl = 0
        self.myst = 0
        self.sklC = 0
        self.houC = 0
        self.qiAbsorb = 0.0
        self.wish = [0] * WISH_COUNT
        self.amul = [0] * AMUL_COUNT
        self.hpPot = self.sldPot = False
        self.ziFlag = self.minFlag = self.piaoFlag = False
        self.growth = 0
        self.pAtkR = self.mAtkR = 0
        self.hpMR = self.sldMR = 0
        self.tStr = self.tAgi = self.tInt = self.tVit = self.tSpr = self.tMnd = 0
        self.mode = 0
        self.atkLvl = self.defLvl = -1
        self.hpS = self.sldS = 0.0
        self.rankLevel = 0
        self.renCounter = 3
        self.bugPoint = 0
        self.alias = ""
        for k, v in kw.items():
            setattr(self, k, v)

    def clone(self):
        b = BStat()
        for k in self.__slots__:
            v = getattr(self, k)
            setattr(b, k, list(v) if isinstance(v, list) else v)
        return b

    def aura_names(self):
        return [AURA_CN.get(AURA_NAME[i], AURA_NAME[i])
                for i in range(AURA_COUNT) if self.psvSkl & (1 << i)]

    def brief(self):
        who = (PC_CN.get(PC_NAME[self.role - ROLE_PC], PC_NAME[self.role - ROLE_PC])
               if self.role >= ROLE_PC
               else NPC_CN.get(NPC_NAME[self.role], NPC_NAME[self.role]))
        return f"{who} Lv.{self.lvl} hp={int(self.hp)}/{int(self.hpM)} sld={int(self.sld)}/{int(self.sldM)} 天赋[{'|'.join(self.aura_names())}]"


# ═══════════════════════ 野怪模板（prepareNpcBStat, newkf.cpp 1720 行）═══════════════════════

# 野怪技能率/暴击率系数表（407-415 行）：sRateB = lvl * sklRate[role][0] / sklRate[role][1]
SKL_RATE = [(1, 1), (3, 1), (8, 1), (1, 1), (2, 1), (5, 2), (8, 1),
            (1, 1), (1, 1), (1, 1), (1, 1), (4, 1)]
CRT_RATE = [(1, 1), (1, 1), (0, 1), (3, 1), (2, 1), (2, 1), (0, 1),
            (3, 1), (4, 1), (0, 1), (1, 1), (4, 1)]

# 新版野怪固定天赋（各 case 末行的 psvSkl 常量）
_NPC_FIXED_AURA = {
    ROLE_MU2: AURA_SHANG | AURA_SHEN | AURA_REN | AURA_WU | AURA_DI,
    ROLE_ZHU2: AURA_XIAO | AURA_SHANG | AURA_SHEN | AURA_RE | AURA_WU | AURA_DI,
    ROLE_DENG2: AURA_DUN | AURA_SHANG | AURA_SHEN | AURA_REN | AURA_DI,
    ROLE_SHOU2: AURA_SHANG | AURA_SHEN | AURA_CI | AURA_REN | AURA_DI,
    ROLE_YU2: AURA_XIAO | AURA_SHANG | AURA_SHEN | AURA_RE | AURA_WU | AURA_DI,
    ROLE_HAO2: AURA_SHENG | AURA_SHANG | AURA_SHEN | AURA_CI | AURA_DI,
    # 史莱姆：与实测 fyg_pk.php 野怪天赋完全吻合（复合护盾+圣盾祝福+伤口恶化
    # +精神创伤+忍无可忍+热血战魂+午时已到+绝对底线），见 04-规则细节.md §4.1
    ROLE_SHI: (AURA_DUN | AURA_SHENG | AURA_SHANG | AURA_SHEN | AURA_REN
               | AURA_RE | AURA_WU | AURA_DI),
}


def prepare_npc_bstat(role, lvl):
    """野怪模板 → BStat（newkf.cpp 1720-2050 行逐字段对拍）。"""
    b = BStat(role=role, lvl=lvl, tAgi=float(lvl))
    if role < NPC_COUNT_OLD:
        # 旧版怪用前缀表（prefix*5+prefixCount 编码），战斗时再展开
        b.psvSkl = 0  # 由调用方通过 NonPlayer.prefix 指定，打野场景不涉及
        return b
    if role == ROLE_LIU:
        # 六边形战士：基础全 0，由 prepare_liu_stat 随机生成
        b.psvSkl = 0
        return b

    L = float(lvl)
    if role == ROLE_MU2:
        b.pAtkB = L * 30.0; b.mAtkB = L * 30.0; b.spdB = L * 3.0
        b.pBrcP = 50.0; b.pBrcA = L * 2.0; b.mBrcP = 50.0; b.mBrcA = L * 2.0
        b.pDefB = L * 5.0; b.mDefB = L * 5.0; b.hpM = L * 600.0
        b.sldM = L * 600.0
    elif role == ROLE_ZHU2:
        b.pAtkB = 0.0; b.mAtkB = L * 40.0; b.spdB = L * 9.0
        b.mBrcP = 50.0; b.mBrcA = L * 3.0
        b.pDefB = L * 4.0; b.mDefB = L * 4.0; b.hpM = L * 10.0
        b.sldM = L * 400.0
    elif role == ROLE_DENG2:
        b.pAtkB = 1.0; b.mAtkB = 1.0; b.spdB = L * 3.0
        b.mBrcP = 50.0; b.mBrcA = L * 3.0
        b.pDefB = L * 5.0; b.mDefB = L * 5.0; b.hpM = 1.0
        b.sldM = L * 900.0; b.sldRecP = 0
    elif role == ROLE_SHOU2:
        b.pAtkB = L * 80.0; b.mAtkB = 1.0; b.spdB = L * 1.0
        b.pBrcP = 50.0; b.pBrcA = L * 3.0; b.cBrcP = 30.0
        b.pDefB = L * 8.0; b.mDefB = L * 8.0; b.hpM = L * 600.0
        b.rflP = 30.0
    elif role == ROLE_YU2:
        b.pAtkB = L * 30.0; b.mAtkB = 0.0; b.spdB = L * 8.0
        b.pBrcP = 80.0; b.pBrcA = L * 2.0
        b.pDefB = L * 4.0; b.mDefB = L * 4.0; b.hpM = L * 400.0
    elif role == ROLE_HAO2:
        b.pAtkB = L * 30.0; b.mAtkB = 1.0; b.spdB = L * 1.0
        b.pBrcP = 50.0; b.pBrcA = L * 2.0; b.mBrcP = 50.0; b.mBrcA = L * 2.0
        b.pDefB = L * 6.0; b.mDefB = L * 6.0; b.hpM = L * 900.0
        b.rflP = 80.0
    elif role == ROLE_SHI:
        # 打野最常见的史莱姆（物理+魔法双修、高盾高回盾）
        b.pAtkB = L * 60.0; b.mAtkB = L * 60.0; b.aAtk = L * 10.0
        b.spdB = L * 5.0
        b.pBrcP = 30.0; b.pBrcA = L * 2.0
        b.mBrcP = 30.0; b.mBrcA = L * 2.0; b.cBrcP = 20.0
        b.pDefB = L * 3.0; b.mDefB = L * 3.0
        b.hpM = L * 300.0; b.hpRecP = 4
        b.sldM = L * 400.0; b.sldRecP = 6
        b.lchP = 10.0; b.rflP = 10.0
        b.pRdc = L * 10.0; b.mRdc = L * 10.0

    # 共通：技能/暴击基数、固定天赋
    b.sRateB = L * SKL_RATE[role][0] / SKL_RATE[role][1]
    b.cRateB = L * CRT_RATE[role][0] / CRT_RATE[role][1]
    b.psvSkl = _NPC_FIXED_AURA.get(role, 0)
    # 收尾（2028-2050 行）：野怪无装备/护符/许愿，附加字段全 0
    b.hp = b.hpM
    b.sld = b.sldM
    b.sRateR = 100.0
    b.hpRecA = 0.0
    b.hpRecRR = 0
    b.pAtkA = b.mAtkA = b.spdA = 0.0
    b.pAtkR = b.mAtkR = 0
    b.hpMR = b.sldMR = 0
    b.spdRR = 0
    b.spdC = b.spdB * (1 - b.spdRR / 100.0)
    b.pDefA = b.mDefA = 0.0
    b.sldRecA = 0.0
    b.sldRecRR = 0
    b.cDef = b.sDef = 0
    b.myst = 0
    b.sklC = b.houC = 0
    return b


# ═══════════════════════ 旧版野怪前缀表（initPrefTable, 595 行）═══════════════════════

PREF_SHANG, PREF_BO, PREF_FEI, PREF_HOU, PREF_JU = 0, 1, 2, 3, 4
PREF_HONG, PREF_DIAN, PREF_CI, PREF_JUE = 5, 6, 7, 8
PREF_COUNT = 9
PREF_NAME = ["SHANG", "BO", "FEI", "HOU", "JU", "HONG", "DIAN", "CI", "JUE"]
# 前缀 = 两光环组合（381 行）
PREF_AURA = [AURA_SHANG | AURA_SHEN, AURA_MO | AURA_BO, AURA_BI | AURA_FEI,
             AURA_REN | AURA_HOU, AURA_RE | AURA_JU, AURA_XIAO | AURA_HONG,
             AURA_SHENG | AURA_DIAN, AURA_XUE | AURA_CI, AURA_DUN | AURA_JUE]

# prefTable[mask][n] = 该掩码/n 个前缀下可选的天赋组合列表
PREF_TABLE = {}


def init_pref_table():
    """旧版野怪前缀展开表（595-626 行逐行对拍）。打野新版怪用不到，保留完整性。"""
    if PREF_TABLE:
        return
    for i in range(1 << PREF_COUNT):
        aura_skl = 0
        pref_n = 0
        for j in range(PREF_COUNT):
            if i & (1 << j):
                pref_n += 1
                aura_skl |= PREF_AURA[j]
        if pref_n <= 4:
            for j in range(i + 1):
                if (j & i) == j:
                    PREF_TABLE.setdefault((j, pref_n), []).append(aura_skl)
        for j in range(min(pref_n, 4) + 1):
            if j < pref_n:
                PREF_TABLE[(i, j)] = [aura_skl]


# ═══════════════════════ 减伤公式（calcDefRate, 2517 行）═══════════════════════

def calc_def_rate(defense, def_p, brc, c_brc, brc_a, def_max,
                  is_dunh, is_zhi, hong_brc_a, is_dian):
    """减伤率计算（newkf.cpp 2517-2545 行逐行对拍）。

    defense: 对方防御(基础+附加)  def_p: 防御%
    brc/c_brc/brc_a: 穿透% / 暴击穿透% / 附加穿透
    def_max: 减伤上限(圣盾祝福=80 否则 75)  is_zhi: 纸薄命硬保底10
    hong_brc_a: 红蓝双刺附加穿透(等级/2)或-1  is_dian: 点到为止防御×1.3
    """
    if hong_brc_a != -1:
        if int(brc) <= 40:
            brc = 40.0
        else:
            brc_a += hong_brc_a
    # 钝化锋芒：穿透与暴击穿透分别 int 再相加（"神秘取整 我服啦 xyfwcnm"）
    brc_p = ctrunc(0.65 * brc) + ctrunc(0.65 * c_brc) if is_dunh else brc + c_brc
    r = ctrunc((defense * 1.3 if is_dian else defense) * (100.0 + def_p - brc_p))
    r = cdiv(r if r >= 0 else r - 99, 100) - ctrunc(brc_a)
    r = cdiv(r, 10) if r >= 0 else -30
    if is_zhi and r < 10:
        r = 10
    if r > def_max:
        r = def_max
    return r


# ═══════════════════════ 战斗前初始化（calcBattle 2601-2927 行）═══════════════════════

def prepare_for_battle(attacker, defender, rng, counter=0):
    """战斗前双方 buff 结算（calcBattle 开头到主循环之前）。返回 (b[2], level_power)。

    ⚠️ 原地修改传入的 BStat（C++ 引用语义）；调用前用 clone() 保护原数据。
    """
    b = [attacker, defender]

    level_power = 0
    # 野怪不计算攻防等级（atkLvl=-1 时跳过）；环境争夺减 rankLevel
    if b[1].atkLvl >= 0:
        level_power = min(b[0].atkLvl - b[1].atkLvl, 20)
        level_power -= b[1].rankLevel

    for i in range(2):
        if b[i].role < NPC_COUNT_OLD:  # 旧版怪：前缀表展开
            init_pref_table()
            prefix_count = b[i].psvSkl % 5
            prefix = b[i].psvSkl // 5
            table = PREF_TABLE[(prefix, prefix_count)]
            index = counter % len(table)
            counter //= len(table)
            b[i].psvSkl = table[index]
        elif b[i].role == ROLE_LIU:
            prepare_liu_stat(b[i], rng)

    # 暴击/技能率对冲（双方基数合并后按 A/B 比例削顶）
    s_rate_rdc = ((b[0].sRateB + b[1].sRateB) * REDUCE_RATE_A
                  + REDUCE_RATE_B / 2) / REDUCE_RATE_B
    c_rate_rdc = ((b[0].cRateB + b[1].cRateB) * REDUCE_RATE_A
                  + REDUCE_RATE_B / 2) / REDUCE_RATE_B
    for i in range(2):
        me, op = b[i], b[1 - i]
        hp_m_add = me.hpMR
        sld_m_add = me.sldMR
        me.sRateB = me.sRateB - s_rate_rdc if me.sRateB > s_rate_rdc else 0.0
        me.cRateB = me.cRateB - c_rate_rdc if me.cRateB > c_rate_rdc else 0.0
        me.sRateR += me.amul[AMUL_SKL]
        if me.role == ROLE_WEI:
            me.sRateR *= 1.1
        me.sRateP = (me.sRateB * 100 / (me.sRateB + 99)) * (me.sRateR / 100.0)
        me.cRateP = me.cRateB * 100 / (me.cRateB + 99)
        me.pAtkR += me.amul[AMUL_PATK]
        me.mAtkR += me.amul[AMUL_MATK]
        if me.myst & MYST_SHIELD:
            op.hpRecRR += 40
            op.sldRecRR += 40
        if me.psvSkl & AURA_XUE:
            me.hpRecRR -= 10
            me.sldRecRR -= 10
        if me.psvSkl & AURA_SHANG:
            op.hpRecRR += 70
        if me.psvSkl & AURA_SHEN:
            op.sldRecRR += 70
        if not (me.psvSkl & FLAG_STAT) and (me.psvSkl & AURA_BI):
            me.pBrcP = int(me.pBrcP * 1.15)
            me.pBrcA = int(me.pBrcA * 1.15)
        if not (me.psvSkl & FLAG_STAT) and (me.psvSkl & AURA_MO):
            me.mBrcP = int(me.mBrcP * 1.15)
            me.mBrcA = int(me.mBrcA * 1.15)
        if me.role == ROLE_WU:
            me.pAtkR += 30
            me.mAtkR += 30
            me.spdRR -= 30
        if me.role == ROLE_MO:
            me.mBrcA += int((me.bugPoint if me.bugPoint > 0
                             else me.tSpr + me.tInt) * 0.2)
        me.hpRecRR -= me.amul[AMUL_REC]
        me.sldRecRR -= me.amul[AMUL_REC]
        me.spdRR -= me.amul[AMUL_SPD]
        me.cRateP += me.amul[AMUL_CRT]
        me.cDef += me.amul[AMUL_CRTR]
        me.sDef += me.amul[AMUL_SKLR]
        if not (me.psvSkl & FLAG_STAT):
            if me.myst & MYST_CLOAK:
                sld_m_add += 50
            if me.myst & MYST_THORN:
                me.rflP += 25.0
            if me.psvSkl & AURA_SHI:
                me.pRdc += int(me.lvl * 2.0 * (1 + me.wish[WISH_SHI_BUF] * 0.05))
                me.mRdc += int(me.lvl * 2.0 * (1 + me.wish[WISH_SHI_BUF] * 0.05))
            if me.psvSkl & AURA_XIN:
                me.hpM += int(me.lvl * 10.0 * (1 + me.wish[WISH_XIN_BUF] * 0.05))
                me.sldM += int(me.lvl * 10.0 * (1 + me.wish[WISH_XIN_BUF] * 0.05))
            if me.psvSkl & AURA_XUE:
                me.lchP += 10.0
            if me.psvSkl & AURA_CI:
                me.pDefB += int(me.pDefB / 10.0)
                me.mDefB += int(me.mDefB / 10.0)
                me.rflP += 10.0
            if me.psvSkl & AURA_JU:
                me.spdB = int(me.spdB * 1.3)
                me.spdA = int(me.spdA * 1.3)
            if me.role == ROLE_MO:
                sld_m_add += 25
            if me.role == ROLE_LIN:
                hp_m_add += 30
            if me.role == ROLE_MENG:
                sld_m_add += 45 if (me.myst & MYST_TIARA) else 30
            if me.role == ROLE_AI:
                me.lchP += 15.0
            if me.role == ROLE_YI:
                hp_m_add += 20
            if me.role == ROLE_QI:
                hp_m_add += 15
            if me.role == ROLE_MING:
                hp_m_add += 90
                me.pDefB = int(me.pDefB * 0.5)
                me.pDefA = int(me.pDefA * 0.5)
                me.mDefB = int(me.mDefB * 0.5)
                me.mDefA = int(me.mDefA * 0.5)
            if me.role == ROLE_WU:
                hp_m_add += 30
                sld_m_add += 30
                me.pDefB = int(me.pDefB * 1.15)
                me.mDefB = int(me.mDefB * 1.15)
            if me.role == ROLE_XI:
                me.lchP += 10.0
                hp_m_add += 50 if me.growth > 100000 else int(me.growth * 0.0005)
            if me.role == ROLE_XIA:
                sld_m_add += 20 if me.growth > 100000 else int(me.growth * 0.0002)
                me.mAtkR += 20 if me.growth > 100000 else int(me.growth * 0.0002)
            hp_m_add += me.amul[AMUL_HP]
            sld_m_add += me.amul[AMUL_SLD]
            me.lchP += me.amul[AMUL_LCH]
            me.rflP += me.amul[AMUL_RFL]
            if int(me.rflP) >= 150:
                me.rflP = 150.0
        me.hpM *= 1 + hp_m_add / 100.0
        me.sldM *= 1 + sld_m_add / 100.0
        me.hp = me.hpM
        me.sld = me.sldM
        me.hpPot = me.sldPot = False
        me.ziFlag = False
        me.minFlag = (me.role == ROLE_MIN)
        me.qiAbsorb = 0.0

    # 霞/雅二次处理（需双方就位后）
    for i in range(2):
        me, op = b[i], b[1 - i]
        if me.role == ROLE_XIA:
            me.mBrcA += int((op.pDefB + op.pDefA) * 0.35)
            if me.myst & MYST_LIMPIDWAND:
                me.mBrcA += (op.mDefB + op.mDefA) * 0.15
            if me.mAtkB + me.mAtkA > op.mAtkB + op.mAtkA:
                me.mAtkB = int(me.mAtkB * 1.3)
                me.mAtkA = int(me.mAtkA * 1.3)
            else:
                me.spdB = int(me.spdB * 1.3)
                me.spdA = int(me.spdA * 1.3)
        if me.role == ROLE_YA:
            if me.mode == 0 or (me.myst & MYST_FIERCE):
                me.pDefB = int(me.pDefB * 1.2)
                me.pDefA = int(me.pDefA * 1.2)
                me.mDefB = int(me.mDefB * 1.2)
                me.mDefA = int(me.mDefA * 1.2)
            if me.mode == 1 or (me.myst & MYST_FIERCE):
                op.mAtkB = int(op.mAtkB * 0.7)
                op.mAtkA = int(op.mAtkA * 0.7)
                op.pAtkB = int(op.pAtkB * 0.7)
                op.pAtkA = int(op.pAtkA * 0.7)
                op.spdB = int(op.spdB * 0.7)
                op.spdA = int(op.spdA * 0.7)

    # 伊：回血恢复清零
    for i in range(2):
        if b[i].role == ROLE_YI and b[i].hpRecRR > 0:
            b[i].hpRecRR = 0

    # 往返车票标记 / 速度计初始化
    for i in range(2):
        b[i].piaoFlag = bool(b[i].psvSkl & AURA_PIAO or b[1 - i].psvSkl & AURA_PIAO)
        b[i].spdC = (1.0 if b[i].psvSkl & AURA_SHAN
                     else (b[i].spdB + b[i].spdA) * (1 - b[i].spdRR / 100.0))

    return b, level_power


# ═══════════════════════ 六边形战士随机属性（prepareLiuStat, 2051 行）═══════════════════════

def prepare_liu_stat(b, rng):
    """六边形战士（ROLE_LIU）六类随机属性（2051-2200 行逐分支对拍）。

    psvSkl 即 cat 编码：各 2bit 类别选择（3=随机）+ 中间夹带系数位。
    """
    cat = b.psvSkl
    b.psvSkl = 0
    L = float(b.lvl)

    # Cat0: 攻击
    v = cat & 3
    v = rng.rand(3) if v == 3 else v
    coef = cat >> 24 & 7
    p_coef = (rng.rand(5) if coef == 7 else coef) + 28
    coef = cat >> 27 & 7
    m_coef = (rng.rand(5) if coef == 7 else coef) + 28
    if v == 0:
        b.pAtkB = L * p_coef * 3.0
        b.mAtkB = (L * m_coef + 9) / 10
        b.psvSkl |= AURA_FEI
    elif v == 1:
        b.pAtkB = (L * p_coef + 9) / 10
        b.mAtkB = L * m_coef * 3.0
        b.psvSkl |= AURA_BO
    else:
        b.pAtkB = (L * p_coef + 9) / 10
        b.mAtkB = L * m_coef * 3.0
        b.psvSkl |= AURA_FEI | AURA_BO

    # Cat1: 穿透/技能率/暴击率
    v = cat >> 2 & 3
    v = rng.rand(3) if v == 3 else v
    if v == 0:
        b.pBrcP = b.mBrcP = 20.0
        b.pBrcA = b.mBrcA = L * 2.0
        b.sRateB = L * 4.0; b.cRateB = L * 1.0
        b.psvSkl |= AURA_HONG
    elif v == 1:
        b.pBrcP = b.mBrcP = 20.0
        b.pBrcA = b.mBrcA = L * 2.0
        b.sRateB = L * 1.0; b.cRateB = L * 4.0
        b.psvSkl |= AURA_HONG
    else:
        b.pBrcP = b.mBrcP = 70.0
        b.pBrcA = b.mBrcA = L * 2.0
        b.cBrcP = 30.0
        b.sRateB = L * 1.0; b.cRateB = L * 1.0
        b.psvSkl |= AURA_BI | AURA_MO

    # Cat2: 攻速/吸血
    v = cat >> 4 & 3
    v = rng.rand(3) if v == 3 else v
    if v == 0:
        b.spdB = L * 6.0; b.lchP = 10.0
        b.psvSkl |= AURA_XIAO | AURA_RE | AURA_JU
    elif v == 1:
        b.spdB = L * 1.0; b.lchP = 60.0
        b.psvSkl |= AURA_HOU
    else:
        b.spdB = L * 1.0; b.lchP = 10.0
        b.aAtk = (b.pAtkB + 4) / 5 + (b.mAtkB + 4) / 5

    # Cat3: 血/盾
    v = cat >> 6 & 3
    v = rng.rand(3) if v == 3 else v
    coef = cat >> 12 & 63
    hp_coef = (rng.rand(41) if coef == 63 else coef) + 280
    coef = cat >> 18 & 63
    sld_coef = (rng.rand(41) if coef == 63 else coef) + 280
    if v == 0:
        b.hp = b.hpM = L * hp_coef
        b.sld = b.sldM = L * sld_coef
        b.hpRecP = b.sldRecP = 10
        b.psvSkl |= AURA_DIAN
    elif v == 1:
        b.hp = b.hpM = L * hp_coef * 3.0
        b.sld = b.sldM = (L * sld_coef + 9) / 10
        b.psvSkl |= AURA_SHENG | AURA_REN
    else:
        b.hp = b.hpM = (L * hp_coef + 9) / 10
        b.sld = b.sldM = L * sld_coef * 6.0
        b.psvSkl |= AURA_DUN | AURA_ZHI

    # Cat4: 防御/反弹
    v = cat >> 8 & 3
    v = rng.rand(3) if v == 3 else v
    if v == 0:
        b.pDefB = L * 6.0; b.mDefB = L * 2.0; b.rflP = 10.0
    elif v == 1:
        b.pDefB = L * 2.0; b.mDefB = L * 6.0; b.rflP = 10.0
    else:
        b.pDefB = L * 2.0; b.mDefB = L * 2.0; b.rflP = 40.0
        b.psvSkl |= AURA_CI

    # Cat5: 其他
    v = cat >> 10 & 3
    v = rng.rand(3) if v == 3 else v
    if v == 0:
        b.psvSkl |= AURA_SHANG | AURA_SHEN
    elif v == 1:
        b.psvSkl |= AURA_DUNH | AURA_WU
    else:
        b.psvSkl |= AURA_JUE | AURA_E


# ═══════════════════════ 战斗主循环（calcBattle, 2601-3825 行）═══════════════════════

class BResult:
    """战斗结果：winner 0=攻方胜 1=守方胜 2=平局；rate=存活方(血+盾)/(血上限+盾上限)。"""

    __slots__ = ("winner", "rate")

    def __init__(self, winner, rate=1.0):
        self.winner = winner
        self.rate = rate


def calc_battle(attacker, defender, rng, show_detail=False):
    """100 回合战斗模拟（newkf.cpp calcBattle 逐段对拍）。

    ⚠️ 原地修改 attacker/defender（C++ 引用语义）——调用方传 clone()。
    返回 BResult。rng 用 Minstd 保证与 C++ 同序列随机数。
    """
    b, level_power = prepare_for_battle(attacker, defender, rng)
    zou_flag = bool(b[0].psvSkl & AURA_ZOU or b[1].psvSkl & AURA_ZOU)
    pen_flag = bool(b[0].psvSkl & AURA_PEN or b[1].psvSkl & AURA_PEN)
    round_counter = 0
    last_side = 0
    round_n = [0, 0]

    round = 0
    while True:
        round += 1
        # --- 出手方判定（2929-2944 行）：速度计时间槽 ---
        s = 0 if b[0].spdC >= b[1].spdC else 1
        b[s].spdC -= b[1 - s].spdC
        b[1 - s].spdC = 0.0
        ren_counter = 3
        if b[1 - last_side].renCounter in (3, 4):
            ren_counter = b[1 - last_side].renCounter
        elif (b[last_side].tAgi > b[1 - last_side].tAgi
              and b[last_side].tAgi >= b[1 - last_side].tAgi * 6):
            ren_counter = 4
        if round_counter == ren_counter and (b[1 - last_side].psvSkl & AURA_REN):
            s = 1 - last_side
            b[s].spdC = 1.0
        b0, b1 = b[s], b[1 - s]
        if s != last_side:
            last_side = s
            round_counter = 0
        round_counter += 1
        round_n[s] += 1

        pa = [0.0, 0.0]
        ma = [0.0, 0.0]
        aa = [0.0, 0.0]
        hd = [0, 0]
        hr = [0, 0]
        sd = [0, 0]
        sr = [0, 0]

        # --- 技能/暴击判定（3028-3060 行）---
        skl_type = 0
        is_s = rng.rand100() < int(b0.sRateP)
        is_c = rng.rand100() < int(b0.cRateP)
        is_e = bool(b0.psvSkl & AURA_E) and rng.rand100() < 1
        if b0.myst & MYST_CLAYMORE:
            is_c = True
        if b0.role == ROLE_MO and (b0.myst & MYST_WAND) and b0.sklC == 0:
            is_s = True
            b0.sklC = 1
        if b0.role == ROLE_MIN and is_s:
            skl_type = rng.rand(3) + 1
        if b0.role == ROLE_XI and b1.hp + b1.sld < (b1.hpM + b1.sldM) / 2:
            is_c = True
        is_mc = bool(b0.myst & MYST_BRACELET) and is_c and rng.rand100() < 20
        if b0.role == ROLE_QI and round_n[s] % 3 == 0:
            is_s = True

        # --- 回合开始回血基准 ---
        b0.hpS = b0.hpM
        b0.sldS = b0.sldM
        b1.hpS = b1.hpM
        b1.sldS = b1.sldM

        pa[s] = b0.pAtkB + b0.pAtkA
        ma[s] = b0.mAtkB + b0.mAtkA
        aa[s] = b0.aAtk
        if b1.spdRR >= SPEED_REDUCE_MAX:
            b1.spdRR = SPEED_REDUCE_MAX
        if b1.role == ROLE_YI and b1.spdRR > 0:
            b1.spdRR = 0
        _attack_phase(b, s, round, pa, ma, aa, hr, sr, is_s, is_c, is_mc,
                      is_e, skl_type, rng, round_n)

        # 攻击值经各天赋/暴击/技能倍率后的附加穿透与反伤计算在 _damage_phase
        # （伤害结算与回血回盾结算都在其中，_settle_phase 由其末尾调用）
        _damage_phase(b, s, round, pa, ma, aa, hd, hr, sd, sr,
                      is_c, is_s, level_power, zou_flag, pen_flag)

        # --- 胜负判定（3795-3810 行）---
        wf = 0
        for i in range(2):
            if b[i].hp == 0:
                wf |= 1 << (1 - i)
        if wf:
            winner = wf - 1
            rate = (1.0 if wf == 3 else
                    1.0 * (b[winner].hp + b[winner].sld)
                    / (b[winner].hpM + b[winner].sldM))
            return BResult(winner, rate)

        # 守方速度计恢复（3812-3814 行）
        b1 = b[1 - s]
        b1.spdC = (1 if b1.psvSkl & AURA_SHAN
                   else (b1.spdB + b1.spdA) * (1 - b1.spdRR / 100.0))
        if b1.spdC <= 0:
            b1.spdC = 1

        if round == 100:
            break

    return BResult(2, 1.0)  # 100 回合平局


# ═══════════════════════ 攻击阶段（3060-3340 行）：技能加成与倍率 ════════════════════════

def _attack_phase(b, s, round, pa, ma, aa, hr, sr, is_s, is_c, is_mc, is_e,
                  skl_type, rng, round_n):
    """攻击值构建：各角色技能加成 → 光环加成 → 暴击/技能倍率 → 攻击%修正。"""
    b0, b1 = b[s], b[1 - s]

    if b0.role == ROLE_AI:
        b0.sklC += 2 if (b0.myst & MYST_DAGGER) else 1
        aa[s] += int((b0.pAtkB + b0.pAtkA + b0.mAtkB + b0.mAtkA) * 9
                     * ((20 + b0.sklC * 3) if (b0.myst & MYST_DAGGER) else 20)
                     / 400.0)
        b1.pAtkR -= 1
        b1.mAtkR -= 1
    if b0.role == ROLE_MENG:
        b0.sklC += 2
        ma[s] += int(b0.sldM * 0.03 * b0.sklC
                     + (b0.mAtkA + b0.mAtkB) * 0.03 * b0.sklC)
        b1.spdRR += 1
    if b0.role == ROLE_YI:
        atk_plus = int((b0.pAtkB + b0.pAtkA + b0.mAtkB + b0.mAtkA) * 1.4)
        if b0.myst & MYST_COLORFUL:
            ma[s] += atk_plus
            pa[s] += atk_plus
        elif b1.pDefB + b1.pDefA > b1.mDefB + b1.mDefA:
            ma[s] += atk_plus
        else:
            pa[s] += atk_plus
    if b0.role == ROLE_WU and (b0.myst & MYST_RING):
        pa[s] += int((b0.sklC + 100) * 0.2)
        ma[s] += int((b0.sklC + 100) * 0.2)
    if b0.role == ROLE_XI and b0.hp < b0.hpM / 2:
        pa[s] += b0.hpM - b0.hp
    if b0.role == ROLE_MIN:
        b0.minFlag = bool(b0.myst & MYST_REFRACT
                          and int(b1.hp) == int(b1.hpM)
                          and int(b1.sld) == int(b1.sldM))
    if b0.psvSkl & AURA_XIAO:  # 削骨之痛：对方血盾各1.5%绝伤
        aa[s] += int(b1.hpM * 0.015) + int(b1.sldM * 0.015)
    if (b0.psvSkl & AURA_WU) and round > 15:  # 午时已到 15 回合后+25%全攻绝伤
        aa[s] += int((b0.pAtkB + b0.pAtkA + b0.mAtkB + b0.mAtkA) * 0.25)
    if b0.psvSkl & AURA_FEI:  # 沸血之志：+18%最大生命物攻
        pa[s] += int(b0.hpM * 0.18)
    if b0.myst & MYST_ASSBOW:
        pa[s] += int(b1.sld * 0.18)
    if b0.myst & MYST_SPEAR:
        ma[s] += int(b1.hp * 0.18)
    if is_e:  # 恶意抽奖（1% 概率）：攻×30
        pa[s] += (b0.pAtkB + b0.pAtkA) * 30
        ma[s] += (b0.mAtkB + b0.mAtkA) * 30
    if b0.role == ROLE_YA:
        pa[s] += int(pa[s] * 0.2 * round) + int((b0.pAtkB + b0.pAtkA) * 0.2 * round)
        if b0.psvSkl & AURA_FEI:  # sb bug：沸血部分再乘一次回合系数
            pa[s] += int(b0.hpM * 0.18 * 0.2 * round)

    # 技能触发时的先手效果（雅削上限）
    if is_s and b0.role == ROLE_YA:
        b0.pAtkB += int(b1.hpM * 0.05) + int(b1.sldM * 0.05)
        b1.hpM = math.ceil(b1.hpM * 0.95)
        b1.sldM = math.ceil(b1.sldM * 0.95)

    # --- 暴击倍率（3237-3257 行）：物/绝×2.0、魔×1.5 ---
    if is_c:
        pa[s] *= 2.0
        ma[s] = int(ma[s] * 1.5)
        aa[s] *= 2.0
        if b0.role == ROLE_MIN:
            pa[s] = int(pa[s] * 1.55)
            ma[s] = int(ma[s] * 1.55)
            aa[s] = int(aa[s] * 1.55)
        if b0.role == ROLE_LIN and b0.sklC == 0:
            pa[s] += b0.hpM * 0.5
            b0.sklC = 1
        if b0.psvSkl & AURA_JU:  # 飓风之力：攻速差转绝伤
            spd = int((b0.spdB + b0.spdA) * (1 - b0.spdRR / 100.0))
            if spd <= 0:
                spd = 1
            spd2 = int((b1.spdB + b1.spdA) * (1 - b1.spdRR / 100.0))
            if spd2 <= 0:
                spd2 = 1
            aa[s] += int((spd * 12 if spd > spd2 * 3 else spd * 9) / 5)
        if b0.myst & MYST_BLADE:  # 神秘刃：暴击附带物攻50%绝伤
            aa[s] += int((b0.pAtkB + b0.pAtkA) / 2)

    # --- 角色技能主体（switch 3264-3411 行）---
    if is_s:
        _skill_effect(b, s, skl_type, pa, ma, aa, hr, sr)

    # --- 薇：21% 对方血盾伤害 ---
    if b0.role == ROLE_WEI:
        p_add = int((b1.hpM + b1.sldM) * 0.21)
        if b0.myst & MYST_HUNT:
            pa[s] += p_add * 0.7
            aa[s] += p_add * 0.3
        else:
            pa[s] += p_add
        if b0.sklC:
            pa[s] = int(pa[s] * 1.4)
    if is_mc:  # 特殊暴击：魔伤×2
        ma[s] *= 2.0
    if b0.psvSkl & AURA_HOU:  # 后发制人：+24%/层
        pa[s] = int(pa[s] * (1 + b0.houC * 0.24))
        ma[s] = int(ma[s] * (1 + b0.houC * 0.24))
        aa[s] = int(aa[s] * (1 + b0.houC * 0.24))
        b0.houC = 0
    if b1.psvSkl & AURA_HOU:
        b1.houC += 1
    pa[s] = int(pa[s] * (1 + b0.pAtkR * 0.01))
    ma[s] = int(ma[s] * (1 + b0.mAtkR * 0.01))


def _skill_effect(b, s, skl_type, pa, ma, aa, hr, sr):
    """角色技能主体（newkf.cpp 3270-3411 行 switch 逐 case 对拍）。"""
    b0, b1 = b[s], b[1 - s]
    role = b0.role
    if role == ROLE_MU:
        pa[s] += b0.pAtkB * 3.0
        hr[s] += int(b0.hpM / 10)
    elif role == ROLE_ZHU:
        ma[s] += b0.mAtkB
        b1.spdRR += 20
    elif role == ROLE_DENG:
        ma[s] += int(b0.sld * 0.3)
        sr[s] += int(b0.sldM / 10)
    elif role == ROLE_SHOU:
        pa[s] += int(b0.hpM / 5)
        hr[s] += int(b0.hpM * 0.3)
    elif role in (ROLE_MU2, ROLE_YU2):
        pa[s] += b0.pAtkB * 5.0
    elif role == ROLE_ZHU2:
        ma[s] += b0.mAtkB * 5.0
    elif role == ROLE_DENG2:
        ma[s] += int(b0.sldM * 0.4)
    elif role in (ROLE_SHOU2, ROLE_HAO2, ROLE_LIU):
        pa[s] += int(b0.hpM * 0.4)
    elif role == ROLE_SHI:
        pa[s] += b0.pAtkB * 3.0
        ma[s] += b0.mAtkB * 3.0
        aa[s] += b0.pAtkB + b0.mAtkB
    elif role == ROLE_MO:
        ma_dif = 0
        if b0.growth > 0:
            ma_dif = b0.growth
        elif b0.tSpr > b0.tInt:
            ma_dif = int(int((b0.tSpr / b0.tInt - 1) * 100) / 2)
        else:
            ma_dif = int(int((b0.tInt / b0.tSpr - 1) * 100) / 2)
        ma_dif = min(ma_dif, 1000)
        atk = ((b0.mAtkB + b0.mAtkA) * 0.35 + b0.sldM * 0.05) * (1 + ma_dif / 100.0)
        ma[s] += int(atk)
        if b0.myst & MYST_WAND:
            ma[s] += int(atk * 0.4)
    elif role == ROLE_LIN:
        pa[s] += int((b0.pAtkB + b0.pAtkA) * 2.2)
        ma[s] += int((b0.pAtkB + b0.pAtkA) * 2.2)
    elif role == ROLE_AI:
        aa[s] += int((b1.hp + b1.sld) * 0.13 * b0.sklC)
        b0.sklC = 0
    elif role == ROLE_MENG:
        b0.sklC += 7
        ma[s] += int((b0.mAtkB + b0.mAtkA) * b0.sklC / 4.0)
        b1.spdRR += int(b0.sklC / 2)
        if b1.spdRR > 100:
            b1.spdRR = 100
    elif role == ROLE_WEI:
        b0.sklC = 1
    elif role == ROLE_YI:
        dmg = int(max(b1.sld, b1.hp) * 0.15)
        aa[s] += dmg
        hr[s] += dmg
    elif role == ROLE_MING:
        dmg = int(b0.hpM - b0.hp)
        ma[s] += dmg
        hr[s] += dmg / 2
    elif role == ROLE_MIN:
        b0.sklC = -skl_type
    elif role == ROLE_WU:
        pa[s] += b0.sklC + 100
        ma[s] += b0.sklC + 100
    elif role == ROLE_XI:
        hp_rate = (b0.hpM - b0.hp) * 100 / b0.hpM
        dmg = (b0.pAtkB + b0.pAtkA) * 3 * (1 + hp_rate / 100.0)
        if (b0.hp + b0.sld < (b0.hpM + b0.sldM) / 10.0
                or b1.hp + b1.sld < (b1.hpM + b1.sldM) / 10.0):
            aa[s] += dmg
        else:
            pa[s] += dmg
    elif role == ROLE_XIA:
        dmg_add = int((b1.mDefB + b1.mDefA) / 10)
        if dmg_add > 200:
            dmg_add = 200
        ma[s] += (b0.mAtkB + b0.mAtkA) * 2 * (1 + dmg_add / 100.0)
    elif role == ROLE_YA:
        pa[s] += int((b0.pAtkB + b0.pAtkA) * 3.0)
    elif role == ROLE_QI:
        pa[s] += int(b0.qiAbsorb)


# ═══════════════════════ 伤害阶段（3340-3660 行）═══════════════════════

def _damage_phase(b, s, round, pa, ma, aa, hd, hr, sd, sr,
                  is_c, is_s, level_power, zou_flag, pen_flag):
    """反伤计算 → 天赋修正 → 三路伤害（物/魔/绝）结算。"""
    b0, b1 = b[s], b[1 - s]

    # --- 反伤基数（3355-3365 行）：mRfl = (物+魔)*0.7 + 绝*0.5，再×反伤% ---
    rfl_p_fixed = int(b1.rflP / 2.0) if (b0.psvSkl & AURA_DI) else b1.rflP
    p_rfl = 0.0
    m_rfl = ((pa[s] * 0.7 + ma[s] * 0.7 + aa[s] * 0.5)
             * (rfl_p_fixed / 100.0))
    if b1.role == ROLE_MO:  # 默额外反伤
        mo_rfl = (((b1.mAtkB + b1.mAtkA) * 0.55) + b1.sldM * 0.07) \
            * (1 + b1.mAtkR * 0.01)
        m_rfl += int(mo_rfl)
    if b0.role == ROLE_MING:  # 冥：反伤 40% 加到自己攻击
        pa[s] += int(p_rfl * 0.4)
        ma[s] += int(m_rfl * 0.4)
    if b1.role == ROLE_MING:
        p_rfl += int(pa[s] * 0.4)
        m_rfl += int(ma[s] * 0.4)
    if b1.myst & MYST_CAPE:  # 神秘斗篷：50% 物伤转魔伤
        convert = int(pa[s] / 2)
        pa[s] -= convert
        ma[s] += convert
    if b0.psvSkl & AURA_DIAN:  # 点到为止：自身输出×0.7
        pa[s] = int(pa[s] * 0.7)
        ma[s] = int(ma[s] * 0.7)
    if b1.psvSkl & AURA_DIAN:
        p_rfl = int(p_rfl * 0.7)
        m_rfl = int(m_rfl * 0.7)
    if b0.psvSkl & AURA_ZI:  # 自信回头：首击×1.5 后续×0.9
        if not b0.ziFlag:
            b0.ziFlag = True
            pa[s] = int(pa[s] * 1.5)
            ma[s] = int(ma[s] * 1.5)
            aa[s] = int(aa[s] * 1.5)
        else:
            pa[s] = int(pa[s] * 0.9)
            ma[s] = int(ma[s] * 0.9)
            aa[s] = int(aa[s] * 0.9)
    if zou_flag:  # 致命节奏：伤害随回合暴涨（0.3+0.2*回合，上限10倍）
        zou_r = min(0.3 + (round - 1) * 0.2, 10.0)
        pa[s] *= zou_r
        ma[s] *= zou_r
        aa[s] *= zou_r
        p_rfl *= zou_r
        m_rfl *= zou_r
    if pen_flag:  # 天降花盆：暴击1.4倍/普攻0.6倍
        pen_r = 1.4 if is_c else 0.6
        pa[s] *= pen_r
        ma[s] *= pen_r
        aa[s] *= pen_r
    # 等级压制：先手攻方 +3%/级（上限20级=60%），后手反向
    if s == 0 and level_power > 0:
        pa[s] *= 1 + 0.03 * level_power
        ma[s] *= 1 + 0.03 * level_power
        aa[s] *= 1 + 0.03 * level_power
    if s == 1 and level_power < 0:
        pa[s] *= 1 - 0.03 * level_power
        ma[s] *= 1 - 0.03 * level_power
        aa[s] *= 1 - 0.03 * level_power
    if is_c:  # 暴击受对方暴抗
        pa[s] *= 1 - b1.cDef / 100.0
        ma[s] *= 1 - b1.cDef / 100.0
        aa[s] *= 1 - b1.cDef / 100.0
    if is_s:  # 技能受对方技能抗
        s_def = b1.sDef + (35 if b1.role == ROLE_QI else 0)
        pa[s] *= 1 - s_def / 100.0
        ma[s] *= 1 - s_def / 100.0
        aa[s] *= 1 - s_def / 100.0
    if s == 0 and level_power < 0:
        pa[s] *= 1 + 0.03 * level_power
        ma[s] *= 1 + 0.03 * level_power
        aa[s] *= 1 + 0.03 * level_power
    if s == 1 and level_power > 0:
        pa[s] *= 1 - 0.03 * level_power
        ma[s] *= 1 - 0.03 * level_power
        aa[s] *= 1 - 0.03 * level_power

    # C++ 反伤是同一变量原地修改（mRfl *= ...后在结算处使用），
    # Python 数值传参不可变 → 返回修改后值给 _settle_phase（对齐源码语义）
    p_rfl, m_rfl = _damage_apply(b, s, pa, ma, aa, hd, hr, sd, sr,
                                 p_rfl, m_rfl, level_power, is_c)
    _settle_phase(b, s, hd, hr, sd, sr, p_rfl, m_rfl)


def _damage_apply(b, s, pa, ma, aa, hd, hr, sd, sr, p_rfl, m_rfl,
                  level_power, is_c):
    """三路伤害过减伤/护盾结算（3458-3660 行逐段对拍）。

    减伤率 dr 对血按 (1-dr/100)，对盾减半 (1-dr/200)；dr<0 时盾按 (1-dr/100)。
    护盾优先：伤害先扣盾（物理对盾有 1.5 系数），溢出转生命。
    """
    b0, b1 = b[s], b[1 - s]
    dun_mul = 1.25 if (b1.psvSkl & AURA_DUN) else 1.5  # 复合护盾降低对盾系数
    # C++ 三段伤害共享同一 sldRemain/sldActive（3460 行）：魔伤先扣盾，
    # 物/绝伤接着打剩余盾 —— 不能每段重读 b1.sld，否则盾被重复扣变负数
    sld_remain = int(b1.sld)
    sld_active = sld_remain > 0

    # --- 魔法伤害 ---
    if ma[s] > 0:
        dr = calc_def_rate(
            b1.mDefB + b1.mDefA, b1.amul[AMUL_MDEF],
            b0.mBrcP + (30 if (b0.psvSkl & AURA_BO and b0.hp > b0.hpM * 0.7
                               and b0.sld > b0.sldM * 0.7) else 0.0),
            b0.cBrcP if is_c else 0.0, b0.mBrcA,
            80 if (b1.psvSkl & AURA_SHENG) else 75,
            bool(b1.psvSkl & AURA_DUNH), bool(b1.psvSkl & AURA_ZHI),
            b0.lvl / 2 if (b0.psvSkl & AURA_HONG) else -1,
            bool(b1.psvSkl & AURA_DIAN))
        ma2 = int(ma[s])
        if b1.role == ROLE_MIN and (b1.minFlag or b1.sklC == -2):
            ma2 = 0
        if b1.role == ROLE_WEI and b1.sklC:
            ma2 /= 10
        if b1.psvSkl & AURA_JUE:
            ma2 *= 0.8
        if b1.psvSkl & AURA_DI:  # 绝对底线：魔伤×0.1
            ma2 *= 0.1
        if sld_active:
            sh = 1 - dr / 200.0 if dr >= 0 else 1 - dr / 100.0
            sd_max = int(ma2 * sh) - b1.mRdc
            if sd_max < 0:
                sd_max = 0
            if sd_max <= sld_remain:
                sld_remain -= sd_max
                sd[1 - s] += sd_max
                ma2 = 0
            else:
                ma2 = (sd_max - sld_remain) / sh
                sd[1 - s] += sld_remain
                sld_remain = 0
        hd2 = int(ma2 * (1 - dr / 100.0)) - b1.mRdc
        if hd2 > 0:
            hd[1 - s] += hd2

    # --- 物理伤害 ---
    if pa[s] > 0:
        dr = calc_def_rate(
            b1.pDefB + b1.pDefA, b1.amul[AMUL_PDEF],
            b0.pBrcP, b0.cBrcP if is_c else 0.0, b0.pBrcA,
            80 if (b1.psvSkl & AURA_SHENG) else 75,
            bool(b1.psvSkl & AURA_DUNH), bool(b1.psvSkl & AURA_ZHI),
            b0.lvl / 2 if (b0.psvSkl & AURA_HONG) else -1,
            bool(b1.psvSkl & AURA_DIAN))
        pa2 = int(pa[s])
        if b1.role == ROLE_MIN and (b1.minFlag or b1.sklC == -1):
            pa2 = 0
        if b1.role == ROLE_WEI and b1.sklC:
            pa2 /= 10
        if b1.psvSkl & AURA_JUE:
            pa2 *= 0.8
        if b1.psvSkl & AURA_DI:  # 绝对底线：物伤×0.1
            pa2 *= 0.1
        if sld_active:
            sh = 1 - dr / 200.0 if dr >= 0 else 1 - dr / 100.0
            sd_max = int(pa2 * dun_mul * sh) - b1.pRdc
            if sd_max < 0:
                sd_max = 0
            if sd_max <= sld_remain:
                sld_remain -= sd_max
                sd[1 - s] += sd_max
                pa2 = 0
            else:
                pa2 = (sd_max - sld_remain) / sh / dun_mul
                sd[1 - s] += sld_remain
                sld_remain = 0
        hd2 = int(pa2 * (1 - dr / 100.0)) - b1.pRdc
        if hd2 > 0:
            hd[1 - s] += hd2

    # --- 绝对伤害（3486-3497 行）：无视防御，直接打 ---
    if aa[s] > 0:
        aa2 = int(aa[s])
        if b1.role == ROLE_MIN and (b1.minFlag or b1.sklC == -3):
            aa2 = 0
        if b1.role == ROLE_WEI and b1.sklC:
            aa2 /= 10
        if b1.psvSkl & AURA_JUE:
            aa2 *= 0.8
        if b1.psvSkl & AURA_DI:  # 绝对底线：绝伤×0.12（减88%）
            aa2 *= 0.12
        if sld_active:
            if aa2 <= sld_remain:
                sld_remain -= aa2
                sd[1 - s] += aa2
                aa2 = 0
            else:
                aa2 -= sld_remain
                sd[1 - s] += sld_remain
                sld_remain = 0
        hd[1 - s] += aa2
    if b1.role == ROLE_WEI and b1.sklC:
        b1.sklC = 0

    # --- 吸血（3570-3572 行）：物/绝回血、魔/绝回盾 ---
    hr[s] += int((pa[s] + aa[s]) * b0.lchP / 200.0)
    sr[s] += int((ma[s] + aa[s]) * b0.lchP / 200.0)
    if b0.myst & MYST_VULTURE:
        hr[s] += int((ma[s] + aa[s]) * b0.lchP / 800.0)

    # --- 反伤结算（3438-3540 行）：反伤打回攻击方，同样过减伤/护盾 ---
    sld_remain = int(b0.sld)
    sld_active = sld_remain > 0
    if m_rfl > 0:
        dr = calc_def_rate(
            b0.mDefB + b0.mDefA, b0.amul[AMUL_MDEF],
            b1.mBrcP + (30 if (b1.psvSkl & AURA_BO and b1.hp > b1.hpM * 0.7
                               and b1.sld > b1.sldM * 0.7) else 0),
            0.0, b1.mBrcA,
            80 if (b0.psvSkl & AURA_SHENG) else 75,
            bool(b0.psvSkl & AURA_DUNH), bool(b0.psvSkl & AURA_ZHI), -1,
            bool(b0.psvSkl & AURA_DIAN))
        m_rfl *= 1 - 0.03 * level_power * (1 - 2 * s)
        ma2 = m_rfl
        if b0.role == ROLE_MIN and b0.sklC == -2:
            ma2 = 0
        if b0.psvSkl & AURA_JUE:
            ma2 *= 0.8
        if b0.psvSkl & AURA_DI:
            ma2 *= 0.1
        if sld_active:
            sh = 1 - dr / 200.0 if dr >= 0 else 1 - dr / 100.0
            sd_max = int(ma2 * sh) - b0.mRdc
            if sd_max < 0:
                sd_max = 0
            if sd_max <= sld_remain:
                sld_remain -= sd_max
                sd[s] += sd_max
                ma2 = 0
            else:
                ma2 = (sd_max - sld_remain) / sh
                sd[s] += sld_remain
                sld_remain = 0
        hd2 = int(ma2 * (1 - dr / 100.0)) - b0.mRdc
        if hd2 > 0:
            hd[s] += hd2
    if p_rfl > 0:
        dr = calc_def_rate(
            b0.pDefB + b0.pDefA, b0.amul[AMUL_PDEF],
            b1.pBrcP, 0.0, b1.pBrcA,
            80 if (b0.psvSkl & AURA_SHENG) else 75,
            bool(b0.psvSkl & AURA_DUNH), bool(b0.psvSkl & AURA_ZHI), -1,
            bool(b0.psvSkl & AURA_DIAN))
        p_rfl *= 1 - 0.03 * level_power * (1 - 2 * s)
        pa2 = p_rfl
        if b0.role == ROLE_MIN and b0.sklC == -1:
            pa2 = 0
        if b0.psvSkl & AURA_JUE:
            pa2 *= 0.8
        if b0.psvSkl & AURA_DI:
            pa2 *= 0.1
        if sld_active:
            sh = 1 - dr / 200.0 if dr >= 0 else 1 - dr / 100.0
            sd_max = int(pa2 * (1.25 if (b0.psvSkl & AURA_DUN) else 1.5) * sh) \
                - b0.pRdc
            if sd_max < 0:
                sd_max = 0
            if sd_max <= sld_remain:
                sld_remain -= sd_max
                sd[s] += sd_max
                pa2 = 0
            else:
                pa2 = (sd_max - sld_remain) / sh \
                    / (1.25 if (b0.psvSkl & AURA_DUN) else 1.5)
                sd[s] += sld_remain
                sld_remain = 0
        hd2 = int(pa2 * (1 - dr / 100.0)) - b0.pRdc
        if hd2 > 0:
            hd[s] += hd2

    return p_rfl, m_rfl


def _settle_phase(b, s, hd, hr, sd, sr, p_rfl, m_rfl):
    """回血回盾结算（3574-3790 行逐段对拍）。

    药水触发 → 冥吸治疗 → 琳/回血被动 → 回复减少率 → 琳/车票免死 → 扣血结萁。
    """
    b0, b1 = b[s], b[1 - s]

    # 反伤方吸血（3574-3577 行）：对反伤部分按 lchP/400 吸
    hr[1 - s] += int(p_rfl * b1.lchP / 400.0)
    sr[1 - s] += int(m_rfl * b1.lchP / 400.0)
    if b1.myst & MYST_VULTURE:
        hr[1 - s] += int(m_rfl * b1.lchP / 1600.0)
    if b1.myst & MYST_WOOD:  # 复苏战衣：回 5% 最大生命
        hr[1 - s] += b1.hpS / 20
    # 药水（3580-3593 行）：血/盾低于 80% 触发，回 0.5%*W
    if not b1.hpPot and int(b1.hp) <= int(b1.hpS * 0.8):
        hr[1 - s] += b1.hpS * b1.wish[WISH_HP_POT] / 200
        b1.hpPot = True
    if not b1.sldPot and int(b1.sld) <= int(b1.sldS * 0.8):
        sr[1 - s] += b1.sldS * b1.wish[WISH_SLD_POT] / 200
        b1.sldPot = True
    if b1.role == ROLE_MIN and b1.minFlag:
        b1.minFlag = False

    # 绮吸收（3601-3603 行）：受伤盾的一半累计
    if b0.role == ROLE_QI:
        b0.qiAbsorb += sd[1] * 0.5
    if b1.role == ROLE_QI:
        b1.qiAbsorb += sd[0] * 0.5

    # 梦被动减速 / 热血战魂（3605-3618 行）
    if b0.spdRR >= SPEED_REDUCE_MAX:
        b0.spdRR = SPEED_REDUCE_MAX
    if b0.role == ROLE_YI and b0.spdRR > 0:
        b0.spdRR = 0
    if b1.role == ROLE_MENG:
        b1.sklC += 1
        b0.spdRR += 4 if (b1.myst & MYST_TIARA) else 2
    if b0.psvSkl & AURA_RE:
        b0.spdRR -= 9

    # 琳回血 + 百分比/附加回复（3629-3642 行）
    for i in range(2):
        me = b[i]
        if me.role == ROLE_LIN:
            if me.myst & MYST_RIBBON:
                hr[i] += me.hpS * 0.06
            elif int(me.hp) <= int(me.hpS * 0.3):
                hr[i] += me.hpS * 0.06
            else:
                hr[i] += me.hpS * 0.03
        hr[i] += (me.hpS * me.hpRecP / 100 + me.hpRecA) / 4
        sr[i] += (me.sldS * me.sldRecP / 100 + me.sldRecA) / 4
        if hd[i] >= me.hp:  # 致死时回复清零
            hr[i] = 0
            sr[i] = 0

    # 冥吸治疗（3655-3670 行）：抽对方 60% 回复
    if b0.role == ROLE_MING:
        hr[s] += int(hr[1 - s] * 0.6)
        sr[s] += int(sr[1 - s] * 0.6)
        if b0.myst & MYST_DEVOUR:
            hr[s] += int(sr[1 - s] * 0.3)
    if b1.role == ROLE_MING:
        hr[1 - s] += int(hr[s] * 0.6)
        sr[1 - s] += int(sr[s] * 0.6)
        if b1.myst & MYST_DEVOUR:
            hr[1 - s] += int(sr[s] * 0.3)

    # 最终结萁（3672-3701 行）
    for i in range(2):
        me = b[i]
        hr[i] *= 1 - min(me.hpRecRR, 100) / 100.0
        sr[i] *= 1 - min(me.sldRecRR, 100) / 100.0
        # 琳免死（sklC==0 挡一次致命）
        if i == 1 - s and me.role == ROLE_LIN and me.sklC == 0 \
                and hd[i] >= me.hp + hr[i]:
            hd[i] = 0
            sd[i] = 0
            me.sklC = 1
        # 往返车票复活（一次性）
        elif me.piaoFlag and hd[i] >= me.hp + hr[i]:
            hd[i] = 0
            sd[i] = 0
            hr[i] = 0
            sr[i] = 0
            me.hp = me.hpM
            me.sld = me.sldM
            me.piaoFlag = False
        if me.psvSkl & AURA_PIAO or b[1 - i].psvSkl & AURA_PIAO:
            hr[i] *= 0.2
            sr[i] *= 0.2
        me.hp = me.hp - hd[i] + hr[i]
        if me.hp > me.hpM:
            me.hp = me.hpM
        if me.hp < 0:
            me.hp = 0
        me.sld = me.sld - sd[i] + sr[i]
        if me.sld > me.sldM:
            me.sld = me.sldM


# ═══════════════════════ 玩家角色模型（Player/Gear + preparePcBStat 2203 行）═══════════════════════

class Gear:
    """一件装备：type=部位类型(GEAR_*)，lvl=装备等级，percent=4 词条评分(%)，isMyst=神秘。"""

    __slots__ = ("type", "lvl", "percent", "isMyst")

    def __init__(self, type=0, lvl=0, percent=(0, 0, 0, 0), isMyst=False):
        self.type = type
        self.lvl = lvl
        self.percent = list(percent)
        self.isMyst = bool(isMyst)


# 装备类型名（386 行 gearName，索引=GEAR_* 枚举值）
GEAR_NAME = ["NONE", "SWORD", "BOW", "STAFF", "BLADE", "ASSBOW", "DAGGER",
             "WAND", "SHIELD", "CLAYMORE", "SPEAR", "COLORFUL", "LIMPIDWAND",
             "GLOVES", "BRACELET", "VULTURE", "RING", "DEVOUR", "REFRACT",
             "PLATE", "LEATHER", "CLOTH", "CLOAK", "THORN", "WOOD", "CAPE",
             "SCARF", "TIARA", "RIBBON", "HUNT", "FIERCE"]
GEAR_NAME_TO_TYPE = {n: i for i, n in enumerate(GEAR_NAME)}
# 部位槽：0 武器 / 1 饰品 / 2 防具 / 3 耳环（399 行 gearSlot）
GEAR_SLOT = [-1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
             1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3]
# 武器位中文名（游戏内叫法 ↔ 源码类型）
GEAR_CN = {"SWORD": "探险者之剑", "BOW": "探险者短弓", "STAFF": "探险者短杖",
           "BLADE": "狂信者的荣誉之刃", "ASSBOW": "反叛者的刺杀弓", "DAGGER": "幽梦匕首",
           "WAND": "光辉法杖", "SHIELD": "荆棘盾剑", "CLAYMORE": "陨铁重剑",
           "SPEAR": "饮血魔剑", "COLORFUL": "彩金长剑", "LIMPIDWAND": "清澄长杖",
           "GLOVES": "探险者手环", "BRACELET": "命师的传承手环", "VULTURE": "秃鹫手环",
           "RING": "海星戒指", "DEVOUR": "噬魔戒指", "REFRACT": "折光戒指",
           "PLATE": "探险者铁甲", "LEATHER": "探险者皮甲", "CLOTH": "探险者布甲",
           "CLOAK": "旅法师的灵光袍", "THORN": "荆棘重甲", "WOOD": "复苏战衣",
           "CAPE": "挑战斗篷", "SCARF": "探险者耳环", "TIARA": "占星师的耳饰",
           "RIBBON": "萌爪耳钉", "HUNT": "猎魔耳环", "FIERCE": "凶神耳环"}


class Player:
    """玩家角色卡配置（415 行 Player 结构体的 Python 版）。

    role: ROLE_*；lvl: 卡片等级；kfLvl: 争夺等级；attr: 六维加点数组；
    gear: [4]Gear（部位序）；auraSkl: 天赋位或集；growth: 成长值（舞技能计数用）。
    """

    def __init__(self, role, lvl, kfLvl, attr, gear=None, auraSkl=0,
                 wish=None, amul=None, growth=0, mode=0, rankLevel=0,
                 renCounter=3, bugPoint=0, alias=""):
        self.role = role
        self.lvl = lvl
        self.kfLvl = kfLvl
        self.attr = list(attr)
        self.gear = list(gear or [Gear(), Gear(), Gear(), Gear()])
        self.auraSkl = auraSkl
        self.wish = list(wish or [0] * WISH_COUNT)
        self.amul = list(amul or [0] * AMUL_COUNT)
        self.growth = growth
        self.mode = mode
        self.rankLevel = rankLevel
        self.renCounter = renCounter
        self.bugPoint = bugPoint
        self.alias = alias


def prepare_pc_bstat(pc):
    """玩家配置 → BStat（newkf.cpp preparePcBStat 2203-2310 行主干：六维换算）。

    装备词条加成在 _apply_gear()（下一块）；此处先给无装备的裸体面板。
    """
    b = BStat()
    b.mode = pc.mode
    b.renCounter = pc.renCounter
    b.bugPoint = pc.bugPoint
    b.rankLevel = pc.rankLevel

    # 攻防等级（2209-2210 行）：争夺等级/100，1600+ 封顶 16
    b.atkLvl = 16 if pc.kfLvl >= 1600 else cdiv(pc.kfLvl, 100)
    b.defLvl = b.atkLvl

    b.tStr, b.tAgi, b.tInt = pc.attr[ATTR_STR], pc.attr[ATTR_AGI], pc.attr[ATTR_INT]
    b.tVit, b.tSpr, b.tMnd = pc.attr[ATTR_VIT], pc.attr[ATTR_SPR], pc.attr[ATTR_MND]

    # 六维 + 护符(AMUL)加成后的有效值（2222-2227 行）
    t_str = pc.attr[ATTR_STR] + pc.amul[AMUL_STR] + pc.amul[AMUL_AAA]
    t_agi = pc.attr[ATTR_AGI] + pc.amul[AMUL_AGI] + pc.amul[AMUL_AAA]
    t_int = pc.attr[ATTR_INT] + pc.amul[AMUL_INT] + pc.amul[AMUL_AAA]
    t_vit = pc.attr[ATTR_VIT] + pc.amul[AMUL_VIT] + pc.amul[AMUL_AAA]
    t_spr = pc.attr[ATTR_SPR] + pc.amul[AMUL_SPR] + pc.amul[AMUL_AAA]
    t_mnd = pc.attr[ATTR_MND] + pc.amul[AMUL_MND] + pc.amul[AMUL_AAA]

    k = pc.kfLvl
    k100 = cdiv(k, 100)  # 争夺等级百位（成长系数）
    b.role = pc.role
    b.lvl = pc.lvl
    # 生命（2233 行）
    b.hpM = ((t_vit + t_mnd) * 35.0
             + int(t_vit * (34.0 if k >= 2000 else k100 * 1.7))
             + int(t_mnd * (34.0 if k >= 2000 else k100 * 1.7)))
    b.hpRecP = (2 if k >= 200 else 0) + (3 if k >= 500 else 0)
    b.hpRecA = 0.0
    b.hpRecRR = 0
    b.hpMR = cdiv(pc.wish[WISH_HPM], 100)
    b.cDef = 0
    b.sDef = 0
    # 物理/魔法攻击（2243-2247 行）
    b.pAtkB = t_str * (10.0 + (20.0 if k >= 2000 else k100 * 1.0))
    b.pAtkA = pc.wish[WISH_PATKA] * 5.0
    b.pAtkR = cdiv(pc.wish[WISH_PATKA], 100)
    b.mAtkB = t_int * (10.0 + (20.0 if k >= 2000 else k100 * 1.0))
    b.mAtkA = pc.wish[WISH_MATKA] * 5.0
    b.mAtkR = cdiv(pc.wish[WISH_MATKA], 100)
    b.aAtk = 0.0
    # 攻速（2250-2251 行）
    b.spdB = t_agi * 3.0
    b.spdA = int(t_agi * (0.5 if k >= 1000 else 0.0)) + pc.wish[WISH_SPDA]
    # 穿透（2253-2256 行）
    b.pBrcP = 10.0 if k >= 2000 else float(cdiv(k, 200))
    b.pBrcA = t_str * (1 if k >= 100 else 0) + pc.wish[WISH_PBRCA]
    b.mBrcP = 10.0 if k >= 2000 else float(cdiv(k, 200))
    b.mBrcA = t_int * (1 if k >= 100 else 0) + pc.wish[WISH_MBRCA]
    # 技能/暴击基数（2257-2259 行）
    b.sRateB = t_int + (int(t_str / 2.0) if k >= 50 else 0)
    b.cRateB = t_agi + (int(t_agi / 10.0) if k >= 1000 else 0)
    b.cBrcP = 0.0
    b.lchP = 0.0
    # 防御（2261-2264 行）：体魄→物防、意志→魔防，精神→双防各半
    b.pDefB = (t_vit + int(t_vit * (3.0 if k >= 2000 else k100 * 0.15))
               + t_spr * 0.5 + int(t_spr * (1.5 if k >= 1900 else k100 * 0.08)))
    b.pDefA = pc.wish[WISH_PDEFA] * 1.0
    b.mDefB = (t_mnd + int(t_mnd * (3.0 if k >= 2000 else k100 * 0.15))
               + t_spr * 0.5 + int(t_spr * (1.5 if k >= 1900 else k100 * 0.08)))
    b.mDefA = pc.wish[WISH_MDEFA] * 1.0
    b.pRdc = 0.0
    b.mRdc = 0.0
    # 护盾（2269 行）：精神×65 + 争夺成长
    b.sldM = t_spr * 65.0 + int(t_spr * (68.0 if k >= 2000 else k100 * 3.4))
    b.sldRecP = (2 if k >= 200 else 0) + (3 if k >= 500 else 0)
    b.sldRecA = 0.0
    b.sldRecRR = 0
    b.sldMR = cdiv(pc.wish[WISH_SLDM], 100)
    b.rflP = 0.0
    b.psvSkl = pc.auraSkl
    b.myst = 0
    # 舞：成长值→技能计数（锦上添花伤害基数），上限 106800
    b.sklC = (min(pc.growth, 106800) if b.role == ROLE_WU else 0)
    b.houC = 0
    b.qiAbsorb = 0
    b.wish = list(pc.wish)
    b.amul = list(pc.amul)

    # 装备词条加成（2305 行起 switch，下一块实现 _apply_gear）
    hp_plus, hp_add, p_atk_plus, m_atk_plus, spd_plus, sld_plus, sld_add = \
        _apply_gear(pc, b)
    # 许愿池固定加成（2306-2307 行）
    hp_add += pc.wish[WISH_HPM] * 12.0
    sld_add += pc.wish[WISH_SLDM] * 20.0

    b.hpM += hp_plus + hp_add
    b.pAtkB += p_atk_plus
    b.mAtkB += m_atk_plus
    b.spdB += spd_plus
    b.sRateP = cround(b.sRateB * 100.0 / (b.sRateB + 99.0) * 100.0) / 100.0
    b.sRateR = 100.0
    b.cRateP = cround(b.cRateB * 100.0 / (b.cRateB + 99.0) * 100.0) / 100.0
    b.sldM += sld_plus + sld_add
    b.hp = b.hpM
    b.spdRR = 0
    b.spdC = b.spdB + b.spdA
    b.sld = b.sldM
    b.growth = pc.growth
    b.alias = pc.alias
    return b


def _apply_gear(pc, b):
    """装备词条加成（2311-2509 行 switch，逐件装备单独公式）。

    返回 (hp_plus, hp_add, p_atk_plus, m_atk_plus, spd_plus, sld_plus, sld_add)。
    percent[i] = 词条评分百分数（如 150 = 150%评分）；b 在此处被写入附加属性。
    ⚠️ C++ 嵌套整数语义：
      - "int(lvl*10*(pct/10))/10" → 数值型词条 = int(lvl*pct)/10（等级×评分/10）
      - "round(基础*(int((lvl/5+X)*(pct/10))/1000)*100)/100" → 百分比词条 =
        基础 × int((lvl/5+X)*pct/10)/1000，保留两位小数（cround）
    """
    hp_plus = hp_add = 0.0
    p_atk_plus = m_atk_plus = spd_plus = 0.0
    sld_plus = sld_add = 0.0

    # 护符加成后的有效六维（COLORFUL/LIMPIDWAND/HUNT 等要引用）
    t_agi = pc.attr[ATTR_AGI] + pc.amul[AMUL_AGI] + pc.amul[AMUL_AAA]
    t_int = pc.attr[ATTR_INT] + pc.amul[AMUL_INT] + pc.amul[AMUL_AAA]
    t_str = pc.attr[ATTR_STR] + pc.amul[AMUL_STR] + pc.amul[AMUL_AAA]
    t_vit = pc.attr[ATTR_VIT] + pc.amul[AMUL_VIT] + pc.amul[AMUL_AAA]
    t_mnd = pc.attr[ATTR_MND] + pc.amul[AMUL_MND] + pc.amul[AMUL_AAA]

    def pct_term(lvl, lvl_div, plus, p):  # int((lvl/lvl_div + plus) * (p/10))
        return int((lvl / lvl_div + plus) * (p / 10.0))

    for g in pc.gear[:4]:
        if not isinstance(g, Gear) or g.type == 0:
            continue
        g_lvl = g.lvl
        p0, p1, p2, p3 = g.percent
        t = g.type

        if t == GEAR_NAME_TO_TYPE["SWORD"]:  # 探险者之剑：数值型词条
            b.pAtkA += int(g_lvl * 10.0 * (p0 / 10.0)) / 10.0
            b.mAtkA += int(g_lvl * 10.0 * (p1 / 10.0)) / 10.0
            b.pBrcA += int(g_lvl * (p2 / 10.0)) / 10.0
            b.lchP += int((g_lvl / 20.0 + 10.0) * (p3 / 10.0)) / 10.0
        elif t == GEAR_NAME_TO_TYPE["BOW"]:  # 探险者短弓
            b.pAtkA += int(g_lvl * 10.0 * (p0 / 10.0)) / 10.0
            b.mAtkA += int(g_lvl * 10.0 * (p1 / 10.0)) / 10.0
            b.spdA += int(g_lvl * 2.0 * (p2 / 10.0)) / 10.0
            b.lchP += int((g_lvl / 20.0 + 10.0) * (p3 / 10.0)) / 10.0
        elif t == GEAR_NAME_TO_TYPE["STAFF"]:  # 探险者短杖
            b.pAtkA += int(g_lvl * 10.0 * (p0 / 10.0)) / 10.0
            b.mAtkA += int(g_lvl * 10.0 * (p1 / 10.0)) / 10.0
            b.mBrcP += int((g_lvl / 20.0 + 5.0) * (p2 / 10.0)) / 10.0
            b.lchP += int((g_lvl / 20.0 + 10.0) * (p3 / 10.0)) / 10.0
        elif t == GEAR_NAME_TO_TYPE["BLADE"]:  # 狂信者的荣誉之刃：百分比词条
            p_atk_plus += cround(b.pAtkB * (pct_term(g_lvl, 5.0, 20.0, p0) / 1000.0) * 100.0) / 100.0
            spd_plus += cround(b.spdB * (pct_term(g_lvl, 5.0, 20.0, p1) / 1000.0) * 100.0) / 100.0
            b.cBrcP += int((g_lvl / 20.0 + 10.0) * (p2 / 10.0)) / 10.0
            b.pBrcP += int((g_lvl / 20.0 + 10.0) * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_BLADE
        elif t == GEAR_NAME_TO_TYPE["ASSBOW"]:  # 反叛者的刺杀弓
            p_atk_plus += cround(b.pAtkB * (pct_term(g_lvl, 5.0, 30.0, p0) / 1000.0) * 100.0) / 100.0
            b.cBrcP += int((g_lvl / 20.0 + 10.0) * (p1 / 10.0)) / 10.0
            b.pBrcP += int((g_lvl / 20.0 + 10.0) * (p2 / 10.0)) / 10.0
            b.pBrcA += int(g_lvl * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_ASSBOW
        elif t == GEAR_NAME_TO_TYPE["DAGGER"]:  # 幽梦匕首
            p_atk_plus += cround(b.pAtkB * (pct_term(g_lvl, 5.0, 0.0, p0) / 1000.0) * 100.0) / 100.0
            m_atk_plus += cround(b.mAtkB * (pct_term(g_lvl, 5.0, 0.0, p1) / 1000.0) * 100.0) / 100.0
            b.spdA += int(g_lvl * 4.0 * (p2 / 10.0)) / 10.0
            spd_plus += cround(b.spdB * (pct_term(g_lvl, 5.0, 25.0, p3) / 1000.0) * 100.0) / 100.0
            if g.isMyst:
                b.myst |= MYST_DAGGER
        elif t == GEAR_NAME_TO_TYPE["WAND"]:  # 光辉法杖：三条魔攻百分比
            m_atk_plus += cround(b.mAtkB * (pct_term(g_lvl, 5.0, 0.0, p0) / 1000.0) * 100.0) / 100.0
            m_atk_plus += cround(b.mAtkB * (pct_term(g_lvl, 5.0, 0.0, p1) / 1000.0) * 100.0) / 100.0
            m_atk_plus += cround(b.mAtkB * (pct_term(g_lvl, 5.0, 0.0, p2) / 1000.0) * 100.0) / 100.0
            b.mBrcP += int(g_lvl / 20.0 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_WAND
        elif t == GEAR_NAME_TO_TYPE["SHIELD"]:  # 荆棘盾剑
            b.lchP += int((g_lvl / 20.0 + 10.0) * (p0 / 10.0)) / 10.0
            b.rflP += int(g_lvl / 15.0 * (p1 / 10.0)) / 10.0
            b.pDefA += int(g_lvl * (p2 / 10.0)) / 10.0
            b.mDefA += int(g_lvl * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_SHIELD
        elif t == GEAR_NAME_TO_TYPE["CLAYMORE"]:  # 陨铁重剑：两条数值物攻
            b.pAtkA += int(g_lvl * 20.0 * (p0 / 10.0)) / 10.0
            b.pAtkA += int(g_lvl * 20.0 * (p1 / 10.0)) / 10.0
            p_atk_plus += cround(b.pAtkB * (pct_term(g_lvl, 5.0, 30, p2) / 1000.0) * 100.0) / 100.0
            b.cBrcP += int((g_lvl / 20.0 + 1.0) * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_CLAYMORE
        elif t == GEAR_NAME_TO_TYPE["SPEAR"]:  # 饮血魔剑
            p_atk_plus += cround(b.pAtkB * (pct_term(g_lvl, 5.0, 50.0, p0) / 1000.0) * 100.0) / 100.0
            b.pBrcP += int((g_lvl / 20.0 + 10.0) * (p1 / 10.0)) / 10.0
            b.mBrcA += int(g_lvl * 2.0 * (p2 / 10.0)) / 10.0
            b.lchP += int((g_lvl / 20.0 + 10.0) * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_SPEAR
        elif t == GEAR_NAME_TO_TYPE["COLORFUL"]:  # 彩金长剑：物/魔/速% + 敏转绝伤
            p_atk_plus += cround(b.pAtkB * (pct_term(g_lvl, 5.0, 10.0, p0) / 1000.0) * 100.0) / 100.0
            m_atk_plus += cround(b.mAtkB * (pct_term(g_lvl, 5.0, 10.0, p1) / 1000.0) * 100.0) / 100.0
            spd_plus += cround(b.spdB * (pct_term(g_lvl, 5.0, 20.0, p2) / 1000.0) * 100.0) / 100.0
            b.aAtk += t_agi * int(g_lvl * 0.04 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_COLORFUL
        elif t == GEAR_NAME_TO_TYPE["LIMPIDWAND"]:  # 清澄长杖：智转攻速
            m_atk_plus += cround(b.mAtkB * (pct_term(g_lvl, 5.0, 20.0, p0) / 1000.0) * 100.0) / 100.0
            b.mBrcP += int((g_lvl / 20.0 + 5.0) * (p1 / 10.0)) / 10.0
            spd_plus += cround(b.spdB * (pct_term(g_lvl, 5.0, 0.0, p2) / 1000.0) * 100.0) / 100.0
            b.spdA += t_int * int(g_lvl / 375.0 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_LIMPIDWAND

        elif t == GEAR_NAME_TO_TYPE["GLOVES"]:  # 探险者手环：数值型
            b.pAtkA += int(g_lvl * 10.0 * (p0 / 10.0)) / 10.0
            b.mAtkA += int(g_lvl * 10.0 * (p1 / 10.0)) / 10.0
            b.spdA += int(g_lvl * 2.0 * (p2 / 10.0)) / 10.0
            hp_add += int(g_lvl * 10.0 * (p3 / 10.0)) / 10.0
        elif t == GEAR_NAME_TO_TYPE["BRACELET"]:  # 命师的传承手环
            m_atk_plus += cround(b.mAtkB * (pct_term(g_lvl, 5.0, 1.0, p0) / 1000.0) * 100.0) / 100.0
            b.mBrcP += int((g_lvl / 20.0 + 1.0) * (p1 / 10.0)) / 10.0
            sld_add += int(g_lvl * 20.0 * (p2 / 10.0)) / 10.0
            b.mDefA += int(g_lvl * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_BRACELET
        elif t == GEAR_NAME_TO_TYPE["VULTURE"]:  # 秃鹫手环：三条吸血
            b.lchP += int((g_lvl / 20.0 + 1.0) * (p0 / 10.0)) / 10.0
            b.lchP += int((g_lvl / 20.0 + 1.0) * (p1 / 10.0)) / 10.0
            b.lchP += int((g_lvl / 20.0 + 1.0) * (p2 / 10.0)) / 10.0
            b.spdA += int(g_lvl * 2.0 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_VULTURE
        elif t == GEAR_NAME_TO_TYPE["RING"]:  # 海星戒指：穿透+暴击/技能基数
            b.pBrcA += int(g_lvl * 0.5 * (p0 / 10.0)) / 10.0
            b.mBrcA += int(g_lvl * 0.5 * (p1 / 10.0)) / 10.0
            b.cRateB += int(g_lvl * 0.8 * (p2 / 10.0)) / 10.0
            b.sRateB += int(g_lvl * 0.8 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_RING
        elif t == GEAR_NAME_TO_TYPE["DEVOUR"]:  # 噬魔戒指：力量转血
            b.mBrcA += int(g_lvl * 0.5 * (p0 / 10.0)) / 10.0
            b.sRateB += int(g_lvl * 0.8 * (p1 / 10.0)) / 10.0
            hp_add += t_str * (int(g_lvl * 0.08 * (p2 / 10.0)) / 10.0)
            hp_plus += cround(b.hpM * (int(g_lvl * 0.07 * (p3 / 10.0)) / 1000.0) * 100.0) / 100.0
            if g.isMyst:
                b.myst |= MYST_DEVOUR
        elif t == GEAR_NAME_TO_TYPE["REFRACT"]:  # 折光戒指：敏捷转血
            b.spdA += int(g_lvl * 2.0 * (p0 / 10.0)) / 10.0
            b.cRateB += int(g_lvl * 0.8 * (p1 / 10.0)) / 10.0
            b.cBrcP += int(g_lvl / 20.0 * (p2 / 10.0)) / 10.0
            hp_add += t_agi * (int(g_lvl * 0.05 * (p3 / 10.0)) / 10.0)
            if g.isMyst:
                b.myst |= MYST_REFRACT
        elif t == GEAR_NAME_TO_TYPE["PLATE"]:  # 探险者铁甲
            hp_add += int(g_lvl * 20.0 * (p0 / 10.0)) / 10.0
            b.pDefA += int(g_lvl * (p1 / 10.0)) / 10.0
            b.mDefA += int(g_lvl * (p2 / 10.0)) / 10.0
            b.hpRecA += int(g_lvl * 10.0 * (p3 / 10.0)) / 10.0
        elif t in (GEAR_NAME_TO_TYPE["LEATHER"], GEAR_NAME_TO_TYPE["CLOTH"]):
            # 皮甲/布甲同公式（2462 行 case 合并）
            hp_add += int(g_lvl * 25.0 * (p0 / 10.0)) / 10.0
            b.pRdc += int(g_lvl * 2.0 * (p1 / 10.0)) / 10.0
            b.mRdc += int(g_lvl * 2.0 * (p2 / 10.0)) / 10.0
            b.hpRecA += int(g_lvl * 6.0 * (p3 / 10.0)) / 10.0
        elif t == GEAR_NAME_TO_TYPE["CLOAK"]:  # 旅法师的灵光袍
            hp_add += int(g_lvl * 10.0 * (p0 / 10.0)) / 10.0
            b.sldRecA += int(g_lvl * 60.0 * (p1 / 10.0)) / 10.0
            sld_plus += cround(b.sldM * (pct_term(g_lvl, 5.0, 25.0, p2) / 1000.0) * 100.0) / 100.0
            sld_add += int(g_lvl * 50.0 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_CLOAK
        elif t == GEAR_NAME_TO_TYPE["THORN"]:  # 荆棘重甲
            hp_plus += cround(b.hpM * (pct_term(g_lvl, 5.0, 20.0, p0) / 1000.0) * 100.0) / 100.0
            b.pDefA += int(g_lvl * (p1 / 10.0)) / 10.0
            b.mDefA += int(g_lvl * (p2 / 10.0)) / 10.0
            b.rflP += int((g_lvl / 15.0 + 10.0) * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_THORN
        elif t == GEAR_NAME_TO_TYPE["WOOD"]:  # 复苏战衣
            hp_plus += cround(b.hpM * (pct_term(g_lvl, 5.0, 50.0, p0) / 1000.0) * 100.0) / 100.0
            b.pRdc += int(g_lvl * 5.0 * (p1 / 10.0)) / 10.0
            b.mRdc += int(g_lvl * 5.0 * (p2 / 10.0)) / 10.0
            b.hpRecA += int(g_lvl * 20.0 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_WOOD
        elif t == GEAR_NAME_TO_TYPE["CAPE"]:  # 挑战斗篷
            sld_plus += cround(b.sldM * (pct_term(g_lvl, 5.0, 50, p0) / 1000.0) * 100.0) / 100.0
            sld_add += int(g_lvl * 100.0 * (p1 / 10.0)) / 10.0
            b.mDefA += int(g_lvl * (p2 / 10.0)) / 10.0
            b.mRdc += int(g_lvl * 5.0 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_CAPE
        elif t == GEAR_NAME_TO_TYPE["SCARF"]:  # 探险者耳环
            hp_add += int(g_lvl * 10.0 * (p0 / 10.0)) / 10.0
            b.pRdc += int(g_lvl * 2.0 * (p1 / 10.0)) / 10.0
            b.mRdc += int(g_lvl * 2.0 * (p2 / 10.0)) / 10.0
            b.hpRecA += int(g_lvl * 4.0 * (p3 / 10.0)) / 10.0
        elif t == GEAR_NAME_TO_TYPE["TIARA"]:  # 占星师的耳饰
            hp_add += int(g_lvl * 5.0 * (p0 / 10.0)) / 10.0
            sld_plus += cround(b.sldM * (pct_term(g_lvl, 5.0, 0.0, p1) / 1000.0) * 100.0) / 100.0
            sld_add += int(g_lvl * 20.0 * (p2 / 10.0)) / 10.0
            b.pRdc += int(g_lvl * 2.0 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_TIARA
        elif t == GEAR_NAME_TO_TYPE["RIBBON"]:  # 萌爪耳钉：体/意转减伤转血
            b.pRdc += b.tVit * (int(g_lvl * 0.0085 * (p0 / 10.0)) / 10.0)
            b.mRdc += b.tMnd * (int(g_lvl * 0.0085 * (p1 / 10.0)) / 10.0)
            hp_add += b.tVit * (int(g_lvl / 30.0 * (p2 / 10.0)) / 10.0)
            hp_add += b.tMnd * (int(g_lvl / 30.0 * (p3 / 10.0)) / 10.0)
            if g.isMyst:
                b.myst |= MYST_RIBBON
        elif t == GEAR_NAME_TO_TYPE["HUNT"]:  # 猎魔耳环：技能基数+力/敏转血
            b.sRateB += int(g_lvl * 0.4 * (p0 / 10.0)) / 10.0
            hp_add += t_str * (int(g_lvl * 0.08 * (p1 / 10.0)) / 10.0)
            hp_add += t_agi * (int(g_lvl * 0.08 * (p2 / 10.0)) / 10.0)
            hp_plus += cround(b.hpM * (int(g_lvl * 0.06 * (p3 / 10.0)) / 1000.0) * 100.0) / 100.0
            if g.isMyst:
                b.myst |= MYST_HUNT
        elif t == GEAR_NAME_TO_TYPE["FIERCE"]:  # 凶神耳环：力/敏转防
            b.pBrcA += int(g_lvl * 0.5 * (p0 / 10.0)) / 10.0
            b.pDefA += int(t_str / 10.0) * (int(g_lvl / 250.0 * (p1 / 10.0)) / 10.0)
            b.mDefA += int(t_agi / 10.0) * (int(g_lvl / 250.0 * (p2 / 10.0)) / 10.0)
            b.sRateB += int(g_lvl * 0.4 * (p3 / 10.0)) / 10.0
            if g.isMyst:
                b.myst |= MYST_FIERCE

    return hp_plus, hp_add, p_atk_plus, m_atk_plus, spd_plus, sld_plus, sld_add


# ═══════════════════════ 真实配置读取（游戏接口 → Player）═══════════════════════

# 光环天赋 checkbox ID → AURA 位（2026-09-09 f=5 实测映射，见 02-接口.md §3.2d）
TF_ID_TO_AURA = {
    101: AURA_SHI, 102: AURA_XIN, 1101: AURA_ZOU, 1102: AURA_PIAO, 1103: AURA_PEN,
    201: AURA_BI, 202: AURA_MO, 203: AURA_DUN, 204: AURA_XUE, 205: AURA_XIAO,
    206: AURA_SHENG, 207: AURA_E,
    301: AURA_SHANG, 302: AURA_SHEN, 303: AURA_CI, 304: AURA_REN, 305: AURA_RE,
    306: AURA_DIAN, 307: AURA_WU, 308: AURA_ZHI, 309: AURA_SHAN,
    401: AURA_FEI, 402: AURA_BO, 403: AURA_JU, 404: AURA_HONG, 405: AURA_JUE,
    406: AURA_HOU, 407: AURA_DUNH, 408: AURA_ZI,
}

# f=6 装备 icon 码（z2101-2405）→ (GEAR 类型名, 槽位 0武器/1手环/2防具/3耳环)
ICON_TO_GEAR = {
    2101: ("SWORD", 0), 2102: ("BOW", 0), 2103: ("STAFF", 0), 2104: ("BLADE", 0),
    2105: ("ASSBOW", 0), 2106: ("DAGGER", 0), 2107: ("WAND", 0), 2108: ("SHIELD", 0),
    2109: ("CLAYMORE", 0), 2110: ("SPEAR", 0), 2111: ("COLORFUL", 0),
    2112: ("LIMPIDWAND", 0),
    2201: ("GLOVES", 1), 2202: ("BRACELET", 1), 2203: ("VULTURE", 1),
    2204: ("RING", 1), 2205: ("DEVOUR", 1), 2206: ("REFRACT", 1),
    2301: ("PLATE", 2), 2302: ("LEATHER", 2), 2303: ("CLOTH", 2), 2304: ("CLOAK", 2),
    2305: ("THORN", 2), 2306: ("WOOD", 2), 2307: ("CAPE", 2),
    2401: ("SCARF", 3), 2402: ("TIARA", 3), 2403: ("RIBBON", 3),
    2404: ("HUNT", 3), 2405: ("FIERCE", 3),
}

# 角色中文名 → ROLE（PC_CN 反向）
CN_TO_ROLE = {cn: NAME_TO_ROLE[py] for py, cn in PC_CN.items()}


def _parse_my_gear(html_text):
    """f=6 HTML → [4]Gear（按槽位 0武器/1手环/2防具/3耳环，缺槽补空）。

    每按钮: icon z2101-2405 → 装备类型；Lv.<span>N</span> → 装备等级；
    词条评分 pull-right bg-*>N% → percent[4]（int 截断对齐 C++ fscanf %d）；
    [神秘属性] → isMyst。不复用 ggz_daily.parse_equips（其 f=6 等级解析失效）。
    """
    import html as _html
    import re as _re
    by_slot = {}
    for btn_raw in _re.findall(r"<button[^>]*>.*?</button>", html_text, _re.S):
        if "ys/icon/z" not in btn_raw:
            continue
        btn = _html.unescape(btn_raw)
        m = _re.search(r"ys/icon/z(?:/z)?(\d{4})", btn)
        if not m:
            continue
        info = ICON_TO_GEAR.get(int(m.group(1)))
        if not info:
            continue
        gname, slot = info
        mlv = _re.search(r"Lv\.<span[^>]*>(\d+)</span>", btn)
        lvl = int(mlv.group(1)) if mlv else 0
        percents = [int(float(x)) for x in _re.findall(
            r"bg-\w+[^>]*>(?:&nbsp;|\s)*(\d+(?:\.\d+)?)%", btn)]
        by_slot[slot] = Gear(GEAR_NAME_TO_TYPE[gname], lvl,
                             (percents + [0] * 4)[:4], "神秘属性" in btn)
    return [by_slot.get(i, Gear()) for i in range(4)]


def _parse_my_auras(html_text):
    """f=5 HTML → 佩戴天赋位或集（halotfmr() 函数体内勾选的 value 列表）。

    ⚠️ 实际文本是 $("input[name=tfcheckbox][value=101]") —— 值后是 "]（jQuery
    属性选择器），不是引号。
    """
    import re as _re
    m = _re.search(r"function halotfmr\(\)\{(.*?)\}", html_text, _re.S)
    if not m:
        return 0
    bits = 0
    for x in _re.findall(r"value=(\d+)\]", m.group(1)):
        bits |= TF_ID_TO_AURA.get(int(x), 0)
    return bits


def load_player(zid=None, verbose=True):
    """从游戏接口读任意角色卡配置 → Player（供模拟/出击预判）。

    ⚠️ 依赖 ggz_daily（requests + cookie.txt）→ 延迟 import，本模块仍可离线用。
    数据源（2026-09-09 逐项实测，2026-09-10 补免切卡直读，见 02-接口.md）：
      f=18&zid= → 卡片等级/六维/成长值（**不切卡可直读任意卡**，页首即目标卡名）；
      f=23 → 总争夺等级(=基础+幻影，全局)；f=5 → 佩戴天赋（全局共享）；
      f=6 → 身上装备（**账号级共享**：切卡实测同一套 4 件不变，任意卡同装）。
    许愿池/护身符无便捷数据源，按 0 假设（对拍验证影响极小）。
    """
    import re as _re
    import ggz_daily as g

    # 2026-09-10 重构后：USER/SAFEID/ZID 宿主 = ggzlib.state，写状态必须走
    # ensure_session（直写 g.USER 只会覆盖 ggz_daily 的转发影子属性，
    # ggzlib.http.request 读 state.COOKIE 拿不到）
    g.ensure_session()
    if zid is None:
        zid = g.ZID
    # 2026-09-10：f=18&zid= 直读任意卡 + 装备栏账号级共享（切卡实测同一套 4 件不变）
    # → 免切卡精确模拟任意卡；旧版此处对 zid≠出战 raise，已移除

    # f=18: 卡片详情（等级/六维/成长值）
    t18 = g.dec(g.request(g.BASE + "/fyg_read.php", {"f": 18, "zid": zid}))
    p18 = g.strip_tags(t18)
    m = _re.search(r"Lv\.(\d+)\s*角色等级", p18)
    lvl = int(m.group(1)) if m else 0
    growth = 0
    m = _re.search(r"(\d+)\s*角色技能成长点数", p18)
    if m:
        growth = int(m.group(1))
    attr = [0] * 6
    for i, key in enumerate(["sjll", "sjmj", "sjzl", "sjtp", "sjjs", "sjyz"]):
        m = _re.search(r'id="%s" value="(\d+)"' % key, t18)
        if m:
            attr[i] = int(m.group(1))
    # 角色名（f=18 去标签开头即卡名，或 f=8 反查）
    cards = g.list_cards()
    role_name = next((n for n, z in cards.items() if z == zid), None)
    role = CN_TO_ROLE.get(role_name)
    if role is None:
        raise ValueError(f"无法确定角色（zid={zid} name={role_name}）")

    # f=23: 总争夺等级
    t23 = g.dec(g.request(g.BASE + "/fyg_read.php", {"f": 23}))
    m = _re.search(r"(\d+)\s*总争夺等级", g.strip_tags(t23))
    kf_lvl = int(m.group(1)) if m else 0

    # f=6: 身上装备 / f=5: 佩戴天赋
    t6 = g.dec(g.request(g.BASE + "/fyg_read.php", {"f": 6}))
    gear = _parse_my_gear(t6)
    t5 = g.dec(g.request(g.BASE + "/fyg_read.php", {"f": 5}))
    aura = _parse_my_auras(t5)

    pc = Player(role=role, lvl=lvl, kfLvl=kf_lvl, attr=attr, gear=gear,
                auraSkl=aura, growth=growth)
    if verbose:
        print(f"[读取] {role_name} Lv.{lvl} 争夺{kf_lvl} 成长{growth} "
              f"六维{attr}")
        names = [GEAR_CN.get(GEAR_NAME[g_.type], GEAR_NAME[g_.type])
                 for g_ in gear if g_.type]
        print(f"[装备] {' / '.join(names) if names else '（无）'}")
        aura_names = BStat(psvSkl=aura).aura_names()
        print(f"[天赋] {'|'.join(aura_names) if aura_names else '（无）'}")
    return pc


def predict_vs_monster(monster_lvl, n=300, npc_role=None, pc=None):
    """当前出战配置 vs 野怪的快速胜率预测（pk() 出击决策用）。

    返回统计 dict（win_rate 等）；数据读取/模拟失败抛异常由调用方兜底。
    npc_role 缺省自动识别：战报名含"史莱姆"→SHI（当前版本打野野怪）。
    """
    if npc_role is None:
        npc_role = ROLE_SHI
    if pc is None:
        pc = load_player(verbose=False)
    return run_matches(pc, npc_role, monster_lvl, n=n)


# ═══════════════════════ 批量模拟接口（供 ggz_daily.py / CLI 调用）═══════════════════════

def run_matches(pc, npc_role, npc_lvl, n=1000, seed=20260909):
    """我方配置 vs 野怪跑 n 局，返回统计 dict。

    每局用不同随机种子（MINSTD 序列可复现）；野怪每局重建（calcBattle 会
    原地修改状态）。返回 {win, lose, draw, win_rate, avg_rate}。
    """
    win = lose = draw = 0
    rates = []
    for i in range(n):
        me = prepare_pc_bstat(pc)          # 每局从 Player 重建（防原地修改污染）
        foe = prepare_npc_bstat(npc_role, npc_lvl)
        r = calc_battle(me, foe, Minstd(seed + i))
        if r.winner == 0:
            win += 1
            rates.append(r.rate)
        elif r.winner == 1:
            lose += 1
        else:
            draw += 1
    return {"win": win, "lose": lose, "draw": draw,
            "win_rate": win / n * 100.0,
            "avg_rate": (sum(rates) / len(rates)) if rates else 0.0,
            "n": n}


# ═══════════════════════ CLI ═══════════════════════

def _demo_pc():
    """内置示例：舞 800 级 2000 争夺（2026-09 实测属性量级），打野作业配装。"""
    G = GEAR_NAME_TO_TYPE
    gear = [
        Gear(G["CLAYMORE"], 100, (150, 150, 120, 100), True),   # 陨铁重剑(神秘)
        Gear(G["GLOVES"], 100, (150, 150, 150, 150)),           # 探险者手环
        Gear(G["WOOD"], 100, (120, 130, 130, 140), True),       # 复苏战衣(神秘)
        Gear(G["HUNT"], 100, (140, 150, 150, 130), True),       # 猎魔耳环(神秘)
    ]
    # 天赋: 削骨之痛+伤口恶化+精神创伤（打野破盾三件套，见 04 §4.1 实测结论）
    aura = AURA_XIAO | AURA_SHANG | AURA_SHEN
    return Player(role=ROLE_WU, lvl=800, kfLvl=2000,
                  attr=[1560, 348, 1, 347, 1, 347], gear=gear, auraSkl=aura,
                  growth=100000)


def _parse_npc(spec):
    """'SHI:88' → (ROLE_SHI, 88)。"""
    name, _, lvl = spec.partition(":")
    key = name.strip().upper()
    if key not in NPC_TO_ROLE:
        raise SystemExit(f"未知野怪 '{name}'，可用: {', '.join(NPC_NAME)}")
    return NPC_TO_ROLE[key], int(lvl or 88)


def _pc_from_json(path):
    """从 JSON 读 Player 配置（字段与 Player 参数同名，gear 用名称字符串）。"""
    d = json.load(open(path, encoding="utf-8"))
    gear = []
    for g in d.get("gear", []):
        gear.append(Gear(GEAR_NAME_TO_TYPE[g["type"].upper()], g["lvl"],
                         g.get("percent", (0, 0, 0, 0)), g.get("myst", False)))
    aura = 0
    for name in d.get("auras", []):
        aura |= AURA_NAME_TO_BIT[name.upper()]
    return Player(role=NAME_TO_ROLE[d["role"].upper()], lvl=d["lvl"],
                  kfLvl=d["kfLvl"], attr=d["attr"], gear=gear, auraSkl=aura,
                  growth=d.get("growth", 0))


def main():
    ap = argparse.ArgumentParser(description="咕咕镇战斗模拟器（newkf.cpp 移植）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="内置示例: 舞打野 vs 史莱姆")

    p = sub.add_parser("npc", help="查看野怪模板属性")
    p.add_argument("--role", default="SHI")
    p.add_argument("--lvl", type=int, default=88)

    p = sub.add_parser("fight", help="批量模拟我方 vs 野怪")
    p.add_argument("--pc", help="我方配置 JSON；缺省用内置 demo 舞")
    p.add_argument("--npc", default="SHI:88", help="野怪 规格 '名:等级'")
    p.add_argument("--n", type=int, default=1000, help="模拟局数")
    p.add_argument("--show", action="store_true", help="显示我方面板")

    p = sub.add_parser("whoami", help="读真实配置（网络）: 面板 + 快速胜率预测")
    p.add_argument("--lvl", type=int, default=0, help="野怪等级（0=只显示面板不模拟）")
    p.add_argument("--n", type=int, default=300, help="模拟局数")

    args = ap.parse_args()
    if args.cmd == "demo":
        pc = _demo_pc()
        me = prepare_pc_bstat(pc)
        foe = prepare_npc_bstat(ROLE_SHI, 88)
        print("我方:", me.brief())
        print("野怪:", foe.brief())
        st = run_matches(pc, ROLE_SHI, 88, n=1000)
        print(f"\n1000 局: 胜 {st['win']} / 负 {st['lose']} / 平 {st['draw']}"
              f" → 胜率 {st['win_rate']:.1f}%"
              f"（胜局平均剩余 {st['avg_rate'] * 100:.0f}%）")
    elif args.cmd == "npc":
        foe = prepare_npc_bstat(NPC_TO_ROLE[args.role.upper()], args.lvl)
        print(foe.brief())
        print(f"  物攻{foe.pAtkB:.0f} 魔攻{foe.mAtkB:.0f} 绝伤{foe.aAtk:.0f}"
              f" 攻速{foe.spdB:.0f} 物防{foe.pDefB:.0f} 魔防{foe.mDefB:.0f}"
              f" 物减伤{foe.pRdc:.0f} 魔减伤{foe.mRdc:.0f}"
              f" 回血%{foe.hpRecP} 回盾%{foe.sldRecP} 反伤%{foe.rflP:.0f}")
    elif args.cmd == "whoami":
        pc = load_player(verbose=True)
        me = prepare_pc_bstat(pc)
        print("面板:", me.brief())
        print(f"  物攻{me.pAtkB:.0f}+{me.pAtkA:.0f} 魔攻{me.mAtkB:.0f}+{me.mAtkA:.0f}"
              f" 绝伤{me.aAtk:.0f} 攻速{me.spdB + me.spdA:.0f}"
              f" 技能率{me.sRateP:.1f}% 暴击率{me.cRateP:.1f}%"
              f" 物穿{me.pBrcP:.1f}%+{me.pBrcA:.0f} 吸血{me.lchP:.1f}%")
        if args.lvl:
            st = run_matches(pc, ROLE_SHI, args.lvl, n=args.n)
            print(f"\n{args.n} 局 vs SHI:{args.lvl}: 胜 {st['win']} / 负 {st['lose']}"
                  f" / 平 {st['draw']} → 胜率 {st['win_rate']:.1f}%")
    elif args.cmd == "fight":
        pc = _pc_from_json(args.pc) if args.pc else _demo_pc()
        role, lvl = _parse_npc(args.npc)
        if args.show:
            print("我方:", prepare_pc_bstat(pc).brief())
            print("野怪:", prepare_npc_bstat(role, lvl).brief())
        st = run_matches(pc, role, lvl, n=args.n)
        print(f"{args.n} 局 vs {args.npc}: 胜 {st['win']} / 负 {st['lose']}"
              f" / 平 {st['draw']} → 胜率 {st['win_rate']:.1f}%"
              f"（胜局平均剩余 {st['avg_rate'] * 100:.0f}%）")


if __name__ == "__main__":
    main()
