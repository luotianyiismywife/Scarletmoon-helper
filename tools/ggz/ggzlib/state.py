# -*- coding: utf-8 -*-
"""会话状态唯一宿主（2026-09-10 拆分自主文件）。

⚠️ 全局 USER/SAFEID/ZID/COOKIE 只允许：
  - 本模块内部赋值（load_cookie/refresh_cookie_auto）
  - http.ensure_session() / 各 main() 入口通过 `state.X = ...` 赋值
其它模块一律读 `state.USER` 属性（实时值），禁止 from-import 值拷贝
（值拷贝会拿到过期快照——这正是旧全局方案迁模块时最容易踩的坑）。
"""
import os
import re

BASE = "https://www.momozhen.com"
GGZ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tools/ggz


class AuthExpiredError(RuntimeError):
    """咕咕镇 cookie 失效（单会话被浏览器顶掉 / 每日刷新）。

    服务器对失效会话返回"请重新登录并刷新！"。此前脚本会把这 9 个字符
    当普通 HTML 解析 → 0 件装备/0 道具 → 静默误判"沙滩空"跳过（2026-08-18 事故）。
    恢复：浏览器走入口链刷新游戏 cookie 后重跑 tools/get_cookies.py --game。
    """


def load_cookie():
    path = os.path.join(GGZ_DIR, "..", "cookie.txt")
    with open(path, encoding="utf-8") as f:
        # 第 1 行 = Cookie 头；第 2 行起 = KF_USER/KF_PASS 登录凭证（2026-09-07），不参与请求
        return f.readline().strip()


COOKIE = load_cookie()
USER = None   # 动态: 主页提取
SAFEID = None  # 动态: 主页提取（写操作令牌）
ZID = None    # 动态: f=8 出战中角色

# cookie 自动刷新状态（防 request() 多次触发刷新）
_cookie_refreshed = False


def refresh_cookie_auto():
    """cookie 失效时自动调用 get_cookies.smart_refresh_ggz() 刷新。

    逻辑（2026-09-07 用户重设计，不再依赖 Firefox Nightly）：
      - cookie.txt 论坛 cookie 有效 → 走论坛入口链刷新（--refreshggz）
      - 论坛 cookie 失效 → 用存储的账号密码重登（--login）
    成功后重新加载 COOKIE 全局变量。整个会话只刷新一次（防多请求重复触发）。
    返回 True=刷新成功，False=刷新失败/已刷新过。
    """
    global COOKIE, _cookie_refreshed
    if _cookie_refreshed:
        return False  # 本次会话已刷新过，不再重复
    _cookie_refreshed = True
    try:
        import sys
        sys.path.insert(0, os.path.join(GGZ_DIR, ".."))
        import get_cookies
        print("\n⚠️ 检测到 cookie 失效，自动刷新中 ...")
        if get_cookies.smart_refresh_ggz():
            COOKIE = load_cookie()  # 重新加载刷新后的 cookie
            print("✅ cookie 已自动刷新，重试请求\n")
            return True
        else:
            print("❌ cookie 自动刷新失败，请手动运行: py tools/get_cookies.py --game")
            return False
    except Exception as e:
        print(f"❌ cookie 自动刷新异常: {e}")
        print("请手动运行: py tools/get_cookies.py --game")
        return False
