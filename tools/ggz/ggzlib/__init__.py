# -*- coding: utf-8 -*-
"""ggzlib —— 咕咕镇脚本功能库（2026-09-10 自 ggz_daily.py 拆分）。

分层（依赖单向，无循环）：
    state    会话状态唯一宿主（USER/SAFEID/ZID/COOKIE/BASE）+ cookie 加载/自动刷新
    http     requests 会话/重试/限流缓解 + 日志(Tee/脱敏) + 读块/点击/解析
    cards    角色卡：列表/切换/加点
    workshop 工坊/宝石/光环/许愿/商店/道具栏
    rules    装备解析 + 沙滩拾取规则引擎（表达式解析/求值/决策）
    beach    沙滩收取/刷新
    warehouse 仓库整理/熔炼/空格
    gearfit  配装推荐 × 持有查询 × 一键换装
    battle   出击/战场状态/野怪解析/模拟对接/额外奖励
    gift     翻牌（透视自动检测/药水 bonus）
    daily    一键日常编排 + 状态汇总
    api      全量聚合出口（外部脚本 battle_sim/warehouse_tidy 兼容层）

入口仍是 tools/ggz/ggz_daily.py（CLI 兼容壳，tasks.json 零改动）。
模块级配置（WISH_MODE/SHOP_MODE/GEM_TARGET_PCT/BEACH_RULES/GEAR_FIT/
ADDPOINT_STRATEGY/PK_SIM_*）随对应模块走，facade re-export 保持可改。
"""
