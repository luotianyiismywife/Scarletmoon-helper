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
| `2ed4e_winduser` | **认证凭据**（HttpOnly，值形如 `<base64凭据>`），登录态核心 |
| `2ed4e_ck_info` | 登录信息 |
| `2ed4e_threadlog` | 浏览记录 |
| `2ed4e_lastvisit` | 最后访问时间（URL 编码：`时间戳\t时间戳\t%2F路径`） |
| `2ed4e_lastpos` / `2ed4e_ol_offset` / `2ed4e_skinco` | 杂项（位置/皮肤等） |
| `PHPSESSID` | **会话 ID（仅存于浏览器内存，不落盘）** |

### 登录态判定（重要！）
- **已登录**的页面包含 `login.php?action=quit`（退出登录链接）
- ❌ 误判陷阱：`login.php` 字符串在**已登录页面也大量存在**（退出链接 `login.php?action=quit&verify=...`），不能凭"页面含 login.php"判断未登录
- **Cookie 提取注意**：`PHPSESSID` 是会话 Cookie 只存在浏览器内存，`cookies.sqlite` 里**没有**；但服务器每次请求会 Set-Cookie 新的 PHPSESSID，用 `requests.Session` 自动管理即可，无需手工拼

### 有效期（2026-08-23 实测）
- **`2ed4e_*` 持久 Cookie 有效期约 1 年**：服务器 Set-Cookie 的 `expires` 为登录日 +1 年（实测 `2026-08-23` → `2027-08-23`）；sqlite 中 expiry 亦在次年（约 2027-08）
- **PHPSESSID 会话 Cookie 不落盘**：浏览器关闭/会话过期即失效；论坛每次请求自动 Set-Cookie 刷新
- 注意：即使 `2ed4e_*` 未到 1 年，**改密码/论坛侧登出**会使旧 cookie 立即失效

### ⚠️ UA 版本校验（2026-08-23 血泪教训）
- PHPWind 论坛**校验 User-Agent 版本**：用旧版 UA（如 `rv:137.0`）请求会**强制登出**（302 → `login.php`），即使 cookie 完全正确
- 浏览器实际发送的 UA 与当前 Firefox 版本一致（如 `rv:156.0`；Nightly 的 UA 版本号去 `a1` 后缀）
- **脚本必须用与浏览器一致的 UA**；`tools/get_cookies.py` 已实现动态读取 `application.ini` 版本生成 UA，升级浏览器后无需改代码

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

### 3.2b 付费帖机制（实测 2026-08-12）⭐
- **付费帖特征**：正文含 `<fieldset><legend>此帖售价 N KFB,已有 M 人购买</legend>`；未购买时正文被隐藏，显示"**购买后可见本内容**"
- **列表页不显示价格**：搜索结果/版块列表**无付费标记**，必须进帖子页才能看到"此帖售价"
- **购买接口（未购买时页面按钮 onclick）**：
  ```
  GET https://bbs.kfpromax.com/job.php?action=buytopic&tid=<帖子tid>&pid=tpc&verify=<verify>
  ```
  - `verify` = 页面按钮 onclick 里的防伪码（如 `88fd6254`），每次打开页面可能变化
  - 表单结构（`read.php?tid=X&sf=Y` POST）：hidden 字段 `fid` / `tid`，button `愿意购买,支付KFB`
  - **购买按钮只在未购买时显示**；已购买则正文直接可见
- **购买人名单**：fieldset 内 `<select name="buyers">` 列出已购者用户名
- **购买策略（项目约定 2026-08-12）**：**售价 ≤10 KFB 的付费帖直接自动购买**（`fetch_posts.py` 已实现 `PAID_RE` 检测 + `buy_paid()` 自动购买）；>10 KFB 跳过并提示
- 实测样本：tid=1064155「咕镇金融时报」售价 6 KFB，103 人购买（账号已购买，正文可读）

### 3.3 搜索接口：`/search.php`（实测 2026-08-05；2026-08-13 补充实测）
**POST 全站搜索（表单提交）：**
```
POST https://bbs.kfpromax.com/search.php?
Content-Type: application/x-www-form-urlencoded

step=2&method=AND&sch_area=0&s_type=forum&f_fid=all&orderway=lastpost&asc=DESC&keyword=<GBK编码>&pwuser=&submit=全站搜索
```
- `keyword` 必须按 **GBK** URL 编码（如"咕咕镇"=`%B9%BE%B9%BE%D5%F2`）；用 UTF-8 会搜到乱码/无结果
- **⭐ 编码大坑（2026-08-13 实测）**：Python `requests` 用 dict 提交表单时默认按 **UTF-8** 编码 → keyword 乱码 → 服务器**静默失败**，返回的不是搜索结果页而是含侧栏"最新帖"的普通页面（看起来像 200 成功）。必须**手工拼 raw body**：`quote(kw.encode('gbk'))` 后拼进 `data=` 字符串（见 `tools/forum/search_posts.py`）
- **搜索需登录态**：cookie 失效时搜索同样静默返回首页（响应无 `action=quit`、len≈5000）；先确认登录态再搜索
- `sch_area=0` 标题搜索；`s_type=forum` 版块范围
- 需先 GET `/search.php` 建立会话（拿 PHPSESSID）再 POST
- 响应为 HTML 结果表格：表头 `标题 | 版块 | 发表`，每行 = 标题链接(`read.php?tid=...&sf=...&keyword=...`) | 版块 | 作者+最后回复时间
- **结果链接特征**：真正的搜索结果链接带 `&keyword=...` 参数——用它区分搜索结果与页面侧栏的最新帖列表（解析时只认带 keyword 的 `read.php` 链接）
- **⭐ 扫描用途**：搜索结果的"发表"列（最后回复时间）可用于**判断帖子是否有新内容**——对比上次扫描时间即可筛选新帖（tid 不在索引）和旧帖新回复（时间更新），无需逐篇打开（详见 咕咕镇-新争夺资料/06 索引「重新扫描方法」节）
- 页脚：`共搜索到了 N 条信息 本日剩余搜索次数 M 次`（**每日搜索次数有限**，实测约 30 次/日）
- 每页约 60 条；结果 ≤60 条时只有 1 页（实测："新争夺"58 条 1 页、"旧争夺"355 条多页）
- **工具**：`tools/forum/search_posts.py --kw 关键词 [--pages N] [--json out.json] [--dump xx.html]`（GBK 编码 + 翻页 + 去重 + JSON 输出）

**翻页（GET）：**
```
GET https://bbs.kfpromax.com/search.php?step=2&keyword=%B9%BE%B9%BE%D5%F2&sid=<搜索会话ID>&page=N
```
- `sid` = 搜索会话 ID，从第一次搜索结果页的分页链接里提取（随会话变化）

### 3.3b 帖子图片提取（2026-08-13 实测）
- PHPWind 帖内 `<img>` 的 onclick 含 `this.width>800`，其中的 `>` 会**破坏标签结构**——用 `<[^>]+>` 剥标签会提前截断，正文里残留 `=800) window.open('...` 碎片
- 图片 URL 两个来源：① `<img ... src="URL">` 属性；② onclick 里的 `window.open('URL')`（大图点击放大）
- 需排除表情图（路径含 `post/smile`）；相对路径补全为 `https://bbs.kfpromax.com/...`
- 论坛附件图路径特征：`/<数字>/Mon_YYMM/...`（如 `1786625453/Mon_2...`）；外站图床常见 `i.loli.net` / `s1.ax1x.com` / `p.inari.site` 等（外站图可能已失效）
- **工具**：`fetch_posts.py --images`：提取图片 URL 写入正文文件 `Images:` 节 + 下载到 `raw/img/<tid>/`（已抓过的帖自动回填）

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
| `post.php` | 发帖/回复（详见 3.6） |

### 3.5b 板块扫描 / 楼层精确解析 / 帖子状态（2026-08-14 扒自 kf-analysis ⭐）

> 参考项目：**`github.com/kisaragizen/kf-analysis`**（绯月论坛活跃度分析 v2.0.0，SQLite 存 20 万+ 回复实测）。
> 该项目无私有接口（全 GET 公开页），但解析逻辑完善，以下均照搬/改造。

#### 板块扫描：`/thread.php?fid=X&orderway=lastpost&page=N`
- **用途**：按板块扫全部主题（tid/sf/标题/回复数），**替代全站搜索**（无 30 次/日限制）
- 翻 N 页（默认 10）+ **最后重抓第 1 页去重**（防翻页期间帖子浮动遗漏）
- 行结构（每主题一个 `<tr>`）：
  ```html
  <tr>
    <td><a href="read.php?tid=1083618&sf=267">新</a></td>  <!-- 新帖标记 -->
    <td><div class="threadtit1"><a href="read.php?tid=1083618&sf=267" title="完整标题">标题</a></div></td>
    <td><ul class="b_tit6"><li><a href="...">8<br><span>140</span></a></li></ul></td>  <!-- 8回复/140浏览 -->
    <td><a href="profile.php?action=show&uid=768869" class="bl">用户名</a> | 21:10<br>最后回复者 | 23:43</td>
  </tr>
  ```
- **关键解析点**：标题取 `<a title="...">` 属性（非标签文本，文本可能是"新"标记）；回复数取 `b_tit6` 的 `<li>` 内第一个数字（后随浏览数）
- **工具**：`fetch_posts.py --fid <板块id> [--max-pages N]`（实测：fid=5 自由讨论区，60 条/页）

#### 楼层精确解析：`/read.php?tid=X&sf=Y`（`parse_replies` 逻辑）
- 每楼 `.readtext` 块（id 为 `pidtpc`=楼主 / `pid<数字>`=回复）：
  - 回复者：`div.readidmsbottom a` → 文本=用户名，href=`profile.php?action=show&uid=<uid>&sf=<sf>`（拆 uid+sf）
  - 楼层号：`<span style='font-size:16px;font-weight:bold'>`（"楼主"=0 层）
  - 回帖时间：`<div style='line-height:30px'>` 内 `#999999` 色 span，`%Y-%m-%d %H:%M`
  - 正文：`.readtext` 内 `菜单</a>` 之后 → `</table>` 之前
  - 关键词@列表、图片列表、权限框（`complete` 状态）均可提取
- **回复 id 规则**：楼主 = `TPC<tid>`；回复 = `PID<pid>`（大写）

#### 帖子状态判定（`check_page_status`）
| 状态 | 判定 | 含义 |
|------|------|------|
| normal | 正常正文 | 可抓 |
| closed | 页面含关闭标记 | 被管理员关闭（存占位） |
| deleted | 页面含删除标记 | 被删除（存占位） |
| incorrect | 无此帖/安全码错 | 不存 |

#### 增量抓取策略（`save_incremental_tx`）
- 存 `reply_count`，下次抓只抓新楼层：`db_total < reply_count` 时从 `db_total//20+1` 页开始（20 楼/页）——比全量重抓省大量请求

#### 其他可扒（kf-analysis 已实现）
- `profile.php?action=show&uid=X&sf=Y` 用户主页解析（`parse_profile_page`）
- 付费帖购买封装（`buy_topic`，mode=buy 执行购买）——我们已用 `fetch_posts.py` 实现同类功能（≤10 KFB 自动买）

### 3.6 发帖 / 回帖：`/post.php`（2026-08-14 浏览器+脚本实测 ⭐）

**已实现脚本**：`tools/forum/forum_post.py`（new 发帖 / reply 回帖 / check 查回复）。

#### 会话前提（关键坑）
- **必须用 `requests.Session`**（域内 cookie + 自动跟随 Set-Cookie 的 PHPSESSID）。用 urllib 手拼 `Cookie` 头会导致会话建立不了，**所有页面被 302 到 `login.php`**（实测）。
- **`sf` 令牌是绑定会话的**：浏览器会话里拿到的 `read.php?tid=X&sf=YYY` 的 sf，拿到脚本自己的会话里用会被重定向回首页。脚本要在**自己的会话内**重新获得 sf（如经搜索接口，见 3.3）。
- 登录态判定同 §2：页面含 `login.php?action=quit`。

#### 发新帖
1. `GET post.php?action=new&fid=<版块>` → 返回表单，提取 hidden `verify`（会话级防伪令牌，形如 `8649c7cb`）。
2. `POST post.php?`，body（**GBK 编码**）关键字段：
   ```
   action=new & step=2 & fid=<版块> & tid=0 & verify=<令牌>
   & atc_title=<标题> & atc_content=<正文> & diy_guanjianci=<关键词>
   & atc_iconid=93 & atc_usesign=1 & atc_convert=1 & atc_autourl=1
   & special=0 & article= & pid= & magicname= & magicid= & atc_downrvrc1=0 & atc_desc1=
   & Submit=确定发表
   ```
3. 成功 → 302/落地 `read.php?tid=<新tid>&sf=<新sf>`。

#### 回帖
1. `GET read.php?tid=<tid>&sf=<sf>` → 页面内回复表单含 hidden `verify`、`fid`（**fid 建议从页面动态提取**，勿写死）。
2. `POST post.php?`，body（GBK）：
   ```
   action=reply & step=2 & fid=<版块> & tid=<tid> & verify=<令牌>
   & atc_content=<正文> & atc_title=none & atc_usesign=1 & atc_convert=1 & atc_autourl=1
   & diy_guanjianci= & Submit=回复帖子
   ```
3. 成功 → 返回 **meta-refresh 跳转页**（`requests` 不跟随 meta-refresh，需靠它判定成功）：
   ```html
   <meta http-equiv="refresh" content="1;url=read.php?tid=<tid>&sf=<sf>&page=e&#a">
   ```
   `page=e&#a` = 跳到最后一页并定位到新回复。

#### 回复指定楼层 / 多楼层（2026-08-16 浏览器实测 ⭐）
> PHPWind **没有 article/pid 隐藏字段**（表单里 `pid`、`article` 为空）——"回复楼层"不是字段，
> 而是 **正文加 `[quote]` 前缀 + 关键词 @作者**，与前端 `postreply()` 一致：
> ```js
> function postreply(txta, txtb){
>   document.FORM.atc_content.value = '[quote]'+txta+'[/quote]\r\n';
>   document.FORM.diy_guanjianci.value = txtb;
> }
> ```

**单楼层**（每个楼层"回复"按钮的 onclick 提供模板）：
```html
<a onclick="postreply('回 8楼(无言的喧嚣) 的帖子','无言的喧嚣');">回复</a>
```
- `atc_content` = `[quote]回 8楼(无言的喧嚣) 的帖子[/quote]\r\n` + 正文
- `diy_guanjianci` = 作者名（@对方，渲染成关键词链接）

**多楼层（一次回复多个楼层，2026-08-16 实测成功）⭐**：
- 前端 `postreply()` 是**覆盖式赋值**（连点多个"回复"不会累积）——多楼层需**手工拼接**：
  ```
  atc_content = "[quote]回 8楼(无言的喧嚣) 的帖子[/quote]\r\n"
              + "[quote]回 9楼(zerostar) 的帖子[/quote]\r\n"
              + "正文内容"
  diy_guanjianci = "无言的喧嚣,zerostar"   # 逗号分隔，多个作者
  ```
- 服务器接受并渲染成多个独立 **Quote: 回 N楼(作者)** 块（实测 11 楼：`Quote: 回 8楼…` + `Quote: 回 9楼…`）
- 关键词渲染：`无言的喧嚣 . zerostar .`（作者间用 `.` 分隔）
- 楼层号规则：**0 = 楼主**，1/2/3… = 第 1/2/3 个回复（页面 `postreply('回 N楼(作者) 的帖子')` 即楼层号）

**楼层号/作者提取**：从帖子页抓所有 `postreply('回 N楼(作者) 的帖子','作者')`（每个回复一个），
按 N 匹配目标楼层取作者。已实现于 `tools/forum/forum_post.py reply(--floor N)`；多楼层建议
`--floors "8,9"`（逗号分隔）→ 逐个提取作者拼多个 `[quote]` 前缀。

**⚠️ 删除自己回复**：PHPWind 普通用户**无删除权限**（页面只有"编辑帖子"无"删除"）。
编辑接口 `post.php?action=modify&fid=<fid>&tid=<tid>&pid=<pid>&article=<楼层>`（测试 08-16
验证：编辑页被重定向搜索页，疑似需要更高权限/特殊 sf）。测试回帖只能**改内容为正常文本**
或留着——发测试帖前先用小号/低价值帖验证。

#### 字段说明
| 字段 | 说明 |
|------|------|
| `atc_title` | 标题（新帖必填；回帖填 `none`） |
| `atc_content` | 正文（纯文本/BBCode，见表情） |
| `diy_guanjianci` | 关键词：≤5 个、英文逗号隔、每个 ≤16 字节、不含引号；**填别人 ID 会 @ 对方**；渲染成 `guanjianci.php?gjc=<词>` 链接 |
| `atc_iconid` | 主题图标 id（自由讨论区默认 `93`，hidden 固定值） |
| `verify` | 防伪令牌，从表单页动态提取 |
| `atc_usesign` | 1=带签名档 |

#### 编码坑（务必）
- 论坛 GBK，POST body 必须**逐字段 `.encode('gbk')` 后 percent-encode**（同 3.3 搜索的坑）。UTF-8 提交中文会乱码/被拒。

#### 表情（详见 `论坛表情对照表.md`）
- 正文写 `[s:编号]`（编号 10–57），发表后渲染成 `em/emNN.gif`（NN=编号-9）。
- 例：`[s:57]`=哈哈大笑、`[s:20]`=委屈、`[s:39]`=憨笑。

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
- 从 Firefox Nightly 配置文件 `cookies.sqlite`（只读）提取 Cookie → 写入 `cookie.txt`
- 默认同时提取：论坛认证 `2ed4e_*`（`bbs.kfpromax.com`）+ 咕咕镇游戏 `fyg2019_*`（`www.momozhen.com`）
  - `python tools/get_cookies.py --forum` 仅论坛；`--game` 仅咕咕镇
- **`--login`：账号密码登录**（不依赖浏览器）：
  ```powershell
  $env:KF_USER = "账号"; $env:KF_PASS = "密码"
  python tools/get_cookies.py --login
  ```
  - 流程：POST `login.php?`（PHPWind 表单 `pwuser`/`pwpwd`/`step=2`，**无验证码**）→ 登录成功 → 走入口链 `fyg_sjcdwj.php?go=play&xl=2` → 自动登录链 `fyg_login.php?m=li` → 游戏主页，服务器下发新 `fyg2019_*`（含新 `endtime`）
  - 账号密码从环境变量读，**不写代码/不入库**；适合 cookie 过期后刷新（游戏 cookie 约 1 天有效）
- **`--refreshggz`：复用论坛 cookie 刷新咕咕镇（2026-08-23 新增，无需账号密码）**：
  ```powershell
  python tools/get_cookies.py --refreshggz
  ```
  - 流程：读 `cookie.txt` 的 `2ed4e_*` → 请求 `fyg_sjcdwj.php?go=play&xl=2`（302 时 Set-Cookie 下发新 `fyg2019_*`）→ 请求 `fyg_index.php` 验证（含"个人信息"即成功）
  - **前提**：论坛登录态仍有效（`2ed4e_*` 约 1 年有效，见 §2 有效期）
  - 实现要点（2026-08-23 血泪）：① **CookieJar 预加载 + 不手写 Cookie 头**——手动 `Cookie:` 头优先级高于 CookieJar，会覆盖 302 Set-Cookie 的新值，`fyg_index.php` 用旧 cookie 被拒（27 字节"请重新登录并刷新！"）；② **PHPSESSID 只留游戏域**——cookie.txt 单值不分域，写回论坛域 PHPSESSID 会让游戏请求会话失效；③ UA 动态生成（见 §2 UA 校验）
- 咕咕镇游戏 Cookie：`fyg2019_gameuid/gamepw/endtime/logme`；PHPSESSID 为会话 Cookie 不落盘，由服务器首次请求自动补发
- ⚠️ **单会话互顶**：`--refreshggz`/`--login` 刷新会顶掉浏览器里的游戏会话（咕咕镇单会话），刷新后浏览器游戏页变"请重新登录"属正常现象，别误以为 cookie 坏了
- `cookies.sqlite` 路径：`%APPDATA%\Mozilla\Firefox\Profiles\30hfbhjk.default-nightly\cookies.sqlite`
- 表：`moz_cookies`（含 `originAttributes` 分区列，查询时按 host LIKE 过滤即可）

### tools/forum/search_posts.py（2026-08-13 新增）
- 全站搜索标题关键字，抓全部结果页，输出 tid/标题/URL/最后回复时间
- `python tools/forum/search_posts.py --kw 旧争夺 [--pages N] [--json out.json] [--dump xx.html]`
- 已内置 GBK 编码修复（手工拼 raw body）、keyword 链接过滤、翻页、去重；探测时先 `--pages 1` 省搜索次数

### tools/forum/fetch_posts.py
- 读取索引表格中的 ⬜ 未读帖子 URL → 批量抓取正文 → 存 `docs/<资料目录>/raw/{tid}.txt`
- **`--dir` / `--index` 指定资料目录与索引文件**（默认 `咕咕镇-新争夺资料/06-论坛帖子索引.md`；旧争夺用 `--dir 旧争夺资料 --index 03-论坛帖子索引.md`）
- 用 `requests.Session`：先注入 `2ed4e_*` Cookie，PHPSESSID 由服务器自动补
- 登录校验：响应含 `action=quit` 才继续
- 限速：默认 0.8s/篇，可 `--limit` / `--tid` / `--delay` 控制
- 输出格式：`标题 / 日期 / URL / --- 楼层[pid] 作者 日期 --- 正文 / Images: 图片URL列表`
- **抓正文时顺手记录真实发表时间** → `publish_time.json`（省一轮 fetch_publish_time）
- **`--images`**：提取帖内图片 URL 写入正文文件 + 下载到 `raw/img/<tid>/`（已抓过的帖自动回填；见 §3.3b）

### 已归档辅助脚本（已删除，产出物保留）
- ~~`analyze_posts.py`~~：曾用于按关键词分类 raw 帖子 → 产出 `docs/咕咕镇-新争夺资料/analysis.txt`（已删除脚本，清单保留）
- ~~`make_summary.py`~~：曾用于生成每帖摘要 → 产出 `docs/咕咕镇-新争夺资料/summary.txt`（已删除脚本，摘要保留）
- ~~`mark_posts.py`~~：曾用于把表格状态标 ✅/⏭️（已删除，标记已写入文档）
- **`fetch_publish_time.py`**（当前可用）：批量抓取每篇帖子的真实发表时间 → `docs/<资料目录>/publish_time.json`（支持 `--dir`/`--index`；fetch_posts.py 已顺手记录，通常无需单独跑）
- **`sort_posts.py`**（当前可用）：按发表时间倒序重排索引表格（支持 `--dir`/`--index`；兼容带 ⚠️ 标记的行）

---

## 6. 注意事项与坑

1. **GBK 编码**：所有页面是 GBK，`response.text()` 默认 UTF-8 会乱码；须 `TextDecoder('gbk')`（JS）或 `r.encoding='gbk'`（Python）
2. **gzip 压缩**：服务器可能返回 gzip；urllib 需解压，否则字节数异常且中文检测失灵
3. **login.php 误判**：已登录页也含 `login.php`（退出链接），正确标志是 `action=quit`
4. **PHPSESSID 不落盘**：不用手工拼，用 Session 自动管理
5. **SSL 偶发中断**：Python 原生 urllib 偶发 `SSL: UNEXPECTED_EOF`（疑似 TLS 指纹/频率限制），换 requests 或降低频率可缓解
6. **帖子抓取正文**：以 `菜单</a>` 后到 `</table>` 前为准，避免混入页面底部快速回复框
7. **img 标签 onclick 破坏结构**：帖内 `<img onclick="if(this.width>800)...">` 的 `>` 会让 `<[^>]+>` 类正则提前截断；提取图片要同时匹配 `src=` 属性和 `window.open('...')`（见 §3.3b）
8. **搜索静默失败**：keyword 编码错误（UTF-8）或未登录时，搜索不报错而是返回普通页面/首页；判定依据 = 响应里有无带 `keyword=` 的 `read.php` 链接（见 §3.3）
7. **已删除/关闭的帖子**：返回"此帖被管理员关闭"（正文为空），属正常
