"""「码取不到」的真因回归：活动其实早就结束了，只是行数据一直挂着 CanApply=1。

现场证据（app_20261029.log）：
    00:12:43 发现待签到! ID:2552421 类型:数字码
    00:12:43 [DEBUG] 大屏数字码: 7882
    00:12:43 [OK] 签到成功！
20 分钟后（00:32）同一门课的大屏页回到 HFChecktype=0、HFSeconds=0，
但那一行仍然 CanApply=1、ApplyLimitDate 还没到 —— 那是补签窗口，不是签到窗口。
于是老版本会拿 2 秒一次去撞，撞满 5 次刷「都没给出签到码」。

这个测试锁两件事：
  1. 大屏 type=0 且活动是几分钟前发起的 → 判定已结束，立即停手，不排重试
  2. 大屏 type=0 但活动刚发起（大屏可能还没同步）→ 仍然重试，别误杀
"""
import sys, re
from datetime import datetime, timedelta
sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import duifene_v3 as d
import os as _os; _os.environ["DUIFENE_LOG_DIR"] = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "_testlogs")


class R:
    status_code = 200
    def __init__(s, text=""):
        s.text = text
        s.url = ""
    def json(s):
        return {}


def board_html(t, key=""):
    return (f'<input type="hidden" id="HFChecktype" value="{t}" />'
            f'<input type="hidden" id="HFCheckInID" value="2552421" />'
            f'<input type="hidden" id="HFCheckCodeKey" value="{key}" />'
            f'<input type="hidden" id="HFSeconds" value="0" />')


STATE = {"board": board_html("0"), "posts": 0}
def fake_post(url, data=None, headers=None, timeout=None, **kw):
    if "TeachCheckIn.aspx" in url:
        STATE["posts"] += 1
        return R(STATE["board"])
    if "getcodeimage" in (data or ""):
        p = {"msg": -1, "msgbox": "签到已结束！"}
        r = R('{"msg":-1,"msgbox":"签到已结束！"}'); r.json = lambda: p
        return r
    r = R('{"msg":1,"msgbox":"签到成功"}'); r.json = lambda: {"msg": 1, "msgbox": "签到成功"}
    return r

d.session.post = fake_post
d.CI._uid = "7541232"
d.CI.reset()

L = d.Loop()
L._cid = "298803"; L.ci = "505019"; L.cn = "测试"

FMT = d.DATE_FMT
old_row = {"ID": "900", "CheckInType": "1",
           "CreaterDate": (datetime.now() - timedelta(minutes=20)).strftime(FMT)}
fresh_row = {"ID": "901", "CheckInType": "1",
             "CreaterDate": datetime.now().strftime(FMT)}

# ── 1) 老活动 + 大屏 type=0 → 判死，且文案说得清 ──
ok, msg = d.Loop._do(L, "1", old_row)
assert not ok, msg
assert "不在了" in msg and "已结束" in msg, msg
print("老活动 + 大屏 type=0 →", msg)

# ── 2) 判死的消息要命中 CLOSED_WORDS，_exec 才会停手 ──
assert any(w in msg for w in d.CLOSED_WORDS), f"没命中关闭词，会白白重试: {msg}"
d.CI.done = set(); L._tried = {}
L._exec("1", old_row, "900")
assert "900" in d.CI.done, "应标记完成"
assert L._tried["900"].get("next", 0) == 0, "不该排重试"
print("_exec 停手 OK，尝试次数", L._tried["900"]["count"])

# ── 3) 刚发起 20 秒 + 大屏 type=0 → 不能误杀，照常重试 ──
ok, msg = d.Loop._do(L, "1", fresh_row)
assert not ok and "手动码" in msg, msg
print("刚发起 + 大屏 type=0 →", msg[:40])

# ── 4) 大屏 type=1 有码 → 这一场就是活的，board_dead 不许说话 ──
STATE["board"] = board_html("1", "4821")
b = d.CI.board("298803")
assert b and b["type"] == "1" and b["key"] == "4821", b
assert d.board_dead(old_row) == "", "大屏有活动时不能判死"
print("大屏 type=1 → board_dead 沉默 OK")

# ── 5) 大屏 type=0 只问一次，别拿微信 UA 再撞一遍（白烧请求=限流）──
STATE["board"] = board_html("0")
STATE["posts"] = 0
d.CI.board("298803")
assert STATE["posts"] == 1, f"type=0 时问了 {STATE['posts']} 次，应为 1 次"
print("type=0 只请求一次 OK")

# ── 6) HTTP 异常仍要走 UA 兜底（这才叫「取不到」）──
class Bad:
    status_code = 500
    text = ""
    url = ""
def bad_post(url, data=None, headers=None, timeout=None, **kw):
    return Bad()
d.session.post = bad_post
STATE["posts"] = 0
d.CI.board("298803")
assert d.CI.last_board.get("why"), d.CI.last_board
print("HTTP 500 →", d.CI.board_why()[:30], "OK")

print("\nSTALE LOGIC OK")
