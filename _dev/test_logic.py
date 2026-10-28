import sys, time, json
from datetime import datetime, timedelta

sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
import duifene_v3 as d
import os as _os; _os.environ["DUIFENE_LOG_DIR"] = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "_testlogs")

fails = []
def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + f"  got={got!r} want={want!r}")
    if not ok: fails.append(name)

class R:
    def __init__(self, payload=None, text=None, code=200):
        self._p = payload; self.text = text if text is not None else (json.dumps(payload, ensure_ascii=False) if payload is not None else ""); self.status_code = code
    def json(self):
        if self._p is None: raise ValueError("no json")
        return self._p

print("--- row_signed ---")
check("空串未签", d.row_signed({"CheckInStatus": ""}), False)
check("0 未签", d.row_signed({"CheckInStatus": "0"}), False)
check("1 已签", d.row_signed({"CheckInStatus": "1"}), True)
check("日期字符串已签", d.row_signed({"CheckInStatus": "2026/10/26 09:00:00"}), True)
check("StatusID=1", d.row_signed({"StatusID": "1"}), True)
check("出勤", d.row_signed({"StatusName": "出勤"}), True)
check("迟到但已签", d.row_signed({"StatusID": "2", "StatusName": "迟到"}), False)

print("--- _verdict ---")
check("msg1+成功", d.CI._verdict(R({"msg": "1", "msgbox": "签到成功"})), (True, "签到成功"))
check("msg0+未成功→失败", d.CI._verdict(R({"msg": "0", "msgbox": "签到未成功"})), (False, "签到未成功"))
check("msg-2 已签", d.CI._verdict(R({"msg": "-2", "msgbox": "您已经签到成功了"}))[0], True)
check("msg0 无成功字样", d.CI._verdict(R({"msg": "0", "msgbox": "签到码错误"}))[0], False)
d.Limiter.until = 0
ok, msg = d.CI._verdict(R({"msg": "0", "msgbox": "操作过于频繁, 请稍后再试"}))
check("限流识别", ok, False)
check("限流已静默", d.Limiter.blocked(), True)
d.Limiter.until = 0

print("--- pend ---")
now = datetime.now()
def row(**kw):
    base = {"ID": "1001", "CheckInType": "1", "CheckInCode": "1234", "CheckInStatus": "",
            "StatusID": "", "StatusName": "", "CanApply": "1",
            "ApplyLimitDate": (now + timedelta(minutes=5)).strftime(d.DATE_FMT),
            "CreaterDate": now.strftime(d.DATE_FMT), "StudentID": ""}
    base.update(kw); return base

d.CI.done.clear()
check("待签命中", (d.CI.pend([row()], {}) or {}).get("ID"), "1001")
d.CI.done.clear()
check("CanApply=0 跳过", d.CI.pend([row(CanApply="0")], {}), None)
d.CI.done.clear()
check("过期跳过", d.CI.pend([row(ApplyLimitDate=(now - timedelta(minutes=1)).strftime(d.DATE_FMT))], {}), None)
d.CI.done.clear()
check("旧活动跳过", d.CI.pend([row(CreaterDate=(now - timedelta(minutes=31)).strftime(d.DATE_FMT))], {}), None)
d.CI.done.clear()
check("超次数跳过", d.CI.pend([row()], {"1001": {"count": d.MAX_TRIES, "next": 0}}), None)
d.CI.done.clear()
check("退避未到跳过", d.CI.pend([row()], {"1001": {"count": 1, "next": time.time() + 60}}), None)
d.CI.done.clear()
check("退避到点放行", (d.CI.pend([row()], {"1001": {"count": 1, "next": time.time() - 1}}) or {}).get("ID"), "1001")

print()
print("FAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
