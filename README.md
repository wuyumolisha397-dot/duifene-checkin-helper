# 对分易签到助手 v3

Windows 桌面端（tkinter）自动签到工具，支持数字码、二维码、定位、汉字四种签到类型。单文件 exe，登录态存本地注册表，不联网上报任何数据。

> 仅供学习网络协议与自动化实现。请遵守所在学校与平台的使用规定，不要用于代签等违规场景。

## 下载

在 [Releases](https://github.com/wuyumolisha397-dot/duifene-checkin-helper/releases) 里取 `duifene-checkin-helper-v3.6.exe`，双击即可运行，不需要装 Python。

## 功能

| 签到类型 | 处理方式 |
|---|---|
| 数字码（type=1） | 读服务端下发的 4 位码，或从教师大屏页取码后提交 |
| 二维码（type=2） | 抓教师大屏的二维码图 → 本地解码 → 直连签到接口提交（无需微信） |
| 定位（type=3） | 读课程绑定的教室坐标，在附近取固定落点后提交 |
| 汉字（type=4） | 从教师大屏页取 `HFCheckCodeKey` 里已经排好序的 4 个字 |

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

## 实现要点

签到窗口通常只有 9~30 秒，所以整个链路是按「一次签到窗口内能跑完」设计的。

**教师页大屏信息**：`POST /_CheckIn/PC/TeachCheckIn.aspx`（`courseid` + `moduleid=7` + `classtype=1`）返回页面里有三个隐藏域：

- `HFChecktype`：当前签到类型，0 表示没有进行中的签到
- `HFCheckCodeKey`：type=4 时是 4 个汉字（已排好序），type=2 时是二维码图片的 base64 JPG
- `HFIsRefresh` / `HFTimeInterval`：大屏二维码是否定时刷新、刷新间隔

学生号同样能读到这个页面，这是能自动化的关键。

**二维码**：真实二维码内容是 `https://www.duifene.com/G.aspx?OnlyId=2&e=<令牌>`。这个地址是给微信浏览器用的，直接访问会 302 到微信 oauth 授权页，落地页是空白——所以不能跟这个跳转。真正能完成签到的是同一个令牌走 `/_CheckIn/MB/QrCodeCheckOK.aspx?state=<令牌>`，带上已有登录态即可。

另外 `POST /_CheckIn/CheckIn.ashx` 的 `action=getcodeimage&cid=<CourseID>` 是「刷新二维码」接口：每调用一次就生成新图并让上一张作废。所以要优先用大屏上那张图，别主动调刷新接口把自己正在用的令牌换掉。

**定位**：`POST /_CheckIn/CheckInRoomHandler.ashx` 的 `action=getroom&cid=<CourseID>` 直接返回教室经纬度和老师设的容差半径（`Range`，单位米）。容差只是上限，程序只会在教室附近 3~12 米内取一个落点，并且同一次签到（同一活动 ID）每次重试都落在同一个点，换活动才换位置。

坐标来源按优先级取：

1. 教师页 `HFCheckCodeKey` 里若带 `经度,纬度`（部分定位签到会带），直接用它当圆心
2. 界面「坐标管理」里手工填的坐标（支持粘贴 `114.40061,22.70745` 或高德/腾讯地图链接）
3. 本次签到绑定的教室 —— 教师页 `HFRoomID` 指向哪间就用哪间，一门课挂多间教室时不会认错楼
4. 课程第一间教室 / 兜底坐标

换楼上课、老师临时改地点，用第 2 条覆盖即可。

**汉字**：`HFCheckCodeKey` 在 type=4 时直接就是正确顺序的 4 个字，不需要自己按拼音去排。

## 目录

```
duifene_v3.py      主程序（约 1750 行，GUI + 协议实现）
duifene_v3.spec    PyInstaller 打包配置
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
.venv\Scripts\python _dev\test_layout.py   # 界面布局（课程卡不被挤掉）
.venv\Scripts\python _dev\test_toggle.py   # 登录卡收起/展开
```

## 已知限制

- 老师**重开**签到属于全新活动（新 ID、新二维码），可以正常捕获；但已经结束的活动拿不到二维码，也补不了签——过期提交服务端会直接拒绝。
- 定位签到依赖教室配置。老师没在平台里设置教室坐标时只能退回校区默认点。
- 平台改版会导致接口失效，`HFChecktype` 之类的字段名是最容易变的部分。

## 更新记录

- **v3.7** 定位：优先用签到页自带的中心；多教室按 `HFRoomID` 选楼；「坐标管理」支持粘贴地图坐标/链接
- **v3.6** 定位落点改为教室附近 3~12 米内的确定性偏移，同一次签到固定不变
- **v3.5** 定位坐标改为直接读服务端教室坐标，自动迁移旧版错误的默认坐标
- **v3.4** 移除人工补签通道（二维码与汉字均已全自动）
- **v3.3** 修复二维码签到：识别 `G.aspx` 令牌并走 `QrCodeCheckOK.aspx?state=`，不再跟随微信 oauth
- **v3.2** 修正取图顺序（大屏图优先），避免主动刷新把令牌作废
- **v3.1** 二维码支持微信 oauth 外壳解析、日志落盘
- **v3.0** 重写：登录态持久化、四种签到自动处理、界面重构
