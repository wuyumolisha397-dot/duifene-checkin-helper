# duifene_v3.py - 对分易自动签到助手（改进版）
# 相对 duifene_v2 的主要改动：
#   1. 数字码/定位/二维码/汉字全部自动：汉字取大屏顺序，二维码取大屏图解码后走 state 入口
#   2. 修正成功判定：原版 "成功" in msg 会把「签到未成功」误判为成功
#   3. 改为成功后才标记已处理，失败按间隔重试（原版先标记，一次失败整节课不再尝试）
#   4. 已签到判据收紧：仅 CheckInStatus=1 / StatusID=1 / 出勤 才算已签，兼容服务端返回 "0"
#   5. 新增限流识别与静默、轮询加抖动、异常指数退避
#   6. 定位签到失败时解析「距离 x 米」并回报，便于校准坐标
#   7. 恢复 TLS 证书校验
import ctypes
try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
except: pass

import json, math, random, re, ssl, time, traceback, sys, platform, threading, os
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bs4 import BeautifulSoup

try:
    import urllib3; urllib3.disable_warnings()
except Exception:
    pass

HOST = "https://www.duifene.com"
# 兜底坐标：本校区教室（服务端 action=getroom 给出的同一位置，见 CI.rooms）。
# 定位签到只认教室为圆心、Range 米以内的坐标，猜别的点一定会被判「距离过远」。
DEFAULT_LNG = "114.40061188"
DEFAULT_LAT = "22.70745659"
# 历史版本预填过的默认坐标：见到就当「没校准」，否则升级后会被误当成人工校准值
DEFAULT_POINTS = ((DEFAULT_LNG, DEFAULT_LAT), ("114.39437", "22.70462"))

TYPE_NAME = {"1": "数字码", "2": "二维码", "3": "定位", "4": "汉字"}
DATE_FMT = "%Y/%m/%d %H:%M:%S"

POLL_BASE_MS = 2000       # 基础轮询间隔
POLL_JITTER_MS = 600      # 轮询抖动，避免固定节拍
MAX_AGE_MIN = 30          # 超过 N 分钟的签到视为旧活动
MAX_TRIES = 5             # 同一活动最多尝试次数
MAX_TRIES_QR = 12         # 二维码单独放宽：图要等生成/刷新，窗口又只有 9~30 秒
MAX_TRIES_LOC = 9         # 定位单独放宽：圆心不确定，要留出换候选点的次数
LOC_MAX_CANDS = 5         # 定位最多换几个候选圆心（多了既超时又容易撞限流）
LOC_TRIES_PER_CAND = 1    # 每个圆心只试 1 次就换：定位窗口只有 30 秒左右、重试又跟着 10 秒轮询走，
                          # 一个点试两次就等于把另一个候选挤没了。候选用完会循环回来，不怕漏
RETRY_GAP_S = 2           # 失败后间隔多久再试（签到窗口通常只有 10~30 秒，间隔必须短）
COOLDOWN_S = 300          # 命中限流后的静默时长
ROOM_CACHE_S = 600        # 教室坐标缓存时长（老师改教室前不用反复请求）
OK_TTL_DAYS = 30          # 「上次成功落点」的有效期，超过就不再用它当首选
LOC_NEAR_M = 12.0         # 定位签到落点：距教室中心最多这么多米（远小于老师设的容差）

# 服务端明确表示"活动已关闭"的文案——命中后说明再试也没用，直接停手。
# 补签窗口 ApplyLimitDate 只对教师端有效，学生端过了签到结束时间一律被拒。
CLOSED_WORDS = ("已结束", "没有正在进行", "签到已截止", "活动不存在", "已过期")

# ── 持久化: Windows 用注册表, 其他平台用内存 ──
if platform.system() == "Windows":
    import winreg
    REG_KEY = r"Software\duifene"
else:
    REG_KEY = None

def _reg_get(key, default=""):
    if REG_KEY is None: return default
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            return winreg.QueryValueEx(k, key)[0]
    except Exception:
        return default

def _reg_set(key, value):
    if REG_KEY is None: return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            winreg.SetValueEx(k, key, 0, winreg.REG_SZ, value)
    except Exception:
        pass

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=1, backoff_factor=0.6,
              status_forcelist=[429, 500, 502, 503], allowed_methods=["GET", "POST"]),
              pool_connections=2, pool_maxsize=4))
session.mount("http://", HTTPAdapter(max_retries=Retry(total=1, backoff_factor=0.6,
              allowed_methods=["GET", "POST"]), pool_connections=1, pool_maxsize=2))
session.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.40(0x1800282a) NetType/WIFI Language/zh_CN")

FORM_H = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
STUDENT_REFERER = HOST + "/_CheckIn/MB/CheckInStudent.aspx?moduleid=16&pasd="
WX_LINK = ("https://open.weixin.qq.com/connect/oauth2/authorize?appid=wx1b5650884f657981"
           "&redirect_uri=https://www.duifene.com/_FileManage/PdfView.aspx?file="
           "https%3A%2F%2Ffs.duifene.com%2Fres%2Fr2%2Fu6106199%2F%E5%AF%B9%E5%88%86%E6%98%93"
           "%E7%99%BB%E5%BD%95_876c9d439ca68ead389c.pdf&response_type=code"
           "&scope=snsapi_userinfo&connect_redirect=1#wechat_redirect")

CB="#f5f6fa"; CC="#ffffff"; CPRI="#3867d6"; CSUC="#20bf6b"; CDAN="#eb3b5a"
CTX="#2d3436"; CT2="#636e72"; CBO="#dfe6e9"; CLB="#1a1a2e"; CLF="#a4b0be"
CWE="#07c160"; CWA="#fdcb6e"


def _log_dir():
    """日志/取证目录：%LOCALAPPDATA%\\duifene，取不到就退回用户目录。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "duifene", "logs")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return os.path.expanduser("~")


class Log:
    def __init__(s):
        s._w = None
        s._d = True
        s._f = None
        s._path = None
        s._n = 0
        s._tid = threading.get_ident()   # 主线程 id：界面控件只能在主线程碰
    def set(s, w): s._w = w
    def file(s):
        """把日志同时落到文件，出问题可以直接翻。"""
        if s._f: return s._path
        try:
            d = _log_dir()
            s._path = os.path.join(d, "app_%s.log" % datetime.now().strftime("%Y%m%d"))
            s._f = open(s._path, "a", encoding="utf-8", buffering=1)
            s._f.write("\n" + "=" * 60 + "\n")
            s._f.write("启动 %s\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            s._f = None
        return s._path
    def _p(s, lv, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{lv}] {msg}"
        try: print(line)
        except Exception: pass
        # 落盘（DEBUG 也写，排查时最需要）
        if s._f is None: s.file()
        if s._f:
            try:
                s._f.write(line + "\n")
                s._n += 1
                if s._n % 20 == 0: s._f.flush()
            except Exception:
                pass
        if s._w and threading.get_ident() == s._tid:
            # 推送线程（WS）也会打日志，tkinter 控件只能在主线程写，子线程只落盘
            tags = {"INFO": "info", "OK": "ok", "WARN": "warn", "ERROR": "error", "DEBUG": "debug"}
            try:
                s._w.insert(tk.END, line + "\n", tags.get(lv))
                s._w.see(tk.END); s._w.update_idletasks()
            except Exception:
                pass
    def i(s, m): s._p("INFO", m)
    def ok(s, m): s._p("OK", m)
    def w(s, m): s._p("WARN", m)
    def e(s, m): s._p("ERROR", m)
    def d(s, m):
        if s._d: s._p("DEBUG", m)


def dump_resp(tag, r):
    """把一次失败响应原文存下来，便于事后对照服务端到底说了什么。"""
    try:
        d = os.path.join(_log_dir(), "dump")
        os.makedirs(d, exist_ok=True)
        fn = os.path.join(d, "%s_%s.html" % (datetime.now().strftime("%H%M%S"), tag))
        body = getattr(r, "text", "") or ""
        with open(fn, "w", encoding="utf-8", errors="replace") as f:
            f.write("URL: %s\n" % (getattr(r, "url", "") or ""))
            f.write("STATUS: %s\n" % (getattr(r, "status_code", "") or ""))
            for h in (getattr(r, "history", None) or []):
                f.write("HOP: %s %s\n" % (h.status_code, h.headers.get("Location", "")))
            f.write("-" * 40 + "\n")
            f.write(body)
        log.d("响应原文已存: %s" % fn)
        return fn
    except Exception:
        return None

log = Log()


class Limiter:
    """服务端限流时的静默器。"""
    until = 0.0

    @classmethod
    def blocked(cls):
        return time.time() < cls.until

    @classmethod
    def left(cls):
        return max(0, int(cls.until - time.time()))

    @classmethod
    def trip(cls, seconds=COOLDOWN_S):
        cls.until = time.time() + seconds

    @classmethod
    def matched(cls, text):
        return bool(text) and ("过于频繁" in text or "频繁" in text)


class LocMgr:
    """坐标管理器: 存于注册表/内存"""
    @staticmethod
    def load():
        raw = _reg_get("locations", "{}")
        try: return json.loads(raw)
        except Exception: return {}

    @staticmethod
    def save(locs):
        _reg_set("locations", json.dumps(locs, ensure_ascii=False))

    @staticmethod
    def sync(courses):
        locs = LocMgr.load(); changed = False
        for c in courses:
            name = c.get("CourseName", "")
            if not name: continue
            cur = locs.get(name)
            if not cur:
                locs[name] = {"lng": DEFAULT_LNG, "lat": DEFAULT_LAT, "note": ""}
                changed = True
            elif (LocMgr.is_default(cur.get("lng"), cur.get("lat")) and
                  (cur.get("lng"), cur.get("lat")) != (DEFAULT_LNG, DEFAULT_LAT)):
                # 老版本预填的默认坐标（离教室几百米），直接升级成现在的教室坐标
                cur["lng"], cur["lat"] = DEFAULT_LNG, DEFAULT_LAT
                locs[name] = cur; changed = True
        if changed: LocMgr.save(locs)
        return locs

    @staticmethod
    def get(name):
        locs = LocMgr.load()
        if name in locs:
            c = locs[name]
            lng = (c.get("lng") or "").strip()
            lat = (c.get("lat") or "").strip()
            if lng and lat: return lng, lat
        return "", ""

    @staticmethod
    def mark_ok(name, lng, lat, room=""):
        """记下这门课定位签到**真的被服务端接受过**的落点。

        服务端到底拿哪个点当圆心没有文档，但"这个点成功过"是硬证据，
        下次同门课直接从这个点开始试，比重新猜教室靠谱。
        """
        name = _s(name)
        lng, lat = _s(lng), _s(lat)
        if not (name and lng and lat): return
        try:
            locs = LocMgr.load()
            cur = locs.get(name) or {}
            cur["ok_lng"], cur["ok_lat"] = lng, lat
            cur["ok_room"] = _s(room)
            cur["ok_at"] = datetime.now().strftime(DATE_FMT)
            locs[name] = cur
            LocMgr.save(locs)
        except Exception as ex:
            log.d("记录成功坐标失败: %s" % ex)

    @staticmethod
    def ok_point(name):
        """上次成功落点。超过 OK_TTL_DAYS 天就当过期——换了学期/换了楼，
        老点只会白撞一次。"""
        c = LocMgr.load().get(_s(name)) or {}
        lng, lat = _s(c.get("ok_lng")), _s(c.get("ok_lat"))
        if not (lng and lat): return "", ""
        at = _s(c.get("ok_at"))
        if at:
            try:
                if (datetime.now() - datetime.strptime(at, DATE_FMT)).days > OK_TTL_DAYS:
                    return "", ""
            except Exception:
                pass
        return lng, lat

    @staticmethod
    def is_default(lng, lat):
        """这个坐标是不是「没改过的默认值」。

        sync() 会给每门课预填一份默认坐标，所以「有值」不等于「用户校准过」。
        只有明显偏离所有已知默认值，才算人工校准，才允许它压过教室坐标。
        """
        try:
            x, y = float(lng), float(lat)
        except (TypeError, ValueError):
            return True
        for dx, dy in DEFAULT_POINTS:
            if abs(x - float(dx)) < 1e-5 and abs(y - float(dy)) < 1e-5:
                return True
        return False


class Manual:
    """手动课程：课程接口取不到列表时的兜底通道。"""
    @staticmethod
    def load():
        try:
            v = json.loads(_reg_get("manual", "[]"))
            return v if isinstance(v, list) else []
        except Exception:
            return []

    @staticmethod
    def save(items):
        _reg_set("manual", json.dumps(items, ensure_ascii=False))


class Auth:
    @staticmethod
    def ok():
        try:
            h = {"Referer": HOST + "/_UserCenter/PC/CenterStudent.aspx", **FORM_H}
            r = session.post(HOST + "/AppCode/LoginInfo.ashx", data="Action=checklogin", headers=h, timeout=10)
            return r.status_code == 200 and str(r.json().get("msg")) == "1"
        except Exception:
            return False

    @staticmethod
    def pwd(u, p):
        try:
            h = {**FORM_H, "Referer": HOST + "/AppGate.aspx"}
            session.get(HOST, timeout=10)
            r = session.post(HOST + "/AppCode/LoginInfo.ashx",
                             data=f"action=loginmb&loginname={u}&password={p}", headers=h, timeout=10)
            if r.status_code == 200:
                resp = r.json()
                if resp.get("msgbox") == "登录成功":
                    CI.reset_uid()
                    if not Auth.ok():
                        log.w("服务端返回登录成功，但会话校验未通过，请先『清除登录』再重试")
                        return False
                    Auth._save(); return True
                log.e(f"登录失败: {resp.get('msgbox','')}")
            return False
        except Exception as ex:
            log.e(f"登录: {ex}"); return False

    @staticmethod
    def wx(link):
        m = re.search(r"(?<=code=)[A-Za-z0-9]{32}", link)
        if not m:
            log.e("无法提取授权码：链接里需要 code= 加 32 位字符")
            return False
        session.cookies.clear()
        CI.reset_uid()
        try:
            r = session.get(f"{HOST}/P.aspx?authtype=1&code={m.group(0)}&state=1", timeout=12)
        except Exception as ex:
            log.e(f"微信登录请求失败: {ex}"); return False
        if r.status_code != 200:
            log.e(f"微信登录失败 HTTP {r.status_code}"); return False
        body = r.text or ""
        if "LostInfo.aspx" in body:
            log.e("授权码已失效：微信 code 只能用一次，且几分钟内过期")
            log.i("请在电脑微信里重新点开授权链接，复制最新地址再登录")
            return False
        if not Auth.ok():
            log.e("登录未生效：会话校验未通过（Cookie 未拿到）")
            log.i("请重新获取微信授权链接后重试")
            return False
        Auth._save(); return True

    @staticmethod
    def _save():
        ck = "; ".join(f"{k}={v}" for k, v in session.cookies.items())
        _reg_set("cookie", ck)
        log.i("Cookie已保存")

    @staticmethod
    def load():
        ck = _reg_get("cookie", "")
        if not ck or ck == "1=1": return False
        try:
            for p in ck.split("; "):
                if "=" in p:
                    k, v = p.split("=", 1)
                    session.cookies[k] = v
            log.i("已加载本地Cookie"); return True
        except Exception:
            return False

    @staticmethod
    def logout():
        session.cookies.clear()
        _reg_set("cookie", "")
        CI.reset_uid()
        log.i("已清除本地 Cookie 和会话")


class Course:
    _cache = []

    @staticmethod
    def raw(data="action=getstudentcourse&classtypeid=2"):
        """原始调一次课程接口，返回 (响应对象, 错误串)。"""
        h = {**FORM_H, "Referer": HOST + "/_UserCenter/PC/CenterStudent.aspx",
             "X-Requested-With": "XMLHttpRequest",
             "Accept": "application/json, text/javascript, */*; q=0.01"}
        try:
            r = session.post(HOST + "/_UserCenter/CourseInfo.ashx",
                             data=data, headers=h, timeout=12)
            return r, None
        except Exception as ex:
            return None, f"{ex}"

    @staticmethod
    def list():
        """返回 (课程列表或 None, 错误说明或 None)；参数不匹配时自动换一种再试。"""
        last_err = None
        empties = 0
        for data in ("action=getstudentcourse&classtypeid=2",
                     "action=getstudentcourse&classtypeid=1",
                     "action=getstudentcourse"):
            lst, err = Course._once(data)
            if lst:
                if "classtypeid=2" not in data:
                    log.i(f"课程接口在「{data}」下才返回数据")
                return lst, None
            if lst is not None:
                empties += 1
                continue
            last_err = err
            if err and "禁止访问" in err:
                return None, err
        if empties:
            return [], None
        return None, last_err

    @staticmethod
    def _once(data):
        r, err = Course.raw(data)
        if r is None:
            return None, err or "请求失败"
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        txt = r.text or ""
        try:
            d = r.json()
        except Exception:
            return None, f"返回的不是 JSON：{txt[:100]}"
        if isinstance(d, list):
            return [_norm_course(x) for x in d if isinstance(x, dict)], None
        if isinstance(d, dict):
            for k in ["data", "list", "courses", "items"]:
                if isinstance(d.get(k), list):
                    return [_norm_course(x) for x in d[k] if isinstance(x, dict)], None
            msg = _s(d.get("msgbox")) or f"未知响应：{txt[:100]}"
            return None, msg
        return None, f"未知响应：{txt[:100]}"

    @staticmethod
    def uid():
        try:
            r = session.get(HOST + "/_UserCenter/MB/index.aspx", timeout=10)
            if r.status_code != 200: return None
            soup = BeautifulSoup(r.text, "html.parser")
            for eid in ["hidUID", "hidUid", "hidUserId", "hidSID", "studentId"]:
                e = soup.find(id=eid)
                if e and e.get("value"): return e.get("value")
            for inp in soup.find_all("input", type="hidden"):
                n = (inp.get("name") or "").lower()
                if any(x in n for x in ["uid", "userid", "studentid"]): return inp.get("value")
            return None
        except Exception:
            return None


def _s(v):
    return ("" if v is None else str(v)).strip()


def _norm_course(d):
    """把服务端课程行归一成统一字段，兼容不同大小写/别名的键名。"""
    def pick(*keys):
        for k in keys:
            v = _s(d.get(k))
            if v: return v
        return ""
    return {
        "CourseName": pick("CourseName", "coursename", "CourseTitle", "Name", "name") or "未命名",
        "TClassID": pick("TClassID", "tclassid", "Tcid", "tcid", "ClassID", "classid"),
        "CourseID": pick("CourseID", "courseid", "Cid", "cid"),
    }


def row_signed(r):
    """判断一条记录是否已签到。

    兼容两种服务端语义：CheckInStatus 为空串（旧）或 "0"/"1"（新）。
    只有明确未签的值才放行，其余非空值一律视为已签，宁可漏也不会重复提交。
    """
    st = _s(r.get("CheckInStatus"))
    if st and st.lower() not in ("0", "false", "none"):
        return True
    if _s(r.get("StatusID")) == "1":
        return True
    if _s(r.get("StatusName")) == "出勤":
        return True
    return False


def decode_qr_image(src):
    """从图片路径 / PIL 图像对象识别二维码，返回文本或 None。"""
    try:
        import zxingcpp
    except Exception:
        return None
    try:
        from PIL import Image
        img = src if hasattr(src, "convert") else Image.open(src)
        for r in (zxingcpp.read_barcodes(img) or []):
            t = _s(getattr(r, "text", ""))
            if t:
                return t
    except Exception:
        return None
    return None


def parse_qr_target(text):
    """把二维码识别出的文本换算成真正要请求的签到地址。

    对分易的二维码可能被微信 oauth 套一层，形如：
        https://open.weixin.qq.com/connect/oauth2/authorize
            ?appid=..&redirect_uri=<urlencode 的 duifene 地址>&state=1#wechat_redirect
    这里外层那个 state 是微信自己的，不是签到令牌——直接拿它去签名一定「参数错误」。
    所以先剥 redirect_uri，再在里层找 duifene 地址。

    返回 (url 或 None, 说明)。
    """
    from urllib.parse import unquote
    t = _s(text).strip()
    if not t:
        return None, "二维码内容是空的"

    # 0) 整个地址被 URL 编码了一层，先还原
    if "://" not in t and re.search(r"%3a%2f", t, re.I):
        try:
            t = unquote(t)
        except Exception:
            pass

    # 1) 剥微信 oauth 外壳（可能多层编码）
    for _ in range(3):
        m = re.search(r"[?&]redirect_uri=([^&\s#]+)", t, re.I)
        if not m:
            break
        try:
            inner = unquote(m.group(1))
        except Exception:
            break
        if "duifene.com" in inner:
            t = inner
        else:
            break

    # 2) 已经是 duifene 的完整地址，直接用
    m = re.search(r"https?://[^\s'\"<>，。；]+", t)
    if m:
        u = m.group(0)
        if "duifene.com" in u:
            # G.aspx?OnlyId=2&e=<令牌> 是「扫码入口」：它对浏览器会 302 到微信 oauth
            # （open.weixin.qq.com/...&state=<同一个令牌>）去取 openid。我们没有微信环境，
            # 跟着 302 走只会停在微信授权页，什么也签不到。
            # 但 state 入口本身不依赖 oauth——带上登录态直接请求就能签。
            # 实测：18:56:40 用这条路径一次成功，活动状态被改成「出勤」。
            mm = re.search(r"[?&]e=([0-9A-Fa-f]{16,})", u)
            if mm:
                return (f"{HOST}/_CheckIn/MB/QrCodeCheckOK.aspx?state={mm.group(1)}",
                        "G.aspx 令牌 → state 签到入口")
            return u, "二维码里的对分易地址"
        return None, f"二维码指向的不是对分易（{u[:40]}），可能不是签到的码"

    # 3) 裸令牌：拼到官方的二维码签到入口
    m = re.search(r"(?:[?&]|^)state=([^&\s'\"#]+)", t, re.I)
    token = m.group(1) if m else t
    if re.fullmatch(r"[0-9A-Za-z_\-+=/%\.]{6,}", token):
        return f"{HOST}/_CheckIn/MB/QrCodeCheckOK.aspx?state={token}", "裸令牌"
    return None, f"看不懂的二维码内容：{t[:60]}"


def loc_dist_m(lng1, lat1, lng2, lat2):
    """两点间直线距离（米），算法与官方前端 Distance() 一致。"""
    r = 6378137.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lng2) - float(lng1))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def loc_near(lng, lat, seed_key="", radius_m=LOC_NEAR_M):
    """在 (lng, lat) 附近 radius_m 米内取一个落点。

    距教室多远才给签到，是老师设的容差（Range，本项目实测 150 米），
    那是**上限**，不是要用的值：越靠圆心越稳。但每次都交同一个坐标、
    或者每次重试都换个位置，都太像脚本。所以这里只偏移几米，并且用
    seed_key 固定下来——同一次签到每次都落在同一个点，换活动才换位置。
    """
    try:
        lng = float(lng); lat = float(lat)
    except (TypeError, ValueError):
        raise
    rnd = random.Random(f"{seed_key}|{lng:.8f},{lat:.8f}")
    dist = rnd.uniform(radius_m * 0.25, radius_m)
    ang = rnd.uniform(0, 2 * math.pi)
    dlat = dist * math.cos(ang) / 111320.0
    coslat = max(math.cos(math.radians(lat)), 1e-6)   # 极地附近也有解，不至于除爆
    dlng = dist * math.sin(ang) / (111320.0 * coslat)
    return round(lng + dlng, 8), round(lat + dlat, 8)


def parse_lnglat(text):
    """从文本里取经纬度，返回 (lng, lat)，取不到返回 ("", "")。

    支持 `114.40061,22.70745`、`114.40061 22.70745`、以及高德/腾讯地图那种
    一长串链接（里面同样有两段小数）。顺序按取值范围判断，中国境内经度
    73~136、纬度 3~54，一般不会搞混。地图链接里常带国测局偏移的 gcj02 坐标，
    与老师端点击地图取值同源，直接用即可。
    """
    t = _s(text)
    if not t:
        return "", ""
    nums = re.findall(r"-?\d{1,3}\.\d{3,}", t)
    if len(nums) < 2:
        return "", ""
    a, b = float(nums[0]), float(nums[1])
    lng_ok = 73.0 <= a <= 136.0
    lat_ok = 3.0 <= a <= 54.0
    lng_ok2 = 73.0 <= b <= 136.0
    lat_ok2 = 3.0 <= b <= 54.0
    if lng_ok and lat_ok2:
        return f"{a:.8f}", f"{b:.8f}"
    if lat_ok and lng_ok2:
        return f"{b:.8f}", f"{a:.8f}"
    return "", ""


class CI:
    done = set()
    _uid = None
    _rooms = {}      # course_id -> (教室坐标列表, 取回时间)，避免每轮都问服务端

    @classmethod
    def reset(cls):
        cls.done.clear()
        cls._rooms.clear()

    @classmethod
    def reset_uid(cls):
        cls._uid = None

    @classmethod
    def mark_done(cls, rid):
        if rid: cls.done.add(str(rid))

    @classmethod
    def uid(cls):
        if not cls._uid:
            cls._uid = Course.uid()
        return cls._uid

    @classmethod
    def _json(cls, r):
        """安全解析 {msg, msgbox} 结构。"""
        try:
            return r.json()
        except Exception:
            return None

    @classmethod
    def _verdict(cls, r):
        """把服务端返回归一成 (是否成功, 文案)。"""
        if r is None:
            return False, "无响应"
        body = r.text or ""
        if Limiter.matched(body):
            Limiter.trip()
            return False, f"服务端限流，静默 {COOLDOWN_S} 秒"
        j = cls._json(r)
        if j is None:
            return False, f"响应异常 HTTP {r.status_code}"
        box = _s(j.get("msgbox"))
        code = j.get("msg")
        try:
            code = int(code)
        except Exception:
            code = None
        if code == -2:
            return True, box or "已签到"
        bad = ("未成功" in box) or ("失败" in box) or ("错误" in box)
        if code is not None and code > 0 and not bad:
            return True, box or "签到成功"
        return False, box or f"HTTP {r.status_code}"

    @classmethod
    def fetch(cls, class_id):
        """拉取当日签到活动列表。"""
        uid = cls.uid()
        if not uid:
            log.e("无法获取学生ID")
            return None
        h = {**FORM_H, "X-Requested-With": "XMLHttpRequest",
             "Referer": f"{HOST}/_CheckIn/PC/StudentNoCheckCount.aspx",
             "Accept": "application/json, text/javascript, */*; q=0.01"}
        data = f"action=getstudentinlogbyday&classid={class_id}&studentid={uid}"
        try:
            r = session.post(HOST + "/_CheckIn/MBCount.ashx", data=data, headers=h, timeout=10)
            if r.status_code != 200: return None
            resp = r.json()
            if str(resp.get("msg")) != "1": return None
            rows = resp.get("rows") or []
            return rows
        except Exception as ex:
            log.e(f"获取签到: {ex}"); return None

    @classmethod
    def pend(cls, rows, tried):
        """挑出一条待签到的活动；tried 记录尝试次数与下次重试时间。"""
        if not rows: return None
        now = datetime.now(); ts = time.time()
        for r in rows:
            rid = _s(r.get("ID"))
            if not rid or rid in cls.done: continue
            if row_signed(r):
                cls.mark_done(rid); continue
            if _s(r.get("CanApply")) != "1": continue
            limit = _s(r.get("ApplyLimitDate"))
            if limit:
                try:
                    if now > datetime.strptime(limit, DATE_FMT): continue
                except Exception:
                    pass
            created = _s(r.get("CreaterDate"))
            if created:
                try:
                    if (now - datetime.strptime(created, DATE_FMT)).total_seconds() > MAX_AGE_MIN * 60:
                        continue
                except Exception:
                    pass
            info = tried.get(rid) or {}
            ct = _s(r.get("CheckInType"))
            cap = MAX_TRIES_QR if ct == "2" else (MAX_TRIES_LOC if ct == "3" else MAX_TRIES)
            if info.get("count", 0) >= cap: continue
            if info.get("next", 0) > ts: continue
            return r
        return None

    # ── 各类签到 ──
    @classmethod
    def code(cls, uid, code):
        """数字码 / 汉字：同一接口，checkincode 传 4 位数字或 4 个汉字。"""
        try:
            h = {**FORM_H, "Referer": STUDENT_REFERER}
            r = session.post(HOST + "/_CheckIn/CheckIn.ashx",
                             data=f"action=studentcheckin&studentid={uid}&checkincode={code}",
                             headers=h, timeout=12)
            if r.status_code != 200:
                return False, f"签到请求失败 HTTP {r.status_code}"
            return cls._verdict(r)
        except Exception as ex:
            return False, f"签到异常: {ex}"

    @classmethod
    def qr_link(cls, text):
        """按二维码里的令牌完成签到。

        二维码的真实内容是（实测取样）：
            https://www.duifene.com/G.aspx?OnlyId=2&e=<48位令牌>

        坑在这里：**不能直接请求 G.aspx**。它对学生浏览器会 302 到
            open.weixin.qq.com/connect/oauth2/authorize?appid=wx1b56...&state=<同一个令牌>
        目的是先拿微信 openid。我们没有微信环境，跟过去只会停在授权页（1033 字节、
        页面无文案），于是被判成「失败」，之前所有二维码失败都是这么来的。

        正确做法：令牌本身（也就是微信那层 302 里带的 state）直接交给
            /_CheckIn/MB/QrCodeCheckOK.aspx?state=<令牌>
        带上已有登录态即可完成签到，不需要 oauth。实测 18:56:40 一次成功，
        服务端把活动状态从「缺勤」改成「出勤」，重放则回「您已经签到成功！」。

        服务器会用 DivOK 里的文案给结论：
            签到成功！/您已经签到成功！   → 成功
            无法解读此签到二维码          → 令牌已换新（t=jx）
            此签到二维码已过期            → 令牌过期（t=gq）
            此二维码不是签到二维码        → 不是签到码（t=ce）
            参数错误,请重新尝试           → 没带 state
        """
        url, why = parse_qr_target(text)
        if not url:
            return False, why
        log.d(f"二维码目标: {url[:120]}")
        try:
            r = session.get(url, timeout=15, allow_redirects=True,
                            headers={"Referer": STUDENT_REFERER})
            if r.status_code != 200:
                return False, f"二维码请求失败 HTTP {r.status_code}"
            body = r.text or ""
            hops = " → ".join("%s %s" % (h.status_code, (h.headers.get("Location") or "")[:70])
                              for h in (getattr(r, "history", None) or []))
            log.d("二维码跳转: %s" % (hops or "无 302"))
            if Limiter.matched(body):
                Limiter.trip()
                return False, f"服务端限流，静默 {COOLDOWN_S} 秒"
            code = ""
            m = re.search(r"[?&]t=([A-Za-z0-9]+)", getattr(r, "url", "") or "")
            if m:
                code = m.group(1)
            m2 = re.search(r'id="DivOK"[^>]*>(.*?)</div>\s*</div>', body, re.S)
            raw = re.sub(r"<[^>]+>", " ", m2.group(1)) if m2 else ""
            txt = " ".join((raw or BeautifulSoup(body, "html.parser")
                            .get_text(" ", strip=True)).split())
            if "成功" in txt and "未成功" not in txt:
                if "已经" in txt or "已签到" in txt:
                    return True, "二维码签到成功（此前已签到）"
                return True, "二维码签到成功"
            if "无法解读" in txt:
                dump_resp("qr_jx", r)
                return False, "二维码无法解读（令牌已换新，需重取）"
            if "过期" in txt or code == "gq":
                dump_resp("qr_gq", r)
                return False, "二维码已被刷新（令牌一次性，2~3 秒就换），要赶上最新那一张"
            if "不是签到二维码" in txt or code == "ce":
                dump_resp("qr_ce", r)
                return False, "这个码不是签到码"
            if "参数错误" in txt:
                dump_resp("qr_bad", r)
                return False, "二维码参数错误（链接不完整）"
            return False, f"二维码未成功: {txt[:80] or code or '空响应'}"
        except Exception as ex:
            return False, f"二维码异常: {ex}"

    @classmethod
    def loc(cls, uid, lng, lat, cid, tcid, seed_key=""):
        try:
            origin = (float(lng), float(lat))
            lng, lat = loc_near(lng, lat, seed_key)
        except (TypeError, ValueError):
            return False, "坐标格式无效"
        log.d(f"定位提交: {lng},{lat}（距本次圆心 {loc_dist_m(origin[0], origin[1], lng, lat):.0f} 米）")
        orig_ua = session.headers.get("User-Agent", "")
        try:
            session.headers["User-Agent"] = MOBILE_UA
            h = {**FORM_H, "Referer": STUDENT_REFERER, "X-Requested-With": "XMLHttpRequest"}
            parts = ["action=signin"]
            if cid: parts.append(f"cid={cid}")
            if tcid: parts.append(f"tcid={tcid}")
            parts += [f"sid={uid}", f"latitude={lat}", f"longitude={lng}"]
            data = "&".join(parts)
            r = session.post(HOST + "/_CheckIn/CheckInRoomHandler.ashx", data=data, headers=h, timeout=15)
        except Exception as ex:
            return False, f"签到异常: {ex}"
        finally:
            session.headers["User-Agent"] = orig_ua
        if r.status_code != 200:
            return False, f"签到请求失败 HTTP {r.status_code}"
        ok, msg = cls._verdict(r)
        if not ok:
            # 圆心猜错时服务端只回一句「距离教室较远」，不带距离也不带期望坐标，
            # 所以把原文整段存下来——下次对不上时至少能看清它到底说了什么。
            dump_resp("loc_fail", r)
            m = re.search(r"距(?:离)?[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*米", r.text or "")
            if m:
                msg = f"{msg}（距目标约 {m.group(1)} 米，建议校准坐标）"
        return ok, msg

    @classmethod
    def rooms(cls, course_id):
        """课程绑定的教室坐标，学生号同样能读到。

        定位签到的判定是「以教室为圆心、Range 米以内」，而官方前端是用微信
        JSAPI 拿真实 GPS 再提交的。我们拿不到 GPS，就直接问服务端要老师的
        教室坐标——比随便猜一个点靠谱得多，也不依赖「上次成功过」。
        """
        cid = _s(course_id)
        if not cid: return [], "没有课程 ID"
        hit = cls._rooms.get(cid)
        if hit and time.time() - hit[1] < ROOM_CACHE_S:
            return hit[0], None
        h = {**FORM_H, "X-Requested-With": "XMLHttpRequest",
             "Referer": f"{HOST}/_CheckIn/PC/StudentNoCheckCount.aspx",
             "Accept": "application/json, text/javascript, */*; q=0.01"}
        try:
            r = session.post(HOST + "/_CheckIn/CheckInRoomHandler.ashx",
                             data=f"action=getroom&cid={cid}", headers=h, timeout=12)
            if r.status_code != 200:
                return [], f"取教室坐标 HTTP {r.status_code}"
            j = r.json()
            if str(j.get("msg")) != "1":
                return [], _s(j.get("msgbox")) or "取教室坐标失败"
            out = []
            for x in (j.get("data") or []):
                lng, lat = _s(x.get("Longitude")), _s(x.get("Latitude"))
                if lng and lat:
                    out.append({"roomid": _s(x.get("RoomID")),
                                "name": _s(x.get("RoomName")),
                                "lng": lng, "lat": lat,
                                "range": _s(x.get("Range"))})
            cls._rooms[cid] = (out, time.time())
            return out, None if out else "课程没有设置教室位置"
        except Exception as ex:
            return [], f"取教室坐标异常: {ex}"

    @classmethod
    def pick_room(cls, course_id, room_id_hint=""):
        """选本次签到的圆心教室。

        一门课可能挂多间教室（换楼上课就是这个情况）。老师发起定位签到时，
        教师页的 HFRoomID 会指向**本次**绑定的那间，所以优先按它匹配；
        匹配不上（或本来就只有一间）才退回列表第一个。
        """
        rooms, why = cls.rooms(course_id)
        if not rooms:
            return None, why
        want = _s(room_id_hint)
        if want and want != "0":
            for r in rooms:
                if r["roomid"] == want:
                    return r, None
            log.w(f"本次绑定教室 {want} 不在课程教室列表里（现有 "
                  + ", ".join(f"{r['roomid']}/{r.get('name') or '?'}" for r in rooms[:4])
                  + "），只能用第一间")
        return rooms[0], None

    @classmethod
    def board(cls, course_id):
        """读教师端「进行中签到」页上的大屏信息——学生号同样能读到。

        对应 /_CheckIn/PC/TeachCheckIn.aspx 的三个隐藏域（页面 JS 里就是拿它们
        渲染大屏的：`checkCodeKey = $(\"#HFCheckCodeKey\").val()`）：

          HFChecktype       1 数字码 / 2 二维码 / 3 定位 / 4 汉字
          HFCheckCodeKey    type=1 → 4 位数字
                            type=2 → **二维码图片本身的 base64 JPG**
                            type=4 → **按正确顺序排列的 4 个汉字**
          HFCheckCodePinyin type=4 → 逐字拼音，逗号分隔

        没有进行中的活动时 type 为 "0"、key 为空。返回 None 表示取不到。
        """
        if not course_id: return None
        h = {**FORM_H, "Referer": f"{HOST}/_CheckIn/PC/TeachCheckInList.aspx"}
        data = f"courseid={course_id}&moduleid=7&classtype=1"

        def fetch(hdr):
            try:
                r = session.post(HOST + "/_CheckIn/PC/TeachCheckIn.aspx",
                                 data=data, headers=hdr, timeout=12)
                if r.status_code != 200: return None
                soup = BeautifulSoup(r.text or "", "html.parser")

                def val(i):
                    e = soup.find(id=i)
                    return _s(e.get("value")) if e is not None else ""

                t = val("HFChecktype")
                if not t or t == "0": return None
                return {"type": t, "key": val("HFCheckCodeKey"),
                        "pinyin": val("HFCheckCodePinyin"), "ciid": val("HFCheckInID"),
                        "roomid": val("HFRoomID"),
                        "refresh": val("HFIsRefresh"), "interval": val("HFTimeInterval")}
            except Exception:
                return None

        b = fetch(h)
        if b: return b
        # 兜底：换微信 UA 再问一次（教师页按 UA 可能有不同渲染）
        return fetch({**h, "User-Agent": MOBILE_UA})

    @classmethod
    def hanzi_answer(cls, course_id):
        b = cls.board(course_id)
        if not b or b.get("type") != "4": return None
        a = _s(b.get("key"))
        return a if len(a) == 4 else None

    @classmethod
    def qr_image_text(cls, b64):
        """教师页下发的二维码图片（base64 JPG）→ 识别出的文本。"""
        b64 = _s(b64)
        if len(b64) < 100: return None
        try:
            import base64, io
            from PIL import Image
            raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
            img = Image.open(io.BytesIO(raw))
        except Exception:
            return None
        text = decode_qr_image(img)
        if img is not None:
            cls._save_qr_sample(img, b64)
        return text

    @staticmethod
    def _save_qr_sample(img, b64):
        """把抓到的二维码留一份，出问题时能直接看是哪张图。"""
        try:
            import os, tempfile, datetime as _dt
            d = os.path.join(tempfile.gettempdir(), "duifene_qr")
            os.makedirs(d, exist_ok=True)
            ts = _dt.datetime.now().strftime("%m%d_%H%M%S")
            p = os.path.join(d, f"qr_{ts}.png")
            img.convert("RGB").save(p)
            log.d(f"二维码图片已存档: {p}")
        except Exception:
            pass

    @classmethod
    def qr_latest_image(cls, course_id):
        """官方刷新二维码用的接口：POST CheckIn.ashx action=getcodeimage&cid=<课程ID>。

        教师页里的 base64 只是「发起那一刻」的图；对分易的二维码会按 HFTimeInterval
        定时换新（checkincode.js 里 IsRefreshCode → GetCheckInCodeImage），
        换过之后旧图里的令牌立刻作废。所以要签就取这张最新的。
        """
        if not course_id: return None, "没有课程 ID"
        h = {**FORM_H, "X-Requested-With": "XMLHttpRequest",
             "Referer": f"{HOST}/_CheckIn/PC/TeachCheckIn.aspx",
             "Accept": "application/json, text/javascript, */*; q=0.01"}
        try:
            r = session.post(HOST + "/_CheckIn/CheckIn.ashx",
                             data=f"action=getcodeimage&cid={course_id}",
                             headers=h, timeout=12)
            if r.status_code != 200:
                return None, f"取二维码 HTTP {r.status_code}"
            j = r.json()
            if str(j.get("msg")) != "1":
                return None, _s(j.get("msgbox")) or "取二维码失败"
            data = _s(j.get("data"))
            if len(data) < 100:
                return None, "接口没返回图片数据"
            return data, None
        except Exception as ex:
            return None, f"取二维码异常: {ex}"

    @classmethod
    def qr_fast(cls, course_id):
        """二维码签到：先用最快的取图接口，失败再回退到教师页那张。

        令牌是短命的（实测每 2~3 秒换一张，旧的立刻作废），所以耗时最要紧：
          · getcodeimage  约 120ms，返回当前最新一张 → 首选
          · TeachCheckIn  约 650ms、34KB，页面里那张可能是旧图 → 兜底
        每次尝试都记下取图/解码/提交三段耗时，日志里一眼能看出慢在哪。

        返回 (True/False, msg) 表示已尝试过签到；(None, why) 表示当前不是二维码。
        """
        tried = []
        # ① 最快通道：直接问最新图，取到就立刻提交
        t0 = time.time()
        b64, why = cls.qr_latest_image(course_id)
        if b64:
            log.d("二维码取图(getcodeimage) %.0fms" % ((time.time() - t0) * 1000))
            text = cls.qr_image_text(b64)
            if text:
                log.d("二维码内容: %s" % text[:160])
                ok, msg = cls.qr_link(text)
                if ok:
                    return True, msg
                tried.append(msg)
            else:
                tried.append("二维码图片识别失败")
        else:
            tried.append(why or "取二维码失败")
            log.d("二维码取图失败: %s" % (why or "未知"))

        # ② 兜底：教师页里那张（不触发轮换，窗口内长期有效的那种）
        t0 = time.time()
        b = cls.board(course_id)
        if not b:
            # 教师页说没有进行中的活动：无论刚才取图报的是什么，都按「没活动」处理
            return None, (tried[0] if tried else "教师页上没有进行中的签到")
        if b.get("type") != "2":
            name = TYPE_NAME.get(b.get("type"), b.get("type"))
            return None, f"大屏当前是【{name}】签到，不是二维码"
        log.d("二维码取图(大屏) %.0fms" % ((time.time() - t0) * 1000))
        key = _s(b.get("key"))
        if len(key) > 100:
            text = cls.qr_image_text(key)
            if text:
                log.d("大屏二维码内容: %s" % text[:160])
                ok, msg = cls.qr_link(text)
                if ok:
                    return True, msg
                tried.append(msg)
            else:
                tried.append("大屏二维码图片识别失败")
        return False, "；".join(tried[:2])



# ── 服务端消息推送（SignalR）──────────────────────────────────────────────
# 别人签到成功时，服务端会把他的 经度/纬度/距教室米数 广播给全站客户端。
# 这是唯一能拿到「真圆心附近坐标」的免费途径：不用猜、不用三角测量、零额外请求。
WS_HUB = "https://wsdf.duifene.com/messageHub"
PEER_MAX_DIST_M = 150.0   # 广播里 Distance 超过它就不抄（那是别人瞎试的探测点）
PEER_TTL_S = 180          # 抄来的坐标保留多久
PEER_KEEP = 3             # 一次签到最多试几个同学的坐标
WS_RS = "\x1e"            # SignalR 记录分隔符


class Peer:
    """别人签到时广播出来的真实坐标，按「距教室近」排队。"""

    _pts = []
    _lock = threading.Lock()

    @classmethod
    def add(cls, lng, lat, dist):
        try:
            lng, lat, dist = float(lng), float(lat), float(dist)
        except (TypeError, ValueError):
            return False
        if not (73.0 <= lng <= 136.0 and 3.0 <= lat <= 54.0):
            return False                      # 不是国内经纬度，多半是脏数据
        if dist > PEER_MAX_DIST_M:
            log.d(f"忽略广播坐标 {lng:.6f},{lat:.6f}（距教室 {dist:.0f} 米，太远）")
            return False
        now = time.time()
        with cls._lock:
            cls._pts = [p for p in cls._pts if now - p[3] < PEER_TTL_S]
            for i, p in enumerate(cls._pts):
                if abs(p[0] - lng) < 1e-6 and abs(p[1] - lat) < 1e-6:
                    cls._pts[i] = (lng, lat, dist, now)
                    break
            else:
                cls._pts.append((lng, lat, dist, now))
            cls._pts.sort(key=lambda p: p[2])   # 距教室越近越靠前
        log.i(f"收到同学坐标 {lng:.6f},{lat:.6f}（距教室 {dist:.0f} 米，签到会优先用它）")
        return True

    @classmethod
    def best(cls, n=PEER_KEEP):
        now = time.time()
        with cls._lock:
            return [(p[0], p[1]) for p in cls._pts if now - p[3] < PEER_TTL_S][:n]

    @classmethod
    def clear(cls):
        with cls._lock:
            cls._pts = []


class Hub:
    """messageHub 的最小实现：连上、注册、收推送，断线自动重连。"""

    def __init__(s):
        s._ws = None
        s._stop = threading.Event()
        s._t = None
        s._ok = False

    def running(s):
        return s._ok

    def start(s, key_id=""):
        if s._t and s._t.is_alive():
            return
        s._stop.clear()
        s._t = threading.Thread(target=s._run, args=(_s(key_id),), daemon=True)
        s._t.daemon = True
        s._t.start()

    def stop(s):
        s._stop.set()
        s._ok = False
        try:
            if s._ws:
                s._ws.send(json.dumps({"type": 7}) + WS_RS)   # 优雅关闭
        except Exception:
            pass
        try:
            if s._ws:
                s._ws.close()
        except Exception:
            pass
        s._ws = None

    def _send(s, obj):
        if s._ws is None:
            raise RuntimeError("WebSocket 未连接")
        s._ws.send(json.dumps(obj, separators=(",", ":")) + WS_RS)

    def _run(s, key_id):
        try:
            import websocket
        except Exception as ex:
            log.w(f"缺少 websocket-client，跟随同学坐标不可用（{ex}）")
            return
        fails = 0
        while not s._stop.is_set():
            try:
                r = session.post(WS_HUB + "/negotiate?negotiateVersion=1",
                                 headers={"Origin": HOST, "Referer": HOST + "/"}, timeout=10)
                token = (r.json() or {}).get("connectionToken")
                if not token:
                    raise RuntimeError(f"negotiate 没有 token（HTTP {r.status_code}）")
                s._ws = websocket.create_connection(
                    WS_HUB.replace("https://", "wss://") + "?id=" + token,
                    header=[f"Origin: {HOST}",
                            f"User-Agent: {session.headers.get('User-Agent', '')}"],
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    timeout=20)
                s._send({"protocol": "json", "version": 1})
                s._ws.settimeout(10)
                s._ws.recv()                                    # 握手回执 "{}"
                if key_id:
                    s._send({"type": 1, "invocationId": "1",
                             "target": "connect", "arguments": [key_id]})
                s._ok = True
                fails = 0
                log.i("消息推送已连接（同学签到成功后会广播坐标，可直接复用）")
                s._read()
            except Exception as ex:
                s._ok = False
                if s._stop.is_set():
                    return
                fails += 1
                # 实测：新进程第一次握手几乎必回 404（服务端还没把连接登记上），
                # 隔 1 秒重新 negotiate 就成功。所以前两次固定等 1 秒，别一上来
                # 就退避到 8 秒——定位签到的窗口只有几十秒，等不起。
                wait = 1.0 if fails <= 2 else min(2 ** (fails - 2), 30)
                log.d(f"消息推送断开: {ex}（{wait:.0f} 秒后重连）")
                s._stop.wait(wait)
            finally:
                try:
                    if s._ws:
                        s._ws.close()
                except Exception:
                    pass
                s._ws = None

    def _read(s):
        s._ws.settimeout(15)
        while not s._stop.is_set():
            try:
                raw = s._ws.recv()
            except Exception as ex:
                if "timed out" in str(ex).lower() or "timeout" in str(ex).lower():
                    s._send({"type": 6})                        # 心跳
                    continue
                raise
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            for part in raw.split(WS_RS):
                part = part.strip()
                if part:
                    s._dispatch(part)

    def _dispatch(s, part):
        try:
            msg = json.loads(part)
        except Exception:
            return
        t = msg.get("type")
        if t == 7:
            raise RuntimeError("服务端关闭连接")
        if t != 1:
            return
        args = msg.get("arguments") or []
        if not args:
            return
        payload = args[0]
        try:
            d = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except Exception:
            return
        uid = _s(d.get("UserID"))
        lng, lat = d.get("Longitude"), d.get("Latitude")
        if not (lng and lat):
            return
        if uid and uid == _s(CI.uid()):
            return                                              # 自己签的，不用抄
        try:
            dist = float(d.get("Distance") or 0)
        except (TypeError, ValueError):
            dist = 0.0
        Peer.add(lng, lat, dist)


hub = Hub()


def loc_candidates(cid, cname, info, board=None, limit=LOC_MAX_CANDS):
    """定位签到的候选圆心，按可信度排序。返回 [(标签, 经度, 纬度, 教室ID)]。

    服务端拿哪个坐标当圆心是黑盒——教室表里的点、老师发起签到时的手机定位、
    另一间教室，都可能。以前只压一个点，猜错就整场失败（「距离教室较远」）。
    现在排成一列候选，失败就换下一个，而不是对着同一个点撞满重试次数。
    """
    info = info or {}
    board = board or {}
    out, seen = [], set()

    def add(tag, lng, lat, room=""):
        lng, lat = _s(lng), _s(lat)
        if not (lng and lat): return
        try:
            key = (round(float(lng), 6), round(float(lat), 6))
        except ValueError:
            return
        if key in seen: return
        seen.add(key)
        out.append((tag, lng, lat, room))

    # 0 同学坐标：刚有同学签成功，服务端把他的点广播出来了，直接抄最稳
    for plng, plat in Peer.best():
        add("同学坐标", plng, plat)

    # 1 活动记录 / 教师页 hidden 域里带的中心：服务端自己记的点，最权威
    add("活动坐标", info.get("Longitude"), info.get("Latitude"))
    kl, ka = parse_lnglat(_s(board.get("key")))
    add("签到页坐标", kl, ka)

    # 2 人工校准：用户明确填过的点
    lng, lat = LocMgr.get(cname)
    if lng and lat and not LocMgr.is_default(lng, lat):
        add("人工校准", lng, lat)

    # 3 教室坐标：HFRoomID 指的那间排前面，其余按课程里的顺序接着试。
    #   排在「上次成功点」前面有实测依据：21:37 那次用上次成功点当圆心，程序算距它
    #   8 米，老师端却显示 36 米；而提交点距教室 e3 正好 34.1 米 —— 说明服务端比的
    #   就是教室表这间。圆心贴着它取点，服务端算出来就是 5~12 米，又稳又不扎眼。
    rooms, why = (CI.rooms(cid) if cid else ([], "没有课程"))
    hint = _s(board.get("roomid"))
    for r in sorted(rooms, key=lambda x: 0 if _s(x.get("roomid")) == hint else 1):
        add("教室 %s" % (r.get("name") or r.get("roomid") or "?"),
            r["lng"], r["lat"], _s(r.get("roomid")))
    if not rooms and why:
        log.d("取教室坐标失败: %s" % why)
    if hint and rooms and all(_s(r.get("roomid")) != hint for r in rooms):
        # 签到活动绑的是哪间教室，只能从教师页 HFRoomID 看。对不上说明这份
        # 教室表和老师建活动时用的不是一套（换了账号、教室被删、跨校区导入），
        # 那就别信「就近那间」，全部当候选逐间试。
        log.w(f"活动绑定的教室(HFRoomID={hint}) 不在你的教室列表里，逐间试")

    # 4 本课程上次定位签到成功过的点：被服务端认过，但它只保证「在容差内」，
    #   不代表靠近圆心（可能就是擦着 150 米边过的）。所以放在教室后面当备胎
    ol, oa = LocMgr.ok_point(cname)
    add("上次成功点", ol, oa)

    # 5 兜底：只有一条候选都排不出来时才用。写死的默认点是「本机这个账号常用的
    #   那个位置」，换个人、换门课就毫无意义，硬塞进列表只会白烧一次重试
    if not out:
        add("兜底坐标", DEFAULT_LNG, DEFAULT_LAT)
    return out[:limit]


class Loop:
    def __init__(s):
        s.r = False; s.ci = ""; s.cn = ""; s._rt = None; s._cid = ""
        s._delay = 0; s._pending = None; s._tick = 0
        s._bc = None             # 教师页缓存 (cid, ts, dict)
        s._last_loc = None       # 本次定位实际提交的 (lng, lat, room)，成功后要回写
        s._tried = {}; s._poll_ms = POLL_BASE_MS; s._err = 0
        s._loc_logged = set()   # 教室坐标只打印一次，别每轮重试都刷屏
        s._loc_i_shown = {}     # 每个活动当前用第几个候选，用于打印「换圆心」

    def start(s, class_id, course_name, root_widget, course_id="", delay=0,
              poll_ms=POLL_BASE_MS):
        s.r = True; s.ci = class_id; s.cn = course_name; s._rt = root_widget
        s._cid = course_id; s._delay = delay; s._poll_ms = poll_ms
        s._pending = None; s._tick = 0; s._tried = {}; s._err = 0
        s._loc_logged = set(); s._loc_i_shown = {}
        CI.reset(); CI.reset_uid()
        log.i(f"监听【{course_name}】签到, 延迟{delay}秒, 轮询{max(1, poll_ms // 1000)}秒")
        hub.start(CI.uid() or "")
        s._poll()

    def stop(s):
        s.r = False; log.i("已停止")
        hub.stop()

    def _next(s):
        if s.r and s._rt:
            delay = s._poll_ms + random.randint(0, POLL_JITTER_MS)
            if s._err:
                delay = min(s._poll_ms * (2 ** min(s._err, 4)), 30000)
            s._rt.after(delay, s._poll)

    def _poll(s):
        if not s.r: return
        try:
            if Limiter.blocked():
                log.d(f"限流静默中，剩余 {Limiter.left()} 秒")
            elif not Auth.ok():
                log.w("登录失效，请重新登录"); s.r = False
                return
            else:
                s._tick += 1
                rows = CI.fetch(s.ci)
                if rows is None:
                    s._err += 1
                    if s._tick % 10 == 1: log.i("持续监控中...")
                else:
                    s._err = 0
                    p = CI.pend(rows, s._tried)
                    if p is None:
                        if s._tick % 10 == 1: log.i("持续监控中...")
                    else:
                        s._dispatch(p)
        except Exception as ex:
            s._err += 1
            log.e(f"监控异常: {ex}"); log.d(traceback.format_exc())
        s._next()

    def _dispatch(s, p):
        rid = _s(p.get("ID")); ct = _s(p.get("CheckInType"))
        log.i(f"发现待签到! ID:{rid} 类型:{TYPE_NAME.get(ct, ct or '?')}"
              + (f" 码:{_s(p.get('CheckInCode'))}" if _s(p.get("CheckInCode")) else ""))
        if s._delay > 0 and not s._pending:
            s._pending = (ct, p, rid)
            log.i(f"将在 {s._delay} 秒后自动签到...")
            if s._rt: s._rt.after(s._delay * 1000, s._do_pending)
        else:
            s._exec(ct, p, rid)

    def _do_pending(s):
        if not s._pending: return
        ct, p, rid = s._pending; s._pending = None
        s._exec(ct, p, rid)

    def _exec(s, ct, p, rid):
        info = s._tried.setdefault(rid, {"count": 0, "next": 0})
        ct = _s(ct)
        limit = MAX_TRIES_QR if ct == "2" else (MAX_TRIES_LOC if ct == "3" else MAX_TRIES)
        info["count"] += 1
        ok, msg = s._do(ct, p, info)

        if ok:
            CI.mark_done(rid)
            if ct == "3" and s._last_loc:
                # 这个点服务端认了，存下来给同门课下次直接复用
                LocMgr.mark_ok(s.cn, *s._last_loc)
                s._last_loc = None
            log.ok(msg)
        elif any(w in (msg or "") for w in CLOSED_WORDS):
            # 服务端已判定活动关闭：补签窗口（ApplyLimitDate）是教师端的，
            # 学生端再怎么重试也只会被拒，继续打接口纯属给自己招限流。
            CI.mark_done(rid)
            log.w(f"{msg}（活动已关闭，本活动不再尝试；需补签请联系任课教师）")
        else:
            info["next"] = time.time() + RETRY_GAP_S
            left = limit - info["count"]
            if "限流" in msg:
                log.w(msg)
            elif left > 0:
                log.e(f"{msg}（{RETRY_GAP_S} 秒后重试，剩 {left} 次）")
            else:
                log.e(f"{msg}（已达上限 {limit} 次，本活动不再尝试）")

    def _board_cached(s, rid="", ttl=8):
        """教师页按「课程+活动」缓存 8 秒，换圆心时不用每轮都重新拉。"""
        now = time.time()
        key = (s._cid, _s(rid))
        if s._bc and s._bc[0] == key and now - s._bc[1] < ttl:
            return s._bc[2]
        b = (CI.board(s._cid) if s._cid else None) or {}
        s._bc = (key, now, b)
        return b

    def _loc_cands(s, info):
        """本次活动的候选圆心（顺序见 loc_candidates）"""
        return loc_candidates(s._cid, s.cn, info, s._board_cached(info.get("ID")))

    def _do(s, ct, info, st=None):
        uid = CI.uid()
        if not uid: return False, "无法获取用户ID"
        ct = _s(ct)
        if ct == "1":
            code = _s(info.get("CheckInCode"))
            if not code:
                b = CI.board(s._cid)
                if b and b.get("type") == "1":
                    k = _s(b.get("key"))
                    if len(k) == 4: code = k; log.d(f"大屏数字码: {code}")
            if not code: return False, "服务端与教师页都没给出签到码"
            return CI.code(uid, code)
        if ct == "3":
            # 圆心是黑盒，候选列表逐个试。以前把「换到第几个」记在页面 dict 上，
            # 而每次轮询都会重建那份 dict——于是换点永远从 0 开始，9 次重试全砸在
            # 同一个点上（日志里候选明明有 2 个，却从没出现「换圆心」就是这原因）。
            # 现在按 s._tried 里的真实重试次数推进，每个候选试 LOC_TRIES_PER_CAND 次。
            cands = s._loc_cands(info)
            if not cands:
                return False, "没有任何可用坐标（活动、教室、人工校准都没有）"
            n = max(1, int((st or {}).get("count") or 1))
            i = ((n - 1) // max(1, LOC_TRIES_PER_CAND)) % len(cands)
            tag, lng, lat, room = cands[i]
            rid = _s(info.get("ID"))
            if rid not in s._loc_logged:
                s._loc_logged.add(rid)
                lst = "；".join("%d.%s %s,%s" % (k + 1, c[0], c[1], c[2])
                                for k, c in enumerate(cands))
                log.i(f"圆心候选 {len(cands)} 个: {lst}")
                log.i(f"先用 {tag} {lng},{lat}（每个试 {LOC_TRIES_PER_CAND} 次，不行自动换）")
            elif s._loc_i_shown.get(rid, -1) != i:
                log.w(f"换圆心 {i + 1}/{len(cands)}: {tag} {lng},{lat}")
            s._loc_i_shown[rid] = i
            s._last_loc = (lng, lat, room)
            return CI.loc(uid, lng, lat, s._cid, s.ci, rid or s.cn)
        if ct == "4":
            ans = _s(info.get("CheckInCode"))
            if len(ans) != 4:
                b = CI.board(s._cid)
                if b and b.get("type") == "4":
                    k = _s(b.get("key"))
                    if len(k) == 4:
                        ans = k
                        py = _s(b.get("pinyin")).replace(",", " ")
                        log.d(f"大屏汉字顺序: {ans}" + (f"（{py}）" if py else ""))
            if len(ans) != 4: return False, "教师页没给出汉字顺序"
            return CI.code(uid, ans)
        if ct == "2":
            done, msg = CI.qr_fast(s._cid)
            if done is not None:
                return done, msg
            # None：教师页上没有进行中的活动，或大屏类型已经不是二维码
            return False, msg or "教师页上没有进行中的签到"
        return False, f"未知签到类型: {ct}"


loop = Loop()


class App:
    def __init__(s, rt):
        s.rt = rt; s.rt.title("对分易签到助手 v3"); s.rt.configure(bg=CB)
        s.rt.resizable(True, True); s.rt.geometry("1220x840"); s.rt.minsize(1000, 640)
        s._cs = []
        s._lcol = False          # 登录卡是否已收起（不能用 winfo_ismapped 判断，窗口未映射时恒为 0）
        ttk.Style().theme_use("clam")
        s._ui(); s._init()

    def _ui(s):
        hd = tk.Frame(s.rt, bg=CPRI, height=48); hd.pack(fill=tk.X); hd.pack_propagate(False)
        tk.Label(hd, text="对分易签到助手", font=("Microsoft YaHei UI", 16, "bold"),
                 bg=CPRI, fg="white").pack(side=tk.LEFT, padx=20, pady=10)
        s._sv = tk.StringVar(value="● 就绪")
        s._sl = tk.Label(hd, textvariable=s._sv, font=("Microsoft YaHei UI", 11), bg=CPRI, fg="#a0c4ff")
        s._sl.pack(side=tk.RIGHT, padx=20, pady=10)

        bd = tk.Frame(s.rt, bg=CB); bd.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        # 左栏：底部常驻「开始/停止」，上方内容可滚动，避免课程选择被窗口高度截掉
        lfw = tk.Frame(bd, bg=CB, width=388); lfw.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        lfw.pack_propagate(False)

        abar = tk.Frame(lfw, bg=CB); abar.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        s._sb = tk.Button(abar, text="▶  开始监听", command=s._go, font=("Microsoft YaHei UI", 13, "bold"),
                          bg=CSUC, fg="white", relief=tk.FLAT, state=tk.NORMAL, cursor="hand2",
                          activebackground="#1aa65a", activeforeground="white", padx=16, pady=9)
        s._sb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        s._xb = tk.Button(abar, text="■  停止", command=s._stop, font=("Microsoft YaHei UI", 13, "bold"),
                          bg=CDAN, fg="white", relief=tk.FLAT, state=tk.DISABLED, cursor="hand2",
                          activebackground="#d1344f", activeforeground="white", padx=16, pady=9)
        s._xb.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))

        cvw = tk.Frame(lfw, bg=CB); cvw.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        s._lfr = tk.Canvas(cvw, bg=CB, highlightthickness=0, bd=0, takefocus=0)
        sbs = ttk.Scrollbar(cvw, orient="vertical", command=s._lfr.yview)
        s._lfr.configure(yscrollcommand=sbs.set)
        sbs.pack(side=tk.RIGHT, fill=tk.Y)
        s._lfr.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lf = tk.Frame(s._lfr, bg=CB); s._lf = lf
        s._lpane = s._lfr.create_window((0, 0), window=lf, anchor="nw")
        lf.bind("<Configure>", lambda e: s._lfr.configure(scrollregion=s._lfr.bbox("all")))
        s._lfr.bind("<Configure>", lambda e: s._lfr.itemconfigure(s._lpane, width=e.width))

        lc = tk.Frame(lf, bg=CC, highlightbackground=CBO, highlightthickness=1, padx=16, pady=14)
        lc.pack(fill=tk.X)
        lch = tk.Frame(lc, bg=CC); lch.pack(fill=tk.X)
        tk.Label(lch, text="账号登录", font=("Microsoft YaHei UI", 14, "bold"),
                 bg=CC, fg=CTX).pack(side=tk.LEFT)
        s._ltg = tk.Button(lch, text="收起", command=s._toggle_login,
                           font=("Microsoft YaHei UI", 9), bg=CBO, fg=CTX, relief=tk.FLAT,
                           cursor="hand2", padx=8, pady=1)
        s._ltg.pack(side=tk.RIGHT)
        lbody = tk.Frame(lc, bg=CC); lbody.pack(fill=tk.X); s._lbody = lbody
        nb = ttk.Notebook(lbody); t1 = ttk.Frame(nb); t2 = ttk.Frame(nb)
        nb.add(t1, text="微信扫码登录"); nb.add(t2, text="账号密码登录"); nb.pack(fill=tk.X, pady=(10, 0))

        wf = tk.Frame(t1, bg=CC); wf.pack(fill=tk.X, padx=6, pady=(8, 4))
        tk.Label(wf, text="支持数字码 / 二维码 / 定位 / 汉字签到",
                 font=("Microsoft YaHei UI", 10), bg=CC, fg=CT2).pack(anchor=tk.W)
        s._le = tk.Entry(wf, font=("Microsoft YaHei UI", 12), relief=tk.FLAT, bg="#f1f2f6",
                         insertbackground=CPRI)
        s._le.pack(fill=tk.X, pady=(8, 4), ipady=6)
        s._le.insert(0, "粘贴微信授权链接到这里..."); s._le.configure(fg=CT2)
        s._le.bind("<FocusIn>", lambda e: s._clr(s._le, "粘贴微信授权链接到这里..."))
        s._le.bind("<FocusOut>", lambda e: s._rst(s._le, "粘贴微信授权链接到这里..."))
        tk.Button(wf, text="微信登录", command=s._wx, font=("Microsoft YaHei UI", 12, "bold"),
                  bg=CWE, fg="white", relief=tk.FLAT, cursor="hand2",
                  activebackground="#06ad56", activeforeground="white", padx=24, pady=6).pack(pady=4)

        tk.Button(wf, text="① 复制授权链接（在电脑微信里打开）", command=s._copy_wx,
                  font=("Microsoft YaHei UI", 10, "bold"), bg="#0984e3", fg="white",
                  relief=tk.FLAT, cursor="hand2", activebackground="#0a72c2",
                  activeforeground="white", pady=5).pack(fill=tk.X, pady=(8, 4))
        ht = tk.Text(wf, font=("Microsoft YaHei UI", 9), bg=CC, fg=CT2, relief=tk.FLAT,
                     height=3, wrap=tk.WORD, borderwidth=0, cursor="arrow")
        ht.insert("1.0", "② 进入页面后点右上角 ··· → 复制链接\n"
                         "③ 粘到上面输入框，点『微信登录』\n"
                         "（授权码只能用一次且几分钟过期，复制后尽快登录）")
        ht.configure(state=tk.DISABLED); ht.pack(fill=tk.X, pady=(2, 0))

        pf = tk.Frame(t2, bg=CC); pf.pack(fill=tk.X, padx=6, pady=(10, 4))
        tk.Label(pf, text="账号密码登录无法完成二维码签到", font=("Microsoft YaHei UI", 10),
                 bg=CC, fg=CDAN).pack()
        for lb, vn, sh in [("账号", "_ue", ""), ("密码", "_pe", "*")]:
            tk.Label(pf, text=lb, font=("Microsoft YaHei UI", 11, "bold"),
                     bg=CC, fg=CTX).pack(anchor=tk.W, pady=(8, 2))
            en = tk.Entry(pf, font=("Microsoft YaHei UI", 12), show=sh, relief=tk.FLAT,
                          bg="#f1f2f6", insertbackground=CPRI)
            en.pack(fill=tk.X, ipady=6); setattr(s, vn, en)
        tk.Button(pf, text="登录", command=s._pwd, font=("Microsoft YaHei UI", 12, "bold"),
                  bg=CPRI, fg="white", relief=tk.FLAT, cursor="hand2",
                  activebackground="#2751c0", activeforeground="white", padx=24, pady=6
                  ).pack(pady=(10, 4))

        bf = tk.Frame(lc, bg=CC); bf.pack(fill=tk.X, pady=(8, 0)); s._lbf = bf
        tk.Button(bf, text="坐标管理", command=s._open_coord_window,
                  font=("Microsoft YaHei UI", 11, "bold"), bg="#6c5ce7", fg="white",
                  relief=tk.FLAT, cursor="hand2", pady=6).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        tk.Button(bf, text="诊断", command=s._diag,
                  font=("Microsoft YaHei UI", 11, "bold"), bg="#0984e3", fg="white",
                  relief=tk.FLAT, cursor="hand2", pady=6).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(bf, text="清除登录", command=s._logout,
                  font=("Microsoft YaHei UI", 11, "bold"), bg="#636e72", fg="white",
                  relief=tk.FLAT, cursor="hand2", pady=6).pack(side=tk.LEFT)
        tk.Button(bf, text="日志", command=s._open_log,
                  font=("Microsoft YaHei UI", 11, "bold"), bg="#00a8a8", fg="white",
                  relief=tk.FLAT, cursor="hand2", pady=6).pack(side=tk.LEFT, padx=(4, 0))

        df = tk.Frame(lc, bg=CC); df.pack(fill=tk.X, pady=(10, 0))
        c1 = tk.Frame(df, bg=CC); c1.pack(side=tk.LEFT)
        tk.Label(c1, text="签到延迟(秒)", font=("Microsoft YaHei UI", 10),
                 bg=CC, fg=CT2).pack(anchor=tk.W)
        s._se = tk.Entry(c1, font=("Microsoft YaHei UI", 12), relief=tk.FLAT,
                         bg="#f1f2f6", width=8, insertbackground=CPRI)
        s._se.insert(0, "0"); s._se.pack(anchor=tk.W, ipady=4, pady=(2, 0))
        c2 = tk.Frame(df, bg=CC); c2.pack(side=tk.LEFT, padx=(16, 0))
        tk.Label(c2, text="轮询间隔(秒, 最小0.5)", font=("Microsoft YaHei UI", 10),
                 bg=CC, fg=CT2).pack(anchor=tk.W)
        s._ie = tk.Entry(c2, font=("Microsoft YaHei UI", 12), relief=tk.FLAT,
                         bg="#f1f2f6", width=8, insertbackground=CPRI)
        s._ie.insert(0, "1"); s._ie.pack(anchor=tk.W, ipady=4, pady=(2, 0))

        cc = tk.Frame(lf, bg=CC, highlightbackground=CBO, highlightthickness=1, padx=16, pady=14)
        cc.pack(fill=tk.X, pady=(12, 0))
        hdr = tk.Frame(cc, bg=CC); hdr.pack(fill=tk.X)
        tk.Label(hdr, text="课程选择", font=("Microsoft YaHei UI", 14, "bold"),
                 bg=CC, fg=CTX).pack(side=tk.LEFT)
        tk.Button(hdr, text="刷新课程", command=s._reload_courses,
                  font=("Microsoft YaHei UI", 9), bg=CBO, fg=CTX, relief=tk.FLAT,
                  cursor="hand2", padx=8, pady=2).pack(side=tk.RIGHT)
        s._cv = tk.StringVar()
        s._co = ttk.Combobox(cc, textvariable=s._cv, state="readonly",
                             font=("Microsoft YaHei UI", 12))
        s._co.pack(fill=tk.X, pady=(10, 4), ipady=2)
        s._co.bind("<<ComboboxSelected>>", lambda e: s._pick())
        s._ch = tk.Label(cc, text="下拉选中课程后，点左下『开始监听』",
                         font=("Microsoft YaHei UI", 9), bg=CC, fg=CT2,
                         wraplength=320, justify=tk.LEFT)
        s._ch.pack(anchor=tk.W, pady=(0, 8))

        mf = tk.Frame(cc, bg=CC); mf.pack(fill=tk.X, pady=(0, 8))
        tk.Label(mf, text="取不到课程列表时，填 TClassID 也能直接监听"
                          "（网页签到页地址栏 classid= 后面那串数字）",
                 font=("Microsoft YaHei UI", 9), bg=CC, fg=CT2,
                 wraplength=330, justify=tk.LEFT).pack(anchor=tk.W)
        gf = tk.Frame(mf, bg=CC); gf.pack(fill=tk.X, pady=(6, 0))
        gf.columnconfigure(1, weight=1)
        for i, (lb, vn) in enumerate([("名称", "_mn"), ("TClassID", "_mt"), ("CourseID", "_mc")]):
            tk.Label(gf, text=lb, width=9, anchor=tk.W, font=("Microsoft YaHei UI", 10),
                     bg=CC, fg=CTX).grid(row=i, column=0, sticky="w", pady=2)
            en = tk.Entry(gf, font=("Microsoft YaHei UI", 10), relief=tk.FLAT, width=10,
                          bg="#f1f2f6", insertbackground=CPRI)
            en.grid(row=i, column=1, sticky="ew", pady=2, ipady=3)
            setattr(s, vn, en)
        tk.Button(mf, text="添加并使用", command=s._add_manual,
                  font=("Microsoft YaHei UI", 10, "bold"), bg="#6c5ce7", fg="white",
                  relief=tk.FLAT, cursor="hand2", pady=4).pack(fill=tk.X, pady=(6, 0))

        s._cc = cc


        rp = tk.Frame(bd, bg=CC, highlightbackground=CBO, highlightthickness=1)
        rp.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        rh = tk.Frame(rp, bg=CC, padx=16, pady=10); rh.pack(fill=tk.X)
        tk.Label(rh, text="运行日志", font=("Microsoft YaHei UI", 14, "bold"),
                 bg=CC, fg=CTX).pack(side=tk.LEFT)
        tk.Button(rh, text="清空", command=lambda: s._lb.delete("1.0", tk.END),
                  font=("Microsoft YaHei UI", 10), bg=CBO, fg=CTX, relief=tk.FLAT,
                  cursor="hand2", padx=10, pady=2).pack(side=tk.RIGHT)
        tk.Frame(rp, bg=CBO, height=1).pack(fill=tk.X, padx=16)
        lf2 = tk.Frame(rp, bg=CLB, padx=4, pady=4); lf2.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        s._lb = tk.Text(lf2, font=("Cascadia Code", 11), wrap=tk.WORD, bg=CLB, fg=CLF,
                        relief=tk.FLAT, borderwidth=0, insertbackground="white",
                        padx=10, pady=8, selectbackground="#3b3b5c")
        sb2 = tk.Scrollbar(lf2, bg=CLB, troughcolor=CLB, activebackground=CPRI)
        s._lb.configure(yscrollcommand=sb2.set); sb2.configure(command=s._lb.yview)
        sb2.pack(side=tk.RIGHT, fill=tk.Y); s._lb.pack(fill=tk.BOTH, expand=True)
        for t, c in [("info", "#74b9ff"), ("ok", "#00b894"), ("warn", "#fdcb6e"),
                     ("error", "#ff7675"), ("debug", "#636e72")]:
            s._lb.tag_configure(t, foreground=c)
        log.set(s._lb)
        lp = log.file()
        if lp:
            log.i(f"日志文件: {lp}")
        s._bind_wheel(lf)

    def _bind_wheel(s, root):
        """左栏内所有控件滚轮都滚左栏，避免鼠标停在控件上时滚不动。"""
        def h(e):
            d = -1 if e.delta > 0 else 1
            try: s._lfr.yview_scroll(d * 2, "units")
            except Exception: pass
            return "break"
        def walk(w):
            try: w.bind("<MouseWheel>", h)
            except Exception: pass
            for c in w.winfo_children():
                walk(c)
        walk(root)

    def _focus_course(s, _try=0):
        """把左栏滚到课程选择卡位置（登录成功后调用）。窗口尚未布局时延迟重试。"""
        try:
            s._lfr.update_idletasks()
            lf, cc = s._lf, s._cc
            if (not lf.winfo_viewable() or lf.winfo_height() <= 2) and _try < 5:
                s.rt.after(200, lambda: s._focus_course(_try + 1))
                return
            total = max(1, lf.winfo_height())
            s._lfr.configure(scrollregion=s._lfr.bbox("all"))
            s._lfr.yview_moveto(max(0.0, min(1.0, (cc.winfo_y() - 8) / total)))
        except Exception:
            pass

    def _open_coord_window(s):
        win = tk.Toplevel(s.rt)
        win.title("坐标管理"); win.geometry("720x520"); win.configure(bg=CB)
        win.transient(s.rt); win.grab_set()
        tk.Label(win, text="课程定位坐标管理", font=("Microsoft YaHei UI", 14, "bold"),
                 bg=CB, fg=CTX).pack(pady=(16, 8))
        tk.Label(win, text="填了就用你填的（优先于教室坐标）；留空则按「上次成功点 → 教室」自动试，签不上会自动换圆心",
                 font=("Microsoft YaHei UI", 9), bg=CB, fg=CT2).pack()
        tk.Label(win, text="经度那一栏可以直接粘贴「114.40061,22.70745」或高德/腾讯地图链接，会自动拆分",
                 font=("Microsoft YaHei UI", 9), bg=CB, fg=CT2).pack(pady=(2, 0))

        canvas = tk.Canvas(win, bg=CB, highlightthickness=0)
        scrollbar = tk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
        frame = tk.Frame(canvas, bg=CB)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(e): canvas.yview_scroll(-1 * (e.delta // 120), "units")
        win.bind_all("<MouseWheel>", _on_mousewheel, "+")
        win.bind("<Destroy>", lambda e: win.unbind_all("<MouseWheel>"))

        locs = LocMgr.load(); widgets = {}; row = 0
        for ci, (hdr, w) in enumerate([("课程", 28), ("经度", 14), ("纬度", 14), ("备注", 18)]):
            tk.Label(frame, text=hdr, font=("Microsoft YaHei UI", 10, "bold"), bg=CB, fg=CTX,
                     width=w, anchor=tk.W if ci == 0 else tk.CENTER).grid(row=row, column=ci, padx=3, pady=3)
        row += 1
        for c in s._cs:
            name = c.get("CourseName", "")
            if not name: continue
            loc = locs.get(name, {"lng": "", "lat": "", "note": ""})
            tk.Label(frame, text=name, font=("Microsoft YaHei UI", 10), bg=CC,
                     anchor=tk.W, width=28).grid(row=row, column=0, padx=3, pady=1, sticky=tk.W)
            le = tk.Entry(frame, font=("Microsoft YaHei UI", 10), relief=tk.FLAT,
                          bg="#f1f2f6", width=14, insertbackground=CPRI)
            le.insert(0, loc.get("lng", "")); le.grid(row=row, column=1, padx=3)
            ae = tk.Entry(frame, font=("Microsoft YaHei UI", 10), relief=tk.FLAT,
                          bg="#f1f2f6", width=14, insertbackground=CPRI)
            ae.insert(0, loc.get("lat", "")); ae.grid(row=row, column=2, padx=3)
            ne = tk.Entry(frame, font=("Microsoft YaHei UI", 10), relief=tk.FLAT,
                          bg="#f1f2f6", width=18, insertbackground=CPRI)
            ne.insert(0, loc.get("note", "")); ne.grid(row=row, column=3, padx=3)
            widgets[name] = (le, ae, ne); row += 1

        def save_all():
            cur = LocMgr.load(); bad = []
            for name, (le, ae, ne) in widgets.items():
                a = (le.get() or "").strip()
                b = (ae.get() or "").strip()
                if not a and not b:
                    # 留空 = 不指定，交给服务端教室坐标
                    cur[name] = {"lng": "", "lat": "", "note": ne.get().strip()}
                    continue
                lng, lat = "", ""
                if a and ("," in a or "，" in a or "http" in a.lower()):
                    lng, lat = parse_lnglat(a)      # 粘的是一串坐标或地图链接
                if not lng or not lat:
                    lng, lat = parse_lnglat(f"{a},{b}") if (a and b) else ("", "")
                if not lng or not lat:
                    lng, lat = a, b
                try:
                    float(lng); float(lat)
                except (TypeError, ValueError):
                    bad.append(name)
                    continue
                cur[name] = {"lng": lng, "lat": lat, "note": ne.get().strip()}
            if bad:
                messagebox.showwarning(
                    "格式不对",
                    "这些课程填的不是经纬度数字，先改一下：\n\n" + "\n".join(bad[:8]), parent=win)
                return
            LocMgr.save(cur)
            messagebox.showinfo("提示", "坐标已保存", parent=win)

        tk.Button(frame, text="保存全部", command=save_all, font=("Microsoft YaHei UI", 11, "bold"),
                  bg=CSUC, fg="white", relief=tk.FLAT, cursor="hand2", padx=24, pady=8
                  ).grid(row=row + 1, column=0, columnspan=4, pady=(16, 0))

    def _clr(s, e, ph):
        if e.get() == ph:
            e.delete(0, tk.END); e.configure(fg=CTX)

    def _rst(s, e, ph):
        if e.get() == "":
            e.insert(0, ph); e.configure(fg=CT2)

    def _st(s, tx, cl):
        s._sv.set(tx); s._sl.configure(fg=cl)

    def _init(s):
        s._cs = list(Manual.load())
        s._refresh_combo()
        try:
            import zxingcpp  # noqa: F401
            log.i("二维码识别组件已就绪（可自动识别大屏二维码）")
        except Exception:
            log.w("未安装二维码识别组件：二维码签到无法自动识别，请重装完整版程序")
        if s._cs:
            log.i(f"已载入 {len(s._cs)} 门手动课程，登录后可直接监听")
        if Auth.load():
            if Auth.ok():
                s._collapse_login()
                s._rf()
            else: log.i("Cookie 已过期，请重新登录")
        else:
            try: session.get(HOST, timeout=10)
            except Exception: log.w("无法连接对分易")
            log.i("未找到 Cookie，请登录")

    def _logout(s):
        if not messagebox.askyesno("确认", "清除本地保存的登录状态？"):
            return
        Auth.logout()
        s._cs = []
        s._co["values"] = ()
        s._co.set("")
        try:
            s._expand_login()
        except Exception:
            pass
        s._sb.configure(state=tk.DISABLED)
        s._xb.configure(state=tk.DISABLED)
        s._st("已清除登录", CT2)

    def _open_log(s):
        """打开日志目录：app_日期.log 是运行日志，dump/ 里是失败响应的原始页面。"""
        import subprocess
        d = _log_dir()
        log.file()
        try:
            os.startfile(d)
        except Exception:
            try:
                subprocess.Popen(["explorer", d])
            except Exception as ex:
                log.e(f"打不开目录: {ex}")
        log.i(f"日志目录: {d}")

    def _diag(s):
        log.i("──────── 诊断开始 ────────")
        log.i(f"本机时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log.i(f"本地 Cookie: {'有' if _reg_get('cookie', '') else '无'}")
        try:
            h = {"Referer": HOST + "/_UserCenter/PC/CenterStudent.aspx", **FORM_H}
            r = session.post(HOST + "/AppCode/LoginInfo.ashx", data="Action=checklogin",
                             headers=h, timeout=10)
            log.i(f"checklogin → HTTP {r.status_code} {(r.text or '')[:120]}")
        except Exception as ex:
            log.e(f"checklogin 异常: {ex}")
        r, err = Course.raw()
        if r is None:
            log.e(f"课程接口异常: {err}")
        else:
            log.i(f"课程接口 → HTTP {r.status_code} {(r.text or '')[:200]}")
        log.i("学生ID: {0}".format(CI.uid() or "未取到"))
        log.i("Cookie 项: " + (", ".join(list(session.cookies.keys())) or "无"))
        try:
            import zxingcpp as _zx
            log.i(f"二维码解码器: 可用（zxingcpp {getattr(_zx, '__version__', '')}）")
        except Exception as ex:
            log.e(f"二维码解码器: 不可用（{ex}）——二维码签到会失败，请用带 zxingcpp 的版本")
        cid = ""
        nm = s._cv.get() if hasattr(s, "_cv") else ""
        for c in (s._cs or []):
            if c.get("CourseName") == nm:
                cid = _s(c.get("CourseID")); break
        if not cid and s._cs:
            cid = _s(s._cs[0].get("CourseID"))
            log.i(f"（未选课程，用第一门「{s._cs[0].get('CourseName')}」的 CourseID={cid}）")
        b = CI.board(cid) if cid else None
        if b:
            k = _s(b.get("key"))
            head = k if len(k) < 24 else f"{k[:16]}…（{len(k)} 字符）"
            log.i(f"教师页大屏: type={b.get('type')}({TYPE_NAME.get(b.get('type'), '?')}) "
                  f"key={head!r} pinyin={_s(b.get('pinyin'))!r}")
            if b.get("type") == "2":
                log.i(f"二维码刷新设定: HFIsRefresh={_s(b.get('refresh')) or '0'} "
                      f"HFTimeInterval={_s(b.get('interval')) or '0'} 秒"
                      f"（0 表示大屏不轮换，此时大屏那张图长期有效）")
        else:
            log.i("教师页大屏: 当前没有进行中的签到（type=0 或取不到）")
        # 二维码专项：先看大屏那张图（不触发轮换），再实签一次
        if b and b.get("type") == "2":
            key = _s(b.get("key"))
            log.i(f"大屏二维码图: {len(key)} 字符")
            txt = CI.qr_image_text(key) if len(key) > 100 else None
            log.i(f"大屏二维码内容: {txt!r}" if txt else "大屏二维码内容: 识别失败")
            if txt:
                u, w = parse_qr_target(txt)
                log.i(f"→ 将请求: {u or '（无法解析）'}  [{w}]")
            t0 = time.perf_counter()
            ok, msg = CI.qr_fast(cid)
            ms = (time.perf_counter() - t0) * 1000
            tag = "成功" if ok else ("不是二维码" if ok is None else "失败")
            log.i(f"二维码实签测试: {tag} → {msg}（{ms:.0f}ms）")
        else:
            img, why = CI.qr_latest_image(cid) if cid else (None, "无课程")
            if img:
                txt = CI.qr_image_text(img)
                log.i(f"最新二维码接口: {len(img)} 字符，内容 {txt!r}")
            else:
                log.i(f"最新二维码接口: {why}（没有二维码进行中时正常）")
        # 定位专项：本次签到的圆心教室 + 全部教室 + 本地缓存
        if cid:
            b3 = CI.board(cid)
            if b3:
                log.i(f"进行中: 【{TYPE_NAME.get(b3.get('type'), b3.get('type'))}】"
                      f"HFRoomID={b3.get('roomid') or '0'}"
                      + ("（本次绑定这间教室）" if b3.get("type") == "3" else ""))
            rooms, rwhy = CI.rooms(cid)
            if rooms:
                log.i(f"课程教室 {len(rooms)} 间:")
                for i, r0 in enumerate(rooms[:6], 1):
                    mark = ("  ← 本次绑定"
                            if b3 and _s(b3.get("roomid")) == r0["roomid"] else "")
                    log.i(f"  {i}. id={r0['roomid']} {r0.get('name') or '未命名'} "
                          f"{r0['lng']},{r0['lat']}"
                          + (f" 容差 {r0['range']} 米" if r0.get("range") else "") + mark)
            else:
                log.w(f"教室坐标: {rwhy}")
            cname = s._cv.get() if hasattr(s, "_cv") else ""
            peers = Peer.best(99)
            log.i(f"消息推送: {'已连接' if hub.running() else '未连接'}"
                  f"；收到的同学坐标 {len(peers)} 个")
            for i, (plng, plat) in enumerate(peers[:4], 1):
                log.i(f"  {i}. 同学坐标 {plng},{plat}")
            cands = loc_candidates(cid, cname, {}, b3)
            log.i(f"圆心候选 {len(cands)} 个（签不上会依次换，不用你管）:")
            for i, (tag, cl, ca, _rm) in enumerate(cands, 1):
                p = loc_near(cl, ca, "diag-sample")
                log.i(f"  {i}. {tag} {cl},{ca}（实际落点距它约 "
                      f"{loc_dist_m(cl, ca, *p):.0f} 米）")
            cl, ca = LocMgr.get(cname)
            if cl and ca:
                src = "默认值" if LocMgr.is_default(cl, ca) else "人工校准"
                log.i(f"本地坐标: {cl},{ca}（{src}，排在教室之前）"
                      if src == "人工校准" else f"本地坐标: {cl},{ca}（{src}，不参与）")
        log.i("──────── 诊断结束 ────────")

    def _toggle_login(s):
        if s._lcol:
            s._expand_login()
        else:
            s._collapse_login()

    def _expand_login(s):
        try:
            s._lbody.pack(fill=tk.X, before=s._lbf)
        except Exception:
            s._lbody.pack(fill=tk.X)
        s._ltg.configure(text="收起")
        s._lcol = False

    def _collapse_login(s):
        try:
            s._lbody.pack_forget()
        except Exception:
            pass
        s._ltg.configure(text="展开")
        s._lcol = True

    def _copy_wx(s):
        try:
            s.rt.clipboard_clear(); s.rt.clipboard_append(WX_LINK); s.rt.update()
            s._st("授权链接已复制", CSUC)
            log.ok("授权链接已复制到剪贴板：在电脑微信里打开它并进入页面，再复制回调链接粘回来")
        except Exception as ex:
            log.e(f"复制失败: {ex}")

    def _wx(s):
        lnk = s._le.get().strip()
        if not lnk or "粘贴微信" in lnk:
            messagebox.showerror("错误", "请粘贴微信授权链接"); return
        s._st("登录中...", CWA)
        if Auth.wx(lnk):
            s._st("已登录", CSUC); s._collapse_login(); s._rf()
        else: s._st("登录失败", CDAN)

    def _pwd(s):
        u = s._ue.get().strip(); p = s._pe.get().strip()
        if not u or not p:
            messagebox.showerror("错误", "请输入账号和密码"); return
        s._st("登录中...", CWA)
        if Auth.pwd(u, p):
            s._st("已登录", CSUC); s._collapse_login(); messagebox.showinfo("提示", "登录成功"); s._rf()
        else:
            s._st("登录失败", CDAN)

    def _refresh_combo(s):
        s._co["values"] = tuple(c.get("CourseName") or "未命名" for c in s._cs)
        if s._cs and not s._cv.get():
            s._co.set(s._cs[0].get("CourseName") or "")

    def _add_manual(s):
        name = s._mn.get().strip() or "手动课程"
        tcid = s._mt.get().strip()
        cid = s._mc.get().strip()
        if not tcid:
            messagebox.showerror("错误", "TClassID 必填：网页端点进某门课的签到页，地址栏 classid= 后面那串")
            return
        s._cs = [c for c in s._cs if str(c.get("TClassID")) != tcid]
        item = {"CourseName": name, "TClassID": tcid, "CourseID": cid, "_manual": True}
        s._cs.append(item)
        Manual.save([c for c in s._cs if c.get("_manual")])
        s._refresh_combo()
        s._co.set(name)
        s._sb.configure(state=tk.NORMAL)
        s._st(f"已选中: {name}", CSUC)
        log.ok(f"已添加手动课程：{name}（TClassID={tcid}，CourseID={cid or '未填'}）")
        if not cid:
            log.w("未填 CourseID：汉字签到答案取不到，数字码/二维码/定位不受影响")

    def _rf(s):
        cs, err = Course.list()
        manual = Manual.load()
        if cs:
            auto_ids = {str(c.get("TClassID")) for c in cs}
            s._cs = cs + [m for m in manual if str(m.get("TClassID")) not in auto_ids]
            s._cv.set("")
            s._refresh_combo()
            s._sb.configure(state=tk.NORMAL)
            s._st(f"已加载 {len(cs)} 门课程", CSUC)
            log.ok(f"已加载 {len(cs)} 门课程" + (f"，另有 {len(s._cs) - len(cs)} 门手动" if len(s._cs) > len(cs) else ""))
            LocMgr.sync(cs)
            s._focus_course()
            log.i("请在左侧『课程选择』下拉框选中课程，再点下方『开始监听』")
            if s._cs:
                s._co.set(s._cs[0].get("CourseName") or "")
                s._pick()
            return
        s._cs = list(manual)
        s._cv.set("")
        s._refresh_combo()
        if s._cs:
            s._sb.configure(state=tk.NORMAL)
            s._st("课程列表为空，已用手动课程", CWA)
        if cs is not None:
            s._st("课程列表为空", CWA)
            log.w("课程接口返回了空列表：该账号下没有学生课程")
            log.i("如果是教师账号，请确认是用学生身份加入课程后再登录")
        else:
            s._st("获取课程失败", CDAN)
            log.e(f"获取课程失败：{err}" if err else "获取课程失败")
            if err and "禁止访问" in err:
                log.i("服务端拒绝访问说明登录没生效：点『清除登录』后重新获取授权链接")
            log.i("也可以在上方填 TClassID 点『添加并使用』，直接开始监听")
        s._focus_course()

    def _pick(s):
        nm = s._cv.get().strip()
        if not nm: return
        c = next((x for x in s._cs if (x.get("CourseName") or "") == nm), None)
        if c and c.get("TClassID"):
            s._st(f"已选: {nm[:20]}", CSUC)
            s._ch.configure(text=f"已选：{nm}　TClassID={c.get('TClassID')}"
                                 f"　CourseID={c.get('CourseID') or '未填'}　→ 点左下『开始监听』",
                            fg=CSUC)
        else:
            s._st(f"已选: {nm[:20]}（缺 TClassID）", CWA)
            s._ch.configure(text=f"已选：{nm}　缺少 TClassID，无法监听，请手动补填", fg=CWA)

    def _reload_courses(s):
        if not Auth.ok():
            messagebox.showerror("错误", "登录已失效，请先重新登录再刷新课程")
            return
        s._st("正在获取课程...", CWA)
        s._rf()

    def _go(s):
        nm = s._cv.get().strip()
        if not nm:
            messagebox.showerror("错误", "请先在下拉框选择课程，或用下方手动添加")
            return
        target = None
        for c in s._cs:
            if (c.get("CourseName") or "") == nm:
                target = c; break
        if target is None:
            messagebox.showerror("错误", f"课程『{nm}』不在当前列表里，请点『刷新课程』后重选")
            return
        ci = _s(target.get("TClassID")); cid = _s(target.get("CourseID"))
        if not ci:
            messagebox.showerror("错误", "该课程没有 TClassID，无法监听：请手动填写后添加")
            return
        if not Auth.ok():
            messagebox.showerror("错误", "登录已失效，请重新登录")
            return
        try: delay = max(0, int(s._se.get() or "0"))
        except Exception: delay = 0
        try: poll = max(500, int(float(s._ie.get() or "2") * 1000))
        except Exception: poll = POLL_BASE_MS
        s._sb.configure(state=tk.DISABLED); s._xb.configure(state=tk.NORMAL)
        s._st(f"监控中: {nm}", CPRI); s._lb.delete("1.0", tk.END)
        loop.start(ci, nm, s.rt, cid, delay, poll)

    def _stop(s):
        loop.stop()
        s._sb.configure(state=tk.NORMAL); s._xb.configure(state=tk.DISABLED)
        s._st("已停止", CT2)


def _res_path(*parts):
    """随包资源的真实路径：打包后取 _MEIPASS，源码运行时取脚本同级目录。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def set_app_icon(win):
    """窗口/任务栏/Alt+Tab 图标。PNG 走 iconphoto（多尺寸缩放更干净），ICO 兜底。"""
    p_png = _res_path("assets", "icon_256.png")
    p_ico = _res_path("assets", "icon.ico")
    try:
        if os.path.exists(p_png):
            img = tk.PhotoImage(file=p_png)
            win.iconphoto(True, img)   # True: 之后弹出的 Toplevel 一并继承
            win._icon_img = img        # 必须持引用，否则被 GC 回收后任务栏变空白
            return True
    except Exception as e:
        log.w("PNG 窗口图标加载失败: %s" % e)
    try:
        if os.path.exists(p_ico):
            win.iconbitmap(default=p_ico)
            return True
    except Exception as e:
        log.w("ICO 窗口图标加载失败: %s" % e)
    return False


if __name__ == "__main__":
    root = tk.Tk()
    set_app_icon(root)
    App(root)
    root.mainloop()
