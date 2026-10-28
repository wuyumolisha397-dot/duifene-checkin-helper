# -*- coding: utf-8 -*-
"""「签到发起后才开监听」的回归：必须明说是来晚了，不许报成取不到码。

现场证据（别人机器上的「实习」）：
    00:06:43 监听开始
    00:07:27 发现待签到! ID:2552417 类型:数字码
    00:07:27~44 连续 5 次「服务端与教师页都没给出签到码」
那门课当时大屏 HFChecktype=0（服务端认为没有进行中的签到），所以码根本不存在，
不是拿不到。这类失败的唯一成因就是「去晚了」——先发起、后开监听。

这个测试锁四件事：
  1. 活动比本次监听还早 → 警告里出现「来晚了」和具体早了多少
  2. 活动是监听之后发起的 → 不警告（别把正常签到吓成失败）
  3. 同一条活动只警告一次（轮询一秒一次，别刷屏）
  4. CreaterDate 缺失/乱码 → 不警告也不崩
"""
import sys
from datetime import datetime, timedelta
sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import duifene_v3 as d
import os as _os; _os.environ["DUIFENE_LOG_DIR"] = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "_testlogs")

WARNS = []
d.log.w = lambda m, *a, **k: WARNS.append(m)
d.log.i = lambda m, *a, **k: None
d.log.d = lambda m, *a, **k: None
d.log.ok = lambda m, *a, **k: None
d.log.e = lambda m, *a, **k: None


def row(rid, ago_s):
    t = datetime.now() - timedelta(seconds=ago_s)
    return {"ID": rid, "CheckInType": "1",
            "CreaterDate": t.strftime(d.DATE_FMT)}


# ── 1) 比监听早 6 分钟：必须警告，且写明「6 分」+「来晚了」 ──
s = d.Loop(); s._t0 = datetime.now()
d.Loop._warn_if_late(s, row("2552417", 6 * 60), "2552417")
assert len(WARNS) == 1, WARNS
msg = WARNS[-1]
assert "来晚了" in msg, msg
assert "6 分" in msg, msg
assert "开始监听" in msg, msg
print("1) 监听前发起 → 警告 OK：", msg[:46], "...")

# ── 2) 同一条再报一次：不许重复刷 ──
d.Loop._warn_if_late(s, row("2552417", 6 * 60), "2552417")
assert len(WARNS) == 1, "重复警告了：%s" % WARNS
print("2) 同一活动只警告一次 OK")

# ── 3) 监听之后才发起：不警告（正常情况） ──
d.Loop._warn_if_late(s, row("2552421", -3), "2552421")   # 3 秒后发起
assert len(WARNS) == 1, "正常签到被误警告：%s" % WARNS
print("3) 监听后发起 → 不警告 OK")

# ── 4) 时间缺失/乱码：不警告、不抛异常 ──
d.Loop._warn_if_late(s, {"ID": "x", "CreaterDate": ""}, "x")
d.Loop._warn_if_late(s, {"ID": "y", "CreaterDate": "0000-00-00"}, "y")
d.Loop._warn_if_late(s, {"ID": "z"}, "z")
assert len(WARNS) == 1, WARNS
print("4) 脏数据不警告不崩 OK")

# ── 5) 走一遍 _dispatch，确认真的接上了（不是只改了函数没人调） ──
d.Loop._exec = lambda s, ct, p, rid: None
s2 = d.Loop(); s2._t0 = datetime.now()
WARNS[:] = []
d.Loop._dispatch(s2, row("2552417", 7 * 60))
assert len(WARNS) == 1 and "来晚了" in WARNS[0], WARNS
print("5) _dispatch 已接线 OK")

# ── 6) 没设 _t0（理论上不会，但别炸） ──
s3 = d.Loop()
d.Loop._warn_if_late(s3, row("1", 600), "1")
print("6) 无 _t0 不崩 OK")

print("ALL OK")
