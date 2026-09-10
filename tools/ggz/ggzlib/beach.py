# -*- coding: utf-8 -*-
"""沙滩收取 / 强制刷新（2026-09-10 拆分自主文件）。"""
import re
import time

from ggzlib import state
from ggzlib.http import request, dec, read_block, click, strip_tags, show
from ggzlib.rules import parse_equips, equip_decision
from ggzlib.warehouse import get_store_space, warehouse_tidy
from ggzlib.workshop import get_items


def _read_beach(retries=3, interval=3):
    """读取 f=1 沙滩并解析装备列表，带重试和有效性验证。

    返回 (items, raw_text)：
      items: 解析出的装备列表（可能为空 = 沙滩真空）
      raw_text: f=1 原始返回文本（供诊断）

    ⚠️ 2026-08-18 踩坑修复：
      旧代码 read_block(1) 返回空/异常时直接当"沙滩空"处理，
      实际可能是限流（服务器返回空 body）或会话失效（返回"请重新登录"）。
      现在先验证返回内容有效性，无效则重试；重试仍失败则抛异常而非静默跳过。

    验证规则：
      - 返回 <20 字符 → 先用 f=6 探测接口健康度（2026-08-23：f=1 空沙滩
        本身就返回空字符串，与限流同形，必须用 f=6 区分）：
          f=6 也空 → 限流，重试；f=6 正常 → 沙滩真空
      - 含"请重新登录"/"登录" → 会话失效，直接报错（重试无意义）
      - 含 "<button" 但 parse_equips 解析 0 件 → HTML 格式变化，报警但继续
      - 返回正常 HTML 且无 button → 沙滩真空
    """
    last_raw = ""
    for attempt in range(retries):
        raw = read_block(1)
        last_raw = raw
        # 会话失效：不重试，直接报错
        if "请重新登录" in raw or ("登录" in raw and len(raw) < 100):
            raise RuntimeError(
                f"沙滩读取失败：会话已失效（f=1 返回 {len(raw)} 字符: {raw[:80]!r}）。\n"
                "→ 请运行 py tools/get_cookies.py --game 刷新 cookie.txt"
            )
        # 空响应/极短返回：⚠️ 2026-08-23 修复——f=1 空沙滩本身返回空字符串，
        # 不能直接当"限流"。用 f=6（身上装备）探测接口健康度：
        if len(raw) < 20:
            probe = read_block(6)
            if len(probe) < 20:
                print(f"  ⚠️ f=1 返回异常短（{len(raw)} 字符）且 f=6 也空，疑似限流，"
                      f"{interval}s 后重试 ({attempt+1}/{retries})")
                time.sleep(interval)
                continue
            # f=6 正常 → 确认沙滩真空
            return [], raw
        # 有效 HTML：尝试解析
        items = parse_equips(raw, want_id=True)
        if items:
            return items, raw
        # 有 button 标签但解析 0 件 → 格式可能变了
        if "<button" in raw and "ys/icon/z" in raw:
            print(f"  ⚠️ f=1 含装备按钮但 parse_equips 解析 0 件（HTML 格式可能变化），重试 ({attempt+1}/{retries})")
            time.sleep(interval)
            continue
        # 正常 HTML 但无装备按钮 → 沙滩真空
        return [], raw
    # 重试耗尽（2026-08-19 修复：抛异常而非 return [] 与"沙滩真空"混淆）
    raise RuntimeError(f"f=1 读取沙滩失败：重试 {retries} 次仍返回异常内容"
                       f"（最后 {len(last_raw)} 字符: {last_raw[:120]!r}），疑似限流/格式变化")


def get_beach_countdown():
    """读取沙滩自然刷新倒计时（分钟）。

    fyg_beach.php 页面 HTML 服务端渲染：
      <span class="pull-right">距离下次随机装备被冲上沙滩还有 1299 分钟</span>
    ⚠️ f=1 接口**不返回**倒计时（2026-08-24 实测），只能从 fyg_beach.php 页面抓。

    返回 int 分钟数；读取失败/格式变化返回 None（调用方自行降级处理）。
    """
    try:
        text = dec(request(state.BASE + "/fyg_beach.php"))
        m = re.search(r"距离下次随机装备被冲上沙滩还有\s*(\d+)\s*分钟", text)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def beach(allow_refresh=True, wait_after_refresh=True):
    """[4] 沙滩收取 + 清理（4.5 规则）。

    流程：**先检查仓库空格（<10 自动整理腾仓）** → 读 f=1 沙滩 + f=6 身上
    → 逐件决策 → 先 c=1 拾取要收的 → 再 c=20 清理剩余。
    沙滩空但有随机装备箱 → 自动强制刷新（c=12）再筛（allow_refresh=False 时跳过）。

    ⚠️ 仓库预整理（2026-09-05）：拾取占仓库格，空格 <10（一滩最多拾取 10 件）
    → 自动 warehouse_tidy(clear_beach=False) 腾仓；tidy 丢出的装备由本流程
    统一 c=20 回收，不误伤自然刷新装备。

    ⚠️ 踩坑记录（2026-08-18 重写）：
      1. f=1 返回空/异常 ≠ 沙滩空：限流返回空 body 时误判沙滩空 → 跳过清理。
      2. 会话失效（"请重新登录"）：直接 raise 提示重抓 cookie。
      3. 沙滩 id 拾取后重排：逐件拾取后剩余 id 全变，禁止复用旧 id。
      4. ✅ 定论（2026-09-03 澄清）：c=12 后读 f=1 返回空**不存在服务器"空窗期"**，
         真实原因：a) 缺浏览器 reload 的整页 GET（GET-first 治愈）；
         b) 高频轮询触发 f=1 专属真限流（低频重试预防）。
      5. 限流：momozhen 对连续请求限流（返回空/SSL 断开），高频场景需间隔 ≥2s。
    """
    # ⚠️ 仓库预整理（2026-09-05）：拾取占仓库格，空格不足时 c=1 拾取失败。
    #   ⚠️ clear_beach 必须为 False：tidy 若立即 c=20 会清掉沙滩上**自然刷新
    #   未决策**的装备（误清！）。
    space = get_store_space()
    if space is not None:
        print(f"仓库剩余空格: {space}")
        if space < 10:
            print("→ 仓库空格不足 10，先整理仓库腾空间（只丢沙滩，清理由 beach 统一处理）...")
            try:
                warehouse_tidy(clear_beach=False)
            except Exception as e:
                print(f"  ⚠️ 仓库整理失败（继续 beach，拾取可能失败）: {e}")
    else:
        print("⚠️ 仓库空格读取失败（疑似限流/格式变化），跳过预整理（拾取可能因空间不足失败）")
    # ⚠️ 自然刷新倒计时（2026-08-24 新增）：数据源 = fyg_beach.php 服务端渲染。
    #   1320 分钟倒计时归零后装备**不会立即**冲上沙滩，服务器还有 ≈4~14 分钟调度延迟
    #   （完整实测时间线见 08-24 日志：理论归零 09:28 → 实际冲上 09:32~09:42）。
    countdown = get_beach_countdown()
    if countdown is not None:
        print(f"沙滩自然刷新倒计时: {countdown} 分钟")
    # 装备箱持有量（2026-08-24 提到开头无条件打印）
    boxes = get_items().get("it004", 0)
    print(f"随机装备箱持有: {boxes}")
    try:
        items, raw = _read_beach()
    except RuntimeError as e:
        print(f"  ❌ 沙滩读取失败: {e}")
        print("  → 限流通常是暂时的，稍后重跑 beach 即可；若持续失败请检查 cookie")
        return
    if not items:
        # 确认是真空（_read_beach 已排除限流/会话失效）
        print(f"沙滩空，无装备")
        # 若倒计时 ≤15 分钟 → 自然刷新在即，**不耗装备箱**，跳过等自然刷新
        if countdown is not None and countdown <= 15:
            print(f"→ 自然刷新倒计时仅剩 {countdown} 分钟（归零后约 4~14 分钟延迟），"
                  f"即将自动冲装备 → 跳过本次（保留装备箱）")
            return
        if allow_refresh and boxes > 0:
            print("→ 有装备箱，自动强制刷新沙滩...")
            r = click(12)
            show("c=12 刷新沙滩返回", r)
            # c=12 成功后倒计时重置 1320、装备箱 -1，重新读取打印（与开头一致）
            if r.strip() == "ok":
                boxes = get_items().get("it004", 0)
                print(f"随机装备箱持有: {boxes}（刷新消耗 1 个）")
                cd = get_beach_countdown()
                if cd is not None:
                    print(f"沙滩自然刷新倒计时: {cd} 分钟（c=12 已重置）")
        elif allow_refresh:
            print("→ 无随机装备箱，跳过")
            return
        elif not wait_after_refresh:
            print("→ 用户指定不刷新（--no-refresh），跳过沙滩（保留装备箱）")
            return
        else:
            print("→ 刚刷新过（allow_refresh=False），按 GET-first 策略重读...")
        # ✅ c=12 后读取铁律（2026-08-24 重构，2026-09-03 定稿）：先 GET 后 POST。
        #   浏览器 gx_sxst() 在 c=12 返回 ok 后执行 window.location.reload()
        #   （整页 GET fyg_beach.php），页面加载时 stall() 再 POST f=1 读装备。
        #   服务端需要一次页面 GET 确认状态，脚本缺这步 → f=1 返回空
        #   （08-24 实测：c=12 ok 后脚本 55s 内 f=1 全空，浏览器 reload 立即有装备）。
        #   ⚠️ 此规律是普适的，不限于 c=12 后：首次读 f=1 前若从未 GET 过
        #   fyg_beach.php 也可能空。
        #   GET 后立即读 1 次；仍空则 10s 间隔重试兜底（最多 4 次覆盖 0-30s），
        #   兜底针对真限流/服务器延迟，勿改为 2s 密集轮询（会触发 f=1 专属限流）。
        try:
            request(state.BASE + "/fyg_beach.php")  # 模拟浏览器 reload（铁律：先 GET 后 POST f=1）
        except Exception:
            pass  # reload 失败不致命，继续尝试读 f=1
        for attempt in range(4):
            try:
                items, raw = _read_beach(retries=1)  # 单次读取
            except RuntimeError:
                items = []
            if items:
                print(f"  ✅ 刷新后第 {attempt + 1} 次读取到 {len(items)} 件装备")
                break
            if attempt < 3:
                print(f"  ⏳ 刷新后第 {attempt + 1} 次仍空，10s 后重试...")
                time.sleep(10)
        else:
            print("  ⚠️ 刷新后 30s 仍未读到装备（可能服务器延迟，可稍后重跑 beach）")
            return
    worn = parse_equips(read_block(6))
    # 仓库读取（2026-09-05: 同名保留最好规则需要仓库数据）;
    # 读取失败(限流)只告警, 不中断 — 同名规则退化为仅对比沙滩同批
    store = []
    try:
        store = parse_equips(read_block(2))
    except Exception as e:
        print(f"  ⚠️ 仓库读取失败（同名规则可能不准确）: {e}")
    print(f"沙滩 {len(items)} 件，身上 {len(worn)} 件，仓库 {len(store)} 件")
    for it in items:
        mark = []
        if it["has_orange"]:
            mark.append("橙")
        if it["has_red"]:
            mark.append("红")
        if it["mystery"]:
            mark.append("神秘")
        mark_str = f" 词条[{'/'.join(mark)}]" if mark else ""
        print(f"  >{it['name']} {it['quality']}等 {it['total']:.0f}% icon={it['icon']} id={it['bid']}{mark_str}")
        # 词条明细（2026-08-23 新增：装备记录用）
        for a in it["affixes"]:
            color_cn = {"danger": "红", "warning": "橙", "info": "蓝",
                        "primary": "紫", "success": "绿"}.get(a["color"], a["color"])
            print(f"      {a['text']}  [评分{a['pct']:.0f}% 词条{color_cn}]")
        if not it["affixes"]:
            print(f"      （无词条明细）")

    take_ids, clear_count = [], 0
    print("逐件决策（规则引擎）:")
    for it in items:
        if it["bid"] is None:
            continue
        action, reason = equip_decision(it, worn, store, items)
        if action == "take":
            take_ids.append(it["bid"])
            print(f"  ✅ {it['name']} {it['quality']}等 {it['total']:.0f}% → 拾取 [{reason}]")
        else:
            clear_count += 1
            rsn = f"（{reason}）" if reason else "（未命中规则）"
            print(f"  ➖ {it['name']} {it['quality']}等 {it['total']:.0f}% → 清理{rsn}")
    print(f"决策: 拾取 {len(take_ids)} 件, 清理 {clear_count} 件")

    for bid in take_ids:
        r = click(1, id=bid)
        show(f"c=1 拾取 id={bid}", r, 80)
    if clear_count > 0:
        r = click(20)
        show("c=20 清理沙滩", r)


def beach_refresh():
    """[4.5] 强制刷新沙滩（耗 1 随机装备箱，c=12）→ 刷新后按 4.5 规则再筛一轮

    ⚠️ 2026-08-16：beach 已恢复自动刷新，这里先 c=12 再调 beach 会二次刷新白耗箱子，
    故传 allow_refresh=False 让 beach 不重复刷（c=12 后由 beach 走 GET-first 读 f=1）。
    """
    boxes = get_items().get("it004", 0)
    print(f"随机装备箱持有: {boxes}")
    if boxes <= 0:
        print("无随机装备箱，跳过强制刷新")
        return
    r = click(12)
    show("c=12 刷新沙滩返回", r)
    beach(allow_refresh=False, wait_after_refresh=True)
