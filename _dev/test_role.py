# -*- coding: utf-8 -*-
"""身份回归：学生身份根本拿不到签到码，别再报「已结束」也别空转重试。

实测证据（2026-10-29 12:22，另一台机器 Mark 的账号，课程「测试」298803）：
学生版大屏页只有
    HFUserRole = {"TUserRole":[{"ID":2,"RoleName":"学生","Inlay":0}]}
HFChecktype / HFCheckCodeKey / HFCheckInID / HFTUserID 一个都没有；
页面里的 JS 也只有 getstudentcourse、joinclassbyclasscode 这类学生动作。
同一门课、同一时刻，教师身份（本机账号）的那份页面五个域齐全。

这个测试锁四件事：
  1. 学生版页面 → board() 返回 None，且能认出是「学生」
  2. 失败文案要说明「只给教师下发」，不能再推给「活动已结束」
  3. 这类失败不占重试次数，只把复查压到每分钟一次
  4. 教师版页面（有 HFChecktype）不能被误判成学生
"""
import sys, os, time
from datetime import datetime
sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import duifene_v3 as d
import os as _os; _os.environ["DUIFENE_LOG_DIR"] = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "_testlogs")

WARN, ERR, INFO = [], [], []
d.log.w = lambda m, *a, **k: WARN.append(m)
d.log.e = lambda m, *a, **k: ERR.append(m)
d.log.i = lambda m, *a, **k: INFO.append(m)
d.log.d = lambda m, *a, **k: None
d.log.ok = lambda m, *a, **k: None


class R:
    status_code = 200
    url = ""
    def __init__(s, text=""): s.text = text
    def json(s): return {}


def page(fields):
    # 属性值里会出现 JSON 的双引号，必须转义，否则解析到第一个引号就截断
    body = "".join('<input type="hidden" id="%s" value="%s" />'
                   % (k, str(v).replace("&", "&amp;").replace('"', "&quot;"))
                   for k, v in fields.items())
    return "<html><body>" + body + "</body></html>"


STUDENT = page({"HFUserRole": '{"TUserRole":[{"ID":2,"RoleName":"学生","Inlay":0}]}'})
TEACHER_ZERO = page({"HFTUserID": "7541232", "HFChecktype": "0", "HFSeconds": "0",
                     "HFRoomID": "0", "HFIsRefresh": "0", "HFTimeInterval": "0"})

STATE = {"html": STUDENT}
d.session.post = lambda *a, **k: R(STATE["html"])
d.CI.uid = lambda *a, **k: "7541232"        # 离线跑，不查真实 UID

# ── 1) 学生版页面 ──
b = d.CI.board("298803")
assert b is None, "学生版页面不该解析出活动：%s" % (b,)
assert d.CI.student_page() is True, "没认出学生身份"
assert "教师" in d.CI.board_why(), d.CI.board_why()
print("1) 学生版页面 → 认出来了：", d.CI.board_why())

# ── 2) 数字码分支的文案 ──
s = d.Loop(); s._cid = "298803"; s.cn = "测试"
info = {"count": 1, "next": 0}
row = {"ID": "2553178", "CheckInType": "1",
       "CreaterDate": datetime.now().strftime(d.DATE_FMT)}
ok, msg = s._do("1", row, info)
assert ok is False
assert "教师" in msg and "手动码" in msg, msg
assert "已结束" not in msg, "又甩锅给「已结束」了：%s" % msg
assert info.get("quiet") is True, "没打上 quiet 标记，会烧重试次数"
print("2) 数字码文案 →", msg[:58], "...")

# ── 3) 汉字分支同样处理 ──
info2 = {"count": 1, "next": 0}
ok, msg2 = s._do("4", row, info2)
assert ok is False and "教师" in msg2 and info2.get("quiet") is True, msg2
print("3) 汉字文案 →", msg2[:52], "...")

# ── 4) quiet 不占重试次数、复查间隔 60 秒、只提示一次 ──
WARN[:] = []
s2 = d.Loop(); s2._cid = "298803"; s2.cn = "测试"
info3 = {"count": 1, "next": 0}
s2._tried["2553178"] = info3
s2._exec("1", row, "2553178")
assert info3["count"] == 1, "把重试次数烧掉了（进出应该相抵）：%s" % info3
assert info3["next"] > time.time() + 50, "没有把复查压到一分钟：%s" % info3
assert len(WARN) == 1, WARN
assert "每分钟" in WARN[0], WARN
info3["next"] = 0
s2._exec("1", row, "2553178")
assert len(WARN) == 1, "重复提示了：%s" % WARN
print("4) 不占次数、60 秒复查、只提示一次 OK")

# ── 5) 教师版页面不能被误判 ──
STATE["html"] = TEACHER_ZERO
d.CI.board("298803")
assert d.CI.student_page() is False, "教师版被误判成学生了"
assert "已结束" in d.CI.board_why() or "没有进行中" in d.CI.board_why(), d.CI.board_why()
print("5) 教师版页面不误判 OK：", d.CI.board_why()[:30], "...")

print("ALL OK")
