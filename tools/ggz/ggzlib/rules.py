# -*- coding: utf-8 -*-
"""装备解析 + 沙滩拾取规则引擎（2026-09-10 拆分自主文件）。

规则引擎（2026-09-05 重构）：
- BEACH_RULES 为**用户自定义配置**: 每条 = (名称, 表达式)。
- 表达式 = **字段 + 比较符 + 值**, 用 and / or / not / 括号组装。
- 满足**任一**规则 → 拾取(take), 全部不满足 → 清理(clear)。
- 字段/比较符/语法完整说明见 tools/ggz/装备词条及筛选规则.md（单一事实源）。
"""
import re
import html
from collections import defaultdict

from ggzlib.http import read_block

# ---------- 装备按钮解析（f=1 沙滩 / f=6 身上 / f=2、f=7 仓库通用） ----------

def parse_equips(html_text, want_id=False):
    """解析装备按钮列表（f=1 沙滩 / f=6 身上 / f=2 仓库 / f=7 通用）。

    每个装备按钮结构（2026-08-12 实测 f=6）：
      <button ... data-content="<p class='fyg_xlxxXXX'>词条名 +N<span class='pull-right bg-*'>&nbsp;150%&nbsp;</span></p>..."
              title="Lv.<span>100</span> 装备名" ...><img src="ys/icon/z2101_4.gif">...
    沙滩版额外含 zbtip('ID','4')；仓库 f=7 是 zbtip('ID','2')（2026-09-10 实测）、
    f=2 是 zbtip('ID','3')。
    返回 [{icon, quality, name, level, total, mystery, bid,
           has_orange, has_red, has_high, affixes}]
    ⚠️ 词条槽固定 4 个（03 文档实测），无 n_affix 字段（恒为 4 无区分度）
    """
    HIGH_AFFIX = ["生命偷取", "附加物伤", "附加魔伤", "附加物穿", "附加魔穿",
                  "技能概率", "暴击概率", "攻击速度"]
    result = []
    for btn_raw in re.findall(r"<button[^>]*>.*?</button>", html_text, re.S):
        if "ys/icon/z" not in btn_raw:
            continue
        # ⚠️ 兼容 HTML 实体转义：fyg_beach.php 页面内 data-content 是转义版
        # (&lt;p class=...&gt;)，f=1 接口返回未转义原生 HTML（2026-08-23 实测）
        btn = html.unescape(btn_raw)
        # icon: background-image:url(ys/icon/z/z2402_2.gif)（品质后缀 _2）
        # ⚠️ 真实路径 icon/ 后带一层 z/ 子目录（2026-08-14 实测）；(?:/z)? 兼容新旧两种写法
        m = re.search(r"ys/icon/z(?:/z)?(\d{4})(?:_(\d))?\.gif", btn)
        icon, quality = (m.group(1), int(m.group(2)) if m and m.group(2) else 0) if m else ("", 0)
        # title: Lv.<span>100</span> <span>星级</span><br>装备名（f=1 沙滩）
        #       Lv.<span class='fyg_f18'>100</span> 装备名（f=6 身上，无 <br>，2026-08-16 实测）
        # ⚠️ 新版沙滩按钮 title 为空、名字在 data-original-title（2026-08-23 实测）
        name = level = "?"
        m = re.search(r'(?:title|data-original-title)="Lv\.<span[^>]*>(\d+)</span>[\s\S]*?(?:<br|</span>)([^"<]*?)(?:"|$)', btn)
        if m:
            level, name = m.group(1), m.group(2).strip()
        else:
            # 旧 f=6 身上: title="Lv.<span class='fyg_f18'>100</span> 探险者之剑"
            m2 = re.search(r'</span>\s*([^"<]+?)"', btn)
            if m2:
                level, name = "?", m2.group(1).strip()
        total = 0.0
        # 词条颜色（2026-08-13 实测 class：danger=红 warning=橙 info=蓝 primary=紫 success=绿）
        has_orange = False
        has_red = False
        has_high = False
        affixes = []  # 词条明细 [{name, text, pct, color}]
        # 每词条: <p class='fyg_xlxxXXX'>词条名 +N<span class='pull-right bg-XXX'>&nbsp;N%&nbsp;</span></p>
        for m in re.finditer(r"<p class='fyg_xlxx(\w+)'>(.*?)</p>", btn, re.S):
            color_cls, affix_html = m.group(1), m.group(2)
            # 词条名+数值文本: <p> 内 <span 前部分
            text_m = re.match(r"\s*(.*?)<span", affix_html, re.S)
            affix_text = text_m.group(1).strip() if text_m else ""
            name_m = re.match(r"\s*([^<+\s]+)", affix_text)
            affix_name = name_m.group(1) if name_m else affix_text
            # 评分百分比: pull-right bg-XXX>...NN%（词条总值评分，非词条自身数值）
            val_m = re.search(r"pull-right bg-(\w+)[^>]*>(?:&nbsp;|\s)*(\d+(?:\.\d+)?)%", affix_html)
            if not val_m:
                continue
            color, pct = val_m.group(1), float(val_m.group(2))
            total += pct
            if color == "warning":
                has_orange = True
            elif color == "danger":
                has_red = True
            if any(kw in affix_name for kw in HIGH_AFFIX):
                has_high = True
            affixes.append({"name": affix_name, "text": affix_text,
                            "pct": pct, "color": color})
        mystery = "[神秘属性]" in btn or "神秘属性" in btn
        # bid：沙滩装备 zbtip('ID','4')；**仓库 f=7 是 zbtip('ID','2')**（2026-09-10 实测，
        #   此前只匹配 '[34]' 导致 f=7 全部 bid=None、换装找不到可穿件）；f=2 是 '3'
        m = re.search(r"zbtip\('(\d+)','[234]'\)", btn)
        bid = m.group(1) if m else None
        result.append({"icon": icon, "quality": quality, "name": name,
                       "level": level, "total": total, "mystery": mystery, "bid": bid,
                       "has_orange": has_orange, "has_red": has_red,
                       "has_high": has_high, "affixes": affixes})
    return result


# ═══════════════ 沙滩拾取规则引擎（2026-09-05 重构）═══════════════
BEACH_RULES = [
    # 含神秘 → 必收（低品质神秘也收, 神秘价值>>装备本身, 独立于可熔炼）
    ("神秘",   "mystery"),
    # 能熔炼 → 收（品质≥3 且 总值≥410%, 供手动熔炼, 长期规则）
    # ⚠️ 已涵盖橙装（2026-09-05 确认逻辑重复后删除橙装规则）:
    #   橙装 total_number>=516 品质必≥3 且 ≥410 → 橙装规则是冗余子集
    ("可熔炼", "quality>=3 and total_number>=410"),
]

# ⭐ 同名硬性过滤（2026-09-05, 默认开）: 同名装备不是最好的 → 直接清理。
# 开着 = 仓库+沙滩同批同名只留一件最好的(品质最高, 同品质留总值最高; 完全相同都保留);
# 关掉 = 同名完全交给 BEACH_RULES 表达式决定。
BEACH_SAME_NAME_BEST = True

# 字段注册表（一级字段）: 名称 → 函数(it, ctx) → 值(数字/字符串/布尔/None)
RULE_FIELDS = {}


def _f(name):
    def deco(fn):
        RULE_FIELDS[name] = fn
        return fn
    return deco


# 字段注册表（二级字段）: 名称 → 函数(一级字段求值函数, it, ctx) → 值
# 二级字段 = 由一级字段**派生**（非独立数据），如 total_number = affix0_pct+affix1_pct+...
DERIVED_FIELDS = {}


def _df(name):
    def deco(fn):
        DERIVED_FIELDS[name] = fn
        return fn
    return deco


def _get_field_val(name, it, ctx):
    """统一取字段值: 一级字段直取, 二级字段派生。未知字段返回 None(调用方报错)。"""
    if name in RULE_FIELDS:
        return RULE_FIELDS[name](it, ctx)
    if name in DERIVED_FIELDS:
        return DERIVED_FIELDS[name](it, ctx)
    return None


@_f("name")
def _f_name(it, ctx):
    return it["name"]


@_f("mystery")
def _f_mystery(it, ctx):
    return it["mystery"]


@_f("quality")
def _f_quality(it, ctx):
    return it["quality"]


# ═══════════ 二级字段（2026-09-05 定义：由一级字段派生，非独立数据）═══════════
# total_number = 4 词条评分之和（一级 affix0~3_pct 派生）


@_df("total_number")
def _df_total(it, ctx):
    total = 0.0
    for i in range(4):
        v = RULE_FIELDS[f"affix{i}_pct"](it, ctx)
        if v is not None:
            total += v
    return total


# ═══════════ 词条位置字段（2026-09-05 新增）═══════════
# 装备固定 4 词条，每种装备词条种类固定 → 按位置访问单个词条（affix0~3/_name/_pct/_color）。
# 词条不足 4 个（解析异常/格式变化）→ 字段返回 None，比较结果为 False（不匹配）。
_AFFIX_SUFFIXES = [("", "text"), ("_name", "name"), ("_pct", "pct"), ("_color", "color")]
for _i in range(4):
    for _suffix, _key in _AFFIX_SUFFIXES:
        def _make_affix_field(idx=_i, key=_key):
            @_f(f"affix{idx}{_suffix}")
            def _f_affix(it, ctx, _idx=idx, _key=key):
                affixes = it.get("affixes") or []
                if _idx < len(affixes):
                    return affixes[_idx].get(_key)
                return None  # 词条缺失 → None（比较时按 False 处理）
            return _f_affix
        _make_affix_field()


def _same_name_allow(it, ctx):
    """同名硬性过滤（2026-09-05 修订）。返回 True=允许收 / False=次品同名直接清。

    比较范围 = **仓库 + 沙滩同批**（2026-09-05 用户指定：不再看身上）。
    判定：品质数字最高才收；**同品质同总值（完全相同）→ 都保留**；
    同品质不同总值 → 总值更高才收。
    BEACH_SAME_NAME_BEST=False 时恒 True(不过滤)。"""
    if not BEACH_SAME_NAME_BEST or it["name"] == "?":
        return True
    rivals = [w for w in ctx["store"] + ctx["beach"]
              if w["name"] == it["name"] and w is not it]
    if not rivals:
        return True
    best_q = max(w["quality"] for w in rivals)
    if it["quality"] > best_q:
        return True
    if it["quality"] < best_q:
        return False
    # 同品质: 总值严格更高才收; 完全相同(同总值) → 都保留
    best_t = max(w["total"] for w in rivals if w["quality"] == best_q)
    return it["total"] >= best_t


# ---------- 表达式解析器（递归下降, 支持 and/or/not/括号/数值比较） ----------

def _tokenize(expr):
    tokens = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c in " \t":
            i += 1
            continue
        if expr.startswith("and", i) and not expr[i + 3:i + 4].isalnum():
            tokens.append(("op", "and")); i += 3; continue
        if expr.startswith("or", i) and not expr[i + 2:i + 3].isalnum():
            tokens.append(("op", "or")); i += 2; continue
        if expr.startswith("not", i) and not expr[i + 3:i + 4].isalnum():
            tokens.append(("op", "not")); i += 3; continue
        if c == "(":
            tokens.append(("op", "(")); i += 1; continue
        if c == ")":
            tokens.append(("op", ")")); i += 1; continue
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", expr[i:])
        if not m:
            raise ValueError(f"无法解析位置 {i}: {expr[i:i + 10]!r}")
        name = m.group(0)
        i += len(name)
        cmp_op = cmp_val = None
        # 数字比较: >= <= == != > < 后跟数字（前导空格容错）
        # ⚠️ ==/!= 后跟数字 = 数字比较（2026-09-05 修复）；后跟引号 = 字符串比较
        m2 = re.match(r"\s*(>=|<=|==|!=|>|<)\s*(\d+)", expr[i:])
        if m2:
            cmp_op, cmp_val = m2.group(1), int(m2.group(2))
            i += len(m2.group(0))
        else:
            # 字符串比较: == != contains startswith endswith 后跟引号字符串
            # ⚠️ alternation 前必须有 \s*（`name contains '剑'` 的 contains 前有空格）
            m3 = re.match(r"\s*(==|!=|contains|startswith|endswith)\s*([\"'])(.*?)\2",
                          expr[i:])
            if m3:
                cmp_op, cmp_val = m3.group(1), m3.group(3)
                i += len(m3.group(0))
        tokens.append(("pred", name, cmp_op, cmp_val))
    return tokens


class _RuleParser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self):
        t = self.peek()
        self.pos += 1
        return t

    def parse(self):
        node = self.parse_or()
        if self.peek():
            raise ValueError("表达式末尾有多余内容")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek() and self.peek()[1] == "or":
            self.next()
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.peek() and self.peek()[1] == "and":
            self.next()
            node = ("and", node, self.parse_not())
        return node

    def parse_not(self):
        if self.peek() and self.peek()[1] == "not":
            self.next()
            return ("not", self.parse_not())
        return self.parse_atom()

    def parse_atom(self):
        t = self.next()
        if t is None:
            raise ValueError("表达式不完整（缺谓词）")
        if t[1] == "(":
            node = self.parse_or()
            nxt = self.next()
            if not nxt or nxt[1] != ")":
                raise ValueError("括号不匹配")
            return node
        if t[0] == "pred":
            return ("pred", t[2], t[3], t[1])
        raise ValueError(f"意外的 token: {t[1]!r}")


def _parse_expr(expr):
    return _RuleParser(_tokenize(expr)).parse()


def _eval_rule(node, it, ctx):
    """三值逻辑求值: True/False/None。None = 中性(该规则不做决定)。"""
    kind = node[0]
    if kind == "or":
        l = _eval_rule(node[1], it, ctx)
        if l is True:
            return True
        r = _eval_rule(node[2], it, ctx)
        if r is True:
            return True
        if l is None or r is None:
            return None
        return False
    if kind == "and":
        l = _eval_rule(node[1], it, ctx)
        if l is False:
            return False
        r = _eval_rule(node[2], it, ctx)
        if r is False:
            return False
        if l is None or r is None:
            return None
        return True
    if kind == "not":
        v = _eval_rule(node[1], it, ctx)
        return None if v is None else (not v)
    # ("pred", cmp_op, cmp_val, name)
    _, cmp_op, cmp_val, name = node
    # ⚠️ 统一取字段: 一级(直取) → 二级(派生)
    if name not in RULE_FIELDS and name not in DERIVED_FIELDS:
        raise ValueError(f"未知字段: {name!r}（可用: {', '.join(sorted(RULE_FIELDS) + sorted(DERIVED_FIELDS))}）")
    val = _get_field_val(name, it, ctx)
    if cmp_op is None:
        return bool(val) if val is not None else None
    # 数字比较
    if cmp_op in (">=", "<=", ">", "<"):
        # ⚠️ 词条缺失（affixN 越界返回 None）→ 不匹配（False），不报错
        if val is None:
            return False
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(f"字段 {name} 值 {val!r} 不能做数字比较 {cmp_op}{cmp_val}")
        if cmp_op == ">=":
            return val >= cmp_val
        if cmp_op == "<=":
            return val <= cmp_val
        if cmp_op == ">":
            return val > cmp_val
        return val < cmp_val
    # == / != : 根据值类型智能比较（2026-09-05 修复）
    if cmp_op in ("==", "!="):
        if val is None:
            return False
        if isinstance(val, bool) or isinstance(val, (int, float)):
            return val == cmp_val if cmp_op == "==" else val != cmp_val
        if isinstance(val, str):
            return val == cmp_val if cmp_op == "==" else val != cmp_val
        raise ValueError(f"字段 {name} 值 {val!r} 无法比较 {cmp_op}")
    # 字符串比较
    if cmp_op in ("contains", "startswith", "endswith"):
        # ⚠️ 词条缺失（affixN 越界返回 None）→ 不匹配（False），不报错
        if val is None:
            return False
        if not isinstance(val, str):
            raise ValueError(f"字段 {name} 值 {val!r} 不能做字符串比较 {cmp_op}")
        if cmp_op == "contains":
            return cmp_val in val
        if cmp_op == "startswith":
            return val.startswith(cmp_val)
        return val.endswith(cmp_val)
    raise ValueError(f"未知比较符 {cmp_op}")


# 表达式编译缓存（每条规则只解析一次）
_RULE_CACHE = {}


def equip_decision(it, worn, store=None, beach_items=None):
    """沙滩装备决策（规则引擎版, 2026-09-05 重构）。返回 (action, reason)。

    action = 'take' / 'clear'；reason = 命中规则名（take 时）或清理原因。
    流程：先求值 BEACH_RULES（任一命中 → take 候选）→ 再套同名硬过滤：
      命中规则 且 通过硬过滤 → take（reason=命中规则名）
      命中规则 但 被硬过滤拦截 → clear（reason=同名硬过滤）⚠️ 硬过滤真正起作用处
      未命中任何规则 → clear（reason=None = 未命中规则）
    ⚠️ 硬过滤后置而非前置（2026-09-05 修正）：前置会把"本来就未命中规则
      的垃圾同名"也归因于"同名硬过滤"，语义误导——垃圾本来就会被清；
      后置才能准确表达"因同名被拦下的是原本会收的装备"。
    """
    ctx = {"worn": worn, "store": store or [], "beach": beach_items or []}
    hit = None
    for name, expr in BEACH_RULES:
        node = _RULE_CACHE.get(expr)
        if node is None:
            try:
                node = _parse_expr(expr)
                _RULE_CACHE[expr] = node
            except ValueError as e:
                print(f"  ⚠️ 规则[{name}] 表达式解析失败: {e} → 跳过该规则")
                continue
        try:
            if _eval_rule(node, it, ctx):
                hit = name
                break
        except ValueError as e:
            print(f"  ⚠️ 规则[{name}] 求值失败: {e} → 跳过该规则")
            continue
    if hit is None:
        return "clear", None  # 未命中任何规则 → 清理
    # 命中规则 → 同名硬性过滤（后置拦截）: 次品同名直接清, 不进仓库
    if not _same_name_allow(it, ctx):
        return "clear", "同名硬过滤（存在更好的同名, 即使命中规则也不收）"
    return "take", hit
