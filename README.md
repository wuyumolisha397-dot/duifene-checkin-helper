# 对分易签到助手 v3

Windows 桌面端（tkinter）自动签到工具，支持数字码、二维码、定位、汉字四种签到类型。单文件 exe，登录态存本地注册表，不联网上报任何数据。

> 仅供学习网络协议与自动化实现。请遵守所在学校与平台的使用规定，不要用于代签等违规场景。

## 下载

在 [Releases](https://github.com/wuyumolisha397-dot/duifene-checkin-helper/releases) 里取 `duifene-checkin-helper-v3.6.exe`，双击即可运行，不需要装 Python。

## 功能

| 签到类型 | 处理方式 |
|---|---|
| 数字码（type=1） | 活动记录 → 手动码 → 教师大屏页取码，按原样提交（不限位数） |
| 二维码（type=2） | 先试「活动 ID 当 state」直签，不行再抓教师大屏的二维码图 → 本地解码 → 直连签到接口提交（全程无需微信） |
| 定位（type=3） | 读课程绑定的教室坐标，在附近取固定落点后提交 |
| 汉字（type=4） | 从教师大屏页取 `HFCheckCodeKey` 里已经排好序的 4 个字 |

> **重要前提**：数字码 / 二维码 / 汉字这三种都要从「教师端大屏页」取码或取图，而那个页面是**按角色渲染**的——只有这门课的教师账号才读得到。纯学生账号只会拿到一个 `HFUserRole=学生`，取码页面上什么码都没有。定位签到不受此限，学生可用。
>
> 数字码/汉字顺序如果取不到（账号是纯学生、老师没开大屏、只填了 TClassID），
> 左栏有「手动码」输入框：填上老师口头报的码，点「用此码签到」立刻签一次；
> 之后自动签到也会优先用它。

其他：

- 课程列表自动拉取，可指定单门课程监听；左栏可滚动，课程多也不会挤掉按钮
- 轮询间隔默认 2 秒（可调到 0.5 秒），对二维码这类只有 9~30 秒窗口的签到很关键
- 日志落盘到 `%LOCALAPPDATA%\duifene\logs\app_YYYYMMDD.log`，界面有「日志」按钮直达
- 失败会按 2 秒间隔重试；服务端明确返回「已结束 / 没有正在进行」时立即停手，不空转
- 命中限流自动静默 5 分钟

## 使用

1. 双击 `对分易签到助手.exe`
2. 输入手机号/账号 + 密码登录（登录态会记住，下次免登录）
3. 在左栏选一门课 → 点「开始监听」
4. 老师发起签到时自动完成，日志区会写明每一步结果

遇到异常先点「诊断」，它会打印当前登录态、课程数、大屏状态、二维码原文、实际提交坐标等，方便定位问题。

## 从源码运行

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python duifene_v3.py
```

打包单文件 exe：

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --clean --noconfirm duifene_v3.spec
```

二维码识别依赖 `zxing-cpp` 的 C 扩展，打包时需要在 spec 里带上（本仓库的 spec 已配置）。

图标分两处，都已进包：exe 文件图标由 spec 的 `icon='assets/icon.ico'` 写进 PE 资源（8 个尺寸 16~256），窗口/任务栏图标由程序启动时读 `assets/icon_256.png`，用 `iconphoto` 挂上（`Toplevel` 一并继承）。换图标只要替换 `assets/icon_256.png` 与 `assets/icon.ico` 后重新打包，不用改代码。

## 实现要点

签到窗口通常只有 9~30 秒，所以整个链路是按「一次签到窗口内能跑完」设计的。

**教师页大屏信息**：`POST /_CheckIn/PC/TeachCheckIn.aspx`（`courseid` + `moduleid=7` + `classtype=1`）返回页面里有三个隐藏域：

- `HFChecktype`：当前签到类型，0 表示没有进行中的签到
- `HFCheckCodeKey`：type=4 时是 4 个汉字（已排好序），type=2 时是二维码图片的 base64 JPG
- `HFIsRefresh` / `HFTimeInterval`：大屏二维码是否定时刷新、刷新间隔

**但这个页面是按角色渲染的**：只有这门课的教师才读得到上面这些域。纯学生账号（2026-10-29 用两个账号、同一门课、同一时刻对照实测）拿到的整份页面里只有一个

```
HFUserRole = {"TUserRole":[{"ID":2,"RoleName":"学生","Inlay":0}]}
```

`HFChecktype` / `HFCheckCodeKey` / `HFCheckInID` / `HFTUserID` 一个都不下发，页面里的 JS 也只有 `getstudentcourse`、`joinclassbyclasscode` 这类学生动作。所以「学生号同样能读到」的说法是错的——那是在教师账号上试出来的结论，别当成通用前提。

**二维码**：真实二维码内容是 `https://www.duifene.com/G.aspx?OnlyId=2&e=<令牌>`。这个地址是给微信浏览器用的，直接访问会 302 到微信 oauth 授权页，落地页是空白——所以不能跟这个跳转。真正能完成签到的是同一个令牌走 `/_CheckIn/MB/QrCodeCheckOK.aspx?state=<令牌>`，带上已有登录态即可。

另外 `POST /_CheckIn/CheckIn.ashx` 的 `action=getcodeimage&cid=<CourseID>` 是「刷新二维码」接口：每调用一次就生成新图并让上一张作废。所以要优先用大屏上那张图，别主动调刷新接口把自己正在用的令牌换掉。

`state` 的取值不止一种，两种部署都见过了：本机实测是二维码里那串 48 位十六进制（与 `G.aspx` 的 `e=` 相同）；另一个开源实现（`wh520-wh/wh-duifene-auto-sign`）的抓包里，`state` 就是轮询接口 `getstudentinlogbyday` 返回的**签到记录 ID**——那一路上根本不用取图、不用解码，也就没有「2~3 秒换一张图」的竞态。所以现在每个活动先拿活动 ID 直签一次（`失败就不再试`，日志 DEBUG 里能看到结论），失败才回到取图解码。

顺带一条实测细节：服务端会同时返回多条 `StatusID=2` 的历史记录，但只有 `CreaterDate` 最新那条还在进行中，旧记录的 `StatusID` 不可信。待签到列表因此按 `CreaterDate` 从新到旧排，避免拿已结束的旧活动反复重试、白烧次数还撞限流。

**定位**：`POST /_CheckIn/CheckInRoomHandler.ashx` 的 `action=getroom&cid=<CourseID>` 直接返回教室经纬度和老师设的容差半径（`Range`，单位米）。容差只是上限，程序只会在教室附近 3~12 米内取一个落点，并且同一次签到（同一活动 ID）每次重试都落在同一个点，换活动才换位置。

坐标来源按这个顺序排成候选列表，**一个点失败就换下一个**（每个候选仍只在圆心 12 米内取点，同一活动重试落点固定）：

0. **同学坐标**：服务端通过 `wss://wsdf.duifene.com/messageHub` 把「有人签到成功」的事件连同他的 **经度/纬度/距教室米数** 广播给全站客户端。距离 ≤150 米的直接抄，几乎必定落在范围内，且不额外发请求
1. 教师页 `HFCheckCodeKey` 里若带 `经度,纬度`（部分定位签到会带），直接用它当圆心
2. 界面「坐标管理」里手工填的坐标（支持粘贴 `114.40061,22.70745` 或高德/腾讯地图链接）
3. 教室：教师页 `HFRoomID` 指向哪间先用哪间，其余按课程里的顺序接着试
4. 这门课上次定位签到成功过的落点（30 天内有效）——被服务端认过，但它只保证「在容差内」，不代表靠近圆心，所以排在教室后面当备胎
5. 兜底坐标（只在一条候选都排不出来时才用——写死的默认点换个人、换门课就没有意义）

每个候选只试 **1 次**就换下一个：定位窗口通常只有 30 秒左右、重试又跟着 10 秒轮询走，一个点试两次就等于把另一个候选挤掉了；候选用完会循环回来，不怕漏。

服务端到底拿哪个点算距离是黑盒：实测出现过「提交点距教室表坐标 7 米，服务端仍回『距离教室较远』」，说明它的圆心并不总是 `getroom` 那间教室。所以失败后不再对着同一个点撞满重试次数，而是换下一个候选（日志里会打「换圆心 2/5: 教室 xx」）。定位签到的失败响应会完整 dump 到 `%LOCALAPPDATA%\duifene\logs\dump\`，事后能对着看服务端到底说了什么。

反过来也有实证：一次成功签到，程序算的落点距「上次成功点」8 米，老师端显示 36 米，而该落点距教室 `e3` 正好 **34.1 米**——说明这一场服务端比的确实是教室 `e3`。所以教室坐标排在「上次成功点」前面，圆心贴着教室取点，服务端算出来就是 5~12 米，既稳又不扎眼。

**WebSocket 推送**：程序在「开始监听」时连上 messageHub（SignalR over WebSocket，`negotiate` → `?id=<token>` → 握手 → `connect` 注册），断线自动重连。它只负责接收坐标，不发任何签到请求；连不上不影响其他功能，只会在日志里提示。

换楼上课、老师临时改地点，用第 2 条覆盖即可。

**数字码**：`getstudentinlogbyday` 返回的 17 个字段里**没有签到码**（`ID / CheckInType / CreaterDate / Remark / ApplyLimitDate / StudentID / CheckInDate / CheckInStatus / CanApply / StatusID / StatusName / ApplyStatus / ApprovalTime / ApproverID / FillDate / FillStatus / ApplyDel`），码只能在教师端大屏页的 `HFCheckCodeKey` 里，而**大屏只在这一场签到还没结束时才给码**。

这一点很容易踩坑：老师结束后，行数据里的 `CanApply` 仍然是 `1`、`ApplyLimitDate` 也还没到——那是**补签申请**的截止时间，不是签到窗口，于是程序会以为还活着，2 秒一次撞满 5 次，最后报「服务端与教师页都没给出签到码」。现在只要大屏明确回 `HFChecktype=0`、而这条活动已经发起超过 2 分钟（`BOARD_SYNC_GRACE_S`，防止刚发起时大屏还没同步），就直接判定这场已结束、停手不再重试；发起不满 2 分钟的仍照常重试。

**汉字**：`HFCheckCodeKey` 在 type=4 时直接就是正确顺序的 4 个字，不需要自己按拼音去排。提交时 `checkincode` 必须 percent-encode 成 UTF-8（`%E9%A0%94...`）：data 传的是 str，requests 原样交给 `http.client`，而它按 latin-1 发送，中文会直接抛 `UnicodeEncodeError`，签到根本发不出去。

## 目录

```
duifene_v3.py      主程序（约 1880 行，GUI + 协议实现）
duifene_v3.spec    PyInstaller 打包配置
assets/            图标：icon.ico（exe 多尺寸）+ icon_256.png（窗口/任务栏）
requirements.txt   运行依赖
_dev/test_*.py     回归测试（纯 mock，不联网）
```

## 测试

```bash
.venv\Scripts\python _dev\test_logic.py    # 判定逻辑
.venv\Scripts\python _dev\test_login.py    # 登录态
.venv\Scripts\python _dev\test_qr.py       # 二维码链路（含 zxing 解码）
.venv\Scripts\python _dev\test_loc.py      # 定位坐标优先级与落点
.venv\Scripts\python _dev\test_board.py    # 大屏信息解析
.venv\Scripts\python _dev\test_closed.py   # 活动已结束的判定
.venv\Scripts\python _dev\test_stale.py    # 「码取不到」其实是活动已结束
.venv\Scripts\python _dev\test_late.py     # 签到发起后才开监听 → 提示来晚了
.venv\Scripts\python _dev\test_layout.py   # 界面布局（课程卡不被挤掉）
.venv\Scripts\python _dev\test_toggle.py   # 登录卡收起/展开
```

**上课前自检**（只读 + 离线，不改动任何数据，几秒出结果）：

```bash
.venv\Scripts\python _dev\selfcheck.py --course 测试
```

检查登录态、课程列表、记录接口、大屏取码字段、数字码/汉字两条提交链路（含汉字 percent-encode）、

应答判定、过期判死。`真实签到` 一项只在**当前有进行中签到**时才可能 PASS，否则 SKIP。

**真机自检**（会真的发起一场签到，签完自动删除；需要该课教师权限）：

```bash
.venv\Scripts\python _dev\selftest_live.py --course 测试 --type 1 --temps 60   # 数字码
.venv\Scripts\python _dev\selftest_live.py --course 测试 --type 4 --temps 60   # 汉字
```

注意：班次列表 `gettclasscheckinlistbypage` 虽然带 `CheckInCode`，但**只列已结束的场次**，

进行中的那一场永远不在列表里（10 秒的场次要等它结束才出现），不能当实时取码来源。

判断“发起是否成功”只能靠大屏页或 `isnowcheckin`。


## 已知限制

- 老师**重开**签到属于全新活动（新 ID、新二维码），可以正常捕获；但已经结束的活动拿不到二维码，也补不了签——过期提交服务端会直接拒绝。
- 定位签到依赖教室配置。老师没在平台里设置教室坐标时只能退回校区默认点。
- **数字码 / 二维码 / 汉字只在「你有这门课教师权限」时能自动签。** 三种都要从教师端大屏页取码或取图，而那个页面按角色渲染，纯学生账号拿不到。纯学生只能用「定位」自动签，或者看到大屏后手动填码扫一次。
- 数字码/汉字如果卡在「拿不到码」，先看日志：写着「只给这门课的教师下发签到码」= 身份问题，填手动码；写着「大屏上这场签到已经不在了」= 发现时这场已经结束，补签只能找老师走申请流程。
- **必须在老师发起签到之前就点『开始监听』。** 签到发起后才开监听，程序只能看到一条已经收摊的记录；此时日志会明写「不是取不到码，是来晚了」。字面意思就是没有别的办法，只能等下一场。
- 平台改版会导致接口失效，`HFChecktype` 之类的字段名是最容易变的部分。

## 更新记录

- **v3.18** 认出「学生版」大屏页（只有 `HFUserRole`、没有 `HFChecktype`）：数字码/汉字/二维码不再报「活动已结束」误导人，改为明说「服务端只给这门课的教师下发签到码」，提示手填；这类身份问题不占重试次数，只每分钟复查一次，随时填了手动码还能立刻用上
- **v3.16** 数字码/汉字「取不到码」不再瞎重试：大屏 `HFChecktype=0` 且活动已发起超过 2 分钟就判它已结束（`CanApply`/`ApplyLimitDate` 是补签窗口，不是签到窗口）；大屏 `type=0` 时也不再拿微信 UA 多打一次
- **v3.17** 发现的活动若比「开始监听」还早，直接提示「不是取不到码，是来晚了」，并写明早了多少；`_dev` 下的测试改写到独立日志目录，不再污染 `%LOCALAPPDATA%\duifene\logs`
- **v3.15** 二维码：新增「活动 ID 当 state」直签通道（部分部署可用，省掉取图解码与换图竞态）；待签到列表改按 `CreaterDate` 从新到旧挑，不再拿已结束的旧记录反复重试
- **v3.14** 数字码放宽到不限位数；汉字/空格统一 percent-encode；定位换圆心按真实重试次数推进
- **v3.7** 定位：优先用签到页自带的中心；多教室按 `HFRoomID` 选楼；「坐标管理」支持粘贴地图坐标/链接
- **v3.6** 定位落点改为教室附近 3~12 米内的确定性偏移，同一次签到固定不变
- **v3.5** 定位坐标改为直接读服务端教室坐标，自动迁移旧版错误的默认坐标
- **v3.4** 移除人工补签通道（二维码与汉字均已全自动）
- **v3.3** 修复二维码签到：识别 `G.aspx` 令牌并走 `QrCodeCheckOK.aspx?state=`，不再跟随微信 oauth
- **v3.2** 修正取图顺序（大屏图优先），避免主动刷新把令牌作废
- **v3.1** 二维码支持微信 oauth 外壳解析、日志落盘
- **v3.0** 重写：登录态持久化、四种签到自动处理、界面重构
