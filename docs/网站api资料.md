# 绯月论坛（bbs.kfpromax.com）网站 API 资料

> 整理日期：2026-08-05
> 来源：本项目油猴脚本（签到模块）实测 + Python 批量抓取调试实测
> 用途：为编写绯月论坛自动化脚本（签到、咕咕镇等）提供接口参考

---

## 1. 基础信息

| 项目 | 值 |
|------|-----|
| 域名 | `bbs.kfpromax.com` |
| 编码 | **GBK**（`charset=gbk`），需 `TextDecoder('gbk')` 解码 |
| 服务器 | nginx / PHP 5.4.45 |
| 旧域名 | 2dkf / 9moe / kfgal / kfpromax 等（页面 JS 会统一替换为 bbs.kfpromax.com） |
| 认证 Cookie 前缀 | `2ed4e_`（如 `2ed4e_winduser`） |

---

## 2. 认证与会话（Cookie）

### 关键 Cookie

| Cookie | 说明 |
|--------|------|
| `2ed4e_winduser` | **认证凭据**（HttpOnly，值形如 `BAVcD1c...%3D%3D`），登录态核心 |
| `2ed4e_ck_info` | 登录信息 |
| `2ed4e_threadlog` | 浏览记录 |
| `2ed4e_lastvisit` | 最后访问时间（URL 编码：`时间戳\t时间戳\t%2F路径`） |
| `2ed4e_lastpos` / `2ed4e_ol_offset` / `2ed4e_skinco` | 杂项（位置/皮肤等） |
| `PHPSESSID` | **会话 ID（仅存于浏览器内存，不落盘）** |

### 登录态判定（重要！）
- **已登录**的页面包含 `login.php?action=quit`（退出登录链接）
- ❌ 误判陷阱：`login.php` 字符串在**已登录页面也大量存在**（退出链接 `login.php?action=quit&verify=...`），不能凭"页面含 login.php"判断未登录
- **Cookie 提取注意**：`PHPSESSID` 是会话 Cookie 只存在浏览器内存，`cookies.sqlite` 里**没有**；但服务器每次请求会 Set-Cookie 新的 PHPSESSID，用 `requests.Session` 自动管理即可，无需手工拼

### 请求头（Python 实测可用）
```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
Accept-Language: zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5
```
- 响应可能 `Content-Encoding: gzip`（Python urllib 需手动解压；requests 自动处理）
- 无需伪造 Sec-Fetch-* 头也能通过

---

## 3. 页面接口

### 3.1 签到：`/kf_growup.php`
**实测结论（2026-08-03/04 浏览器验证）：**
- `GET /kf_growup.php` → 返回签到页面 HTML（GBK）
- 签到动作：`GET /kf_growup.php?ok=3&safeid=XXXX` ✅ **真实签到**
- `ok=2&safeid=XXXX&color=N` = ID 颜色设置（**非签到，必须排除**）
- `ok=1` 从未观测到（已从代码移除兼容）
- 已签到判定文本：`今天的每日奖励已经领过了，请明天继续。` 等（见 sign-module.js `SIGN_STATUS_REGEX`）
- `safeid` 是页面内的防伪令牌（不同页面不同）

### 3.2 帖子阅读：`/read.php`
- `GET /read.php?tid=XXXXX&sf=XXX` → 帖子页（GBK）
  - `tid` = 主题 ID；`sf` = 防伪/翻页令牌（搜索结果带 `&keyword=...` 也可访问）
- 正文容器：
  - 楼主：`<div class="readtext" ... id="pidtpc">`
  - 回复楼层：`<div class="readtext" ... id="pid<数字>">`
  - 作者：`<a href="profile.php?action=show&uid=数字&sf=XXX">名字</a>`
  - 日期：`YYYY-MM-DD HH:MM` 文本
  - 正文起点：`class="readcza">菜单</a>` 之后，结束于 `</table>`
- 分页：`read.php?tid=XXX&sf=XXX&page=N`
- **发表时间（真实发布日期）**：楼主信息区文本 `楼主 YYYY-MM-DD HH:MM`（实测 `2025-01-26 02:31`）；正文中形如 `发表时间：YYYY-MM-DD HH:MM`
  - ⚠️ 注意：搜索结果的"发表"列显示的是**最后回复时间**，非发布日期！要真实发布日期必须进帖子页

### 3.3 搜索接口：`/search.php`（实测 2026-08-05）
**POST 全站搜索（表单提交）：**
```
POST https://bbs.kfpromax.com/search.php?
Content-Type: application/x-www-form-urlencoded

step=2&method=AND&sch_area=0&s_type=forum&f_fid=all&orderway=lastpost&asc=DESC&keyword=<GBK编码>&pwuser=&submit=全站搜索
```
- `keyword` 必须按 **GBK** URL 编码（如"咕咕镇"=`%B9%BE%B9%BE%D5%F2`）；用 UTF-8 会搜到乱码/无结果
- `sch_area=0` 标题搜索；`s_type=forum` 版块范围
- 需先 GET `/search.php` 建立会话（拿 PHPSESSID）再 POST
- 响应为 HTML 结果表格：表头 `标题 | 版块 | 发表`，每行 = 标题链接(`read.php?tid=...&sf=...&keyword=...`) | 版块 | 作者+最后回复时间
- 页脚：`共搜索到了 N 条信息 本日剩余搜索次数 M 次`（**每日搜索次数有限**，实测约 30 次/日）
- 每页约 60 条，共 9 页

**翻页（GET）：**
```
GET https://bbs.kfpromax.com/search.php?step=2&keyword=%B9%BE%B9%BE%D5%F2&sid=<搜索会话ID>&page=N
```
- `sid` = 搜索会话 ID，从第一次搜索结果页的分页链接里提取（随会话变化）

### 3.4 用户资料：`/profile.php`
- `profile.php?action=show&uid=XXX` 查看资料
- `profile.php?action=modify` 修改资料
- `profile.php?action=favor` 收藏夹

### 3.5 其他常见接口
| 路径 | 用途 |
|------|------|
| `login.php` | 登录（`login.php?action=quit&verify=XXX` 退出） |
| `index.php` | 首页 |
| `message.php` | 消息 |
| `kf_growup.php` | 签到（见 3.1） |
| `kf_no1.php` | 会员排行 |
| `hack.php?H_name=bank` | 贡献转账 |
| `kf_fw_1wkfb.php?ping=3` | 评分 |
| `guanjianci.php?gjc=用户名` | 被@记录 |
| `diy_read_tui.php` | 推帖（POST，需 safeid） |
| `post.php` | 发帖/回复 |

---

## 4. 咕咕镇小游戏相关（从论坛帖子收集）

### 入口与域名
- 咕咕镇游戏域名：`guguzhen.com` / `momozhen.com`（镜像）
- 手机反代：`m.miaola.work`
- 主页：`fyg_` 前缀的 PHP（如 `fyg_sjcdwj.php?go=play&xl=1`、`fyg_equip.php`）
- Wiki：`https://gugutown.github.io/Wiki/` 和 `https://gu.inari.site/Wiki/`

### 认证
- 咕咕镇 cookie 约 **1 天**有效，频繁刷新；论坛侧触发登录会使旧 cookie 失效
- 登录方式：① 复用论坛 Cookie ② 咕咕镇**密钥**（7 天过期，仅用于咕咕镇）
- 密钥续期：需在咕咕镇内每日刷新（guguzhen-slack 在凌晨 1 点执行）

### 装备页 DOM（护符筛选脚本实测）
- `fyg_equip.php` 页面：
  - 商店/背包：`#eq4 > div.storeDiv > button > h3` / `#eq4 > div.backpackDiv > button > h3`
  - 护符属性：`item.parentElement.querySelectorAll('div>p>span')`（`innerText[1]` 为数值）

### 现有自动化工具（参考实现）
| 工具 | 仓库 | 语言 | 说明 |
|------|------|------|------|
| guguzhen-slack | `github.com/ilusrdbb/guguzhen-slack` | Python | 摆烂工具：多账号/商店/许愿/出击/翻牌/工坊/密钥续期 |
| guguzhen-calculator | `github.com/ilusrdbb/guguzhen-calculator` | C++ | 计算器+战斗模拟器（`newkf.cpp`，`calcBattle()` 100 回合） |
| gugutask | ilusrdbb/guguzhen-slack releases | Rust | 跨平台挂机（除战斗记录外全功能） |
| zyxboy 插件 | 论坛内分发（篡改猴） | JS | 数据采集/装备分类/一键换卡/沙滩批量 |

---

## 5. Python 批量抓取方案（本项目已实现）

### tools/get_cookies.py
- 从 Firefox Nightly 配置文件 `cookies.sqlite`（只读）提取 `2ed4e_*` Cookie → 写入 `cookie.txt`
- `cookies.sqlite` 路径：`%APPDATA%\Mozilla\Firefox\Profiles\30hfbhjk.default-nightly\cookies.sqlite`
- 表：`moz_cookies`（含 `originAttributes` 分区列，查询时按 host LIKE 过滤即可）

### tools/fetch_posts.py
- 读取 `docs/咕咕镇资料/06-论坛帖子索引.md` 表格中的 ⬜ 未读帖子 URL → 批量抓取正文 → 存 `docs/咕咕镇资料/raw/{tid}.txt`
- 用 `requests.Session`：先注入 `2ed4e_*` Cookie，PHPSESSID 由服务器自动补
- 登录校验：响应含 `action=quit` 才继续
- 限速：默认 0.8s/篇，可 `--limit` / `--tid` / `--delay` 控制
- 输出格式：`标题 / 日期 / URL / --- 楼层[pid] 作者 日期 --- 正文`

### 已归档辅助脚本（已删除，产出物保留）
- ~~`analyze_posts.py`~~：曾用于按关键词分类 raw 帖子 → 产出 `docs/咕咕镇资料/analysis.txt`（已删除脚本，清单保留）
- ~~`make_summary.py`~~：生成每帖摘要 → 产出 `docs/咕咕镇资料/summary.txt`（已删除脚本，摘要保留）
- ~~`mark_posts.py`~~：曾用于把表格状态标 ✅/⏭️（已删除，标记已写入文档）
- **`fetch_publish_time.py`**（当前可用）：批量抓取每篇帖子的真实发表时间 → `docs/咕咕镇资料/publish_time.json`
- **`sort_posts.py`**（当前可用）：按发表时间倒序重排 `docs/咕咕镇资料/06-论坛帖子索引.md` 表格

---

## 6. 注意事项与坑

1. **GBK 编码**：所有页面是 GBK，`response.text()` 默认 UTF-8 会乱码；须 `TextDecoder('gbk')`（JS）或 `r.encoding='gbk'`（Python）
2. **gzip 压缩**：服务器可能返回 gzip；urllib 需解压，否则字节数异常且中文检测失灵
3. **login.php 误判**：已登录页也含 `login.php`（退出链接），正确标志是 `action=quit`
4. **PHPSESSID 不落盘**：不用手工拼，用 Session 自动管理
5. **SSL 偶发中断**：Python 原生 urllib 偶发 `SSL: UNEXPECTED_EOF`（疑似 TLS 指纹/频率限制），换 requests 或降低频率可缓解
6. **帖子抓取正文**：以 `菜单</a>` 后到 `</table>` 前为准，避免混入页面底部快速回复框
7. **已删除/关闭的帖子**：返回"此帖被管理员关闭"（正文为空），属正常
