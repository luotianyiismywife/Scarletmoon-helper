# -*- coding: utf-8 -*-
"""咕咕镇日常脚本（CLI 入口薄壳，功能实现已拆分至 ggzlib 包，2026-09-10）。

用法:
    python tools/ggz/ggz_daily.py stat          # 汇总状态（战场/工坊/翻牌）
    python tools/ggz/ggz_daily.py addpoint      # [0.5] 加点（自动分配剩余点）
    python tools/ggz/ggz_daily.py gem           # [1] 工坊收菜+开工（加工中→收工→自动重开）
    python tools/ggz/ggz_daily.py gemup         # [1.5] 提升宝石（B以下只升梦>红>银；比例低优先）
    python tools/ggz/ggz_daily.py halo          # [1.5b] 提升光环（读光环天赋石持有量→c=29）
    python tools/ggz/ggz_daily.py wish          # [3] 许愿池（按 WISH_MODE：combo 300w 十连送1=11次 / single 30w×N）
    python tools/ggz/ggz_daily.py shop [--full]  # [2] 商店（默认只买日限10W贝壳；--full 批量清仓+买1瓶药水）
    python tools/ggz/ggz_daily.py beach         # [4] 沙滩收取+清理（4.5 规则；空且有箱→自动刷新；--no-refresh 禁用自动刷新不耗箱）
    python tools/ggz/ggz_daily.py refresh       # [4.5] 强制刷新沙滩（耗随机装备箱）
    python tools/ggz/ggz_daily.py smelt         # [4.5c] 熔炼仓库可熔炼装备为护身符（手动）
    python tools/ggz/ggz_daily.py warehouse_tidy [--dry-run] [--green-only] [--clear-beach]  # 仓库整理
    python tools/ggz/ggz_daily.py pk [n]        # [5] 出击打野（默认 3 狗牌停；[--full] 打满 n 次；换卡自动连带换装+重排加点）
    python tools/ggz/ggz_daily.py gift [--bonus1|--bonus2]  # [6] 翻牌（透视自动检测；--bonus1 耗1药水再领 / --bonus2 耗2药水重置再翻）
    python tools/ggz/ggz_daily.py bonus         # [7] 额外奖励（耗 1 体能刺激药水；手动）
    python tools/ggz/ggz_daily.py gearfit [--apply]  # [8] 配装推荐×持有查询（--apply 一键换装到当前角色推荐套）
    python tools/ggz/ggz_daily.py all [--bonus1|--bonus2]  # 一键日常（按序执行；--bonus 显式开启翻牌后药水操作）
    python tools/ggz/ggz_daily.py switch <名|zid>  # 切换出战角色

日志: 每次执行同时输出到终端 + logs/ggz_YYYYMMDD.log（完整留档，
      终端输出被吞/截断时以日志文件为准）。

依赖: cookie.txt（tools/get_cookies.py --login 或提取生成）

═══ 架构说明（2026-09-10 拆分）═══
功能实现全部在 tools/ggz/ggzlib/ 包（state/http/cards/workshop/rules/beach/
warehouse/gearfit/battle/gift/daily），本文件只保留 CLI 派发。
⚠️ 会话状态（USER/SAFEID/ZID/COOKIE）唯一宿主 = ggzlib.state；本模块通过
   模块级 __getattr__ 动态转发（外部脚本 `import ggz_daily as g` 后 g.USER
   读到的永远是实时值）。**写状态必须走 g.ensure_session() 或 ggzlib.state**，
   直接 `g.USER = x` 只会覆盖本模块转发出来的影子属性、ggzlib 内部读不到。
配置常量（WISH_MODE/SHOP_MODE/BEACH_RULES/GEAR_FIT/ADDPOINT_STRATEGY/PK_SIM_*
等）随实现模块走（ggzlib.workshop / ggzlib.rules / ggzlib.gearfit /
ggzlib.cards / ggzlib.battle），改配置改对应源文件；下方 re-export 仅为
兼容读取（改 facade 上的值不影响实现模块内部引用）。
"""
import sys

# ⚠️ 必须**先** reconfigure 再 import ggzlib：ggzlib.http 的 Tee 会替换
# sys.stdout，之后再 reconfigure 会在 Tee 对象上炸 AttributeError（battle_sim
# 顶部注释同款教训，2026-09-09 实战踩中）
sys.stdout.reconfigure(encoding="utf-8")

# ── 功能 re-export（函数/类：对象绑定不可变，安全）──
from ggzlib.state import AuthExpiredError, refresh_cookie_auto, load_cookie
from ggzlib.http import (
    setup_logging, request, dec, read_block, click, strip_tags, show,
    get_user_and_safeid, get_active_zid, ensure_session,
    mask_secret, add_secret, SECRETS, Tee,
)
from ggzlib.http import PROJECT_ROOT, USER_HOME  # noqa: F401 （旧兼容常量）
from ggzlib.cards import (
    list_cards, switch_card, addpoint, CARD_ZIDS, ADDPOINT_STRATEGY,
)
from ggzlib.workshop import (
    gem, gemup, halo, wish, shop, shop_click, get_items,
    parse_gem_panel, gem_eta_text, show_gem_panel,
    WISH_MODE, SHOP_MODE, GEM_TARGET_PCT, GEM_PANEL_COLS,
)
from ggzlib.rules import (
    parse_equips, equip_decision, BEACH_RULES, BEACH_SAME_NAME_BEST,
    RULE_FIELDS, DERIVED_FIELDS,
)
from ggzlib.beach import beach, beach_refresh, _read_beach, get_beach_countdown
from ggzlib.warehouse import smelt, warehouse_tidy, get_store_space
from ggzlib.gearfit import gearfit, equip_loadout, GEAR_FIT
from ggzlib.battle import (
    pk, fight, parse_pk, parse_monster, parse_monster_level, bonus,
    sim_current_winrate,
    PK_SIM_SWITCH_RATE, PK_SIM_ROUNDS, PK_SIM_QUICK_ROUNDS,
)
from ggzlib.gift import gift, _gift_flip
from ggzlib.daily import all_daily, stat


# ── 会话状态动态转发（读）──
# g.USER / g.SAFEID / g.ZID / g.COOKIE 每次访问实时读 ggzlib.state，
# 保证外部脚本（battle_sim.load_player 等）拿到的是 ensure_session 后的值。
_STATE_NAMES = {"USER", "SAFEID", "ZID", "COOKIE", "BASE", "GGZ_DIR"}


def __getattr__(name):
    if name in _STATE_NAMES:
        import ggzlib.state as _st
        return getattr(_st, name)
    raise AttributeError(f"module 'ggz_daily' has no attribute {name!r}")


def main():
    setup_logging()
    user, safeid, zid = ensure_session()
    if not safeid or not user:
        print("无法获取登录信息（用户名/safeid），请确认 cookie 有效")
        sys.exit(1)
    # 登记敏感字段 → Tee 写日志时自动替换为 MD5（终端显示明文，日志文件脱敏可上传）
    add_secret(user)
    add_secret(safeid)
    print(f"[用户={user} safeid={safeid} 出战角色zid={zid}]")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "stat"
    if cmd == "addpoint":
        addpoint()
    elif cmd == "gem":
        gem()
    elif cmd == "gemup":
        gemup()
    elif cmd == "halo":
        halo()
    elif cmd == "wish":
        wish()
    elif cmd == "shop":
        shop(full="--full" in sys.argv)
    elif cmd == "beach":
        no_refresh = "--no-refresh" in sys.argv
        beach(allow_refresh=not no_refresh, wait_after_refresh=not no_refresh)
    elif cmd == "refresh":
        beach_refresh()
    elif cmd == "smelt":
        smelt()
    elif cmd == "warehouse_tidy":
        dry_run = "--dry-run" in sys.argv
        green_only = "--green-only" in sys.argv
        clear_beach = "--clear-beach" in sys.argv
        warehouse_tidy(dry_run=dry_run, green_only=green_only, clear_beach=clear_beach)
    elif cmd == "pk":
        full = "--full" in sys.argv
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        n = int(args[0]) if args else 20
        pk(n, full=full)
    elif cmd == "switch":
        # 切卡: switch 绮 / switch 3012 / switch（列出所有角色）
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        if args:
            arg = args[0]
            switch_card(zid=int(arg) if arg.isdigit() else None,
                        name=arg if not arg.isdigit() else None)
        else:
            switch_card()
    elif cmd == "gift":
        b = 1 if "--bonus1" in sys.argv else (2 if "--bonus2" in sys.argv else 0)
        gift(bonus=b)
    elif cmd == "bonus":
        bonus()
    elif cmd == "gearfit":
        if "--apply" in sys.argv:
            equip_loadout()
        else:
            gearfit()
    elif cmd == "all":
        no_refresh = "--no-refresh" in sys.argv
        b = 1 if "--bonus1" in sys.argv else (2 if "--bonus2" in sys.argv else 0)
        all_daily(no_refresh=no_refresh, bonus=b)
    else:
        stat()


if __name__ == "__main__":
    try:
        main()
    except AuthExpiredError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
