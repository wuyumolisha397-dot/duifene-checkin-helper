# -*- coding: utf-8 -*-
"""数字码 / 汉字签到的请求体编码回归。

汉字码必须 percent-encode：body 是 str，requests 交给 http.client 后按
latin-1 编码，中文直接抛 UnicodeEncodeError，签到根本没发出去。
"""
import sys

sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
import duifene_v3 as d
import os as _os; _os.environ["DUIFENE_LOG_DIR"] = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "_testlogs")

captured = []


class R:
    status_code = 200
    text = '{"msg":1,"msgbox":"签到成功"}'

    def json(self):
        return {"msg": 1, "msgbox": "签到成功"}


_orig = d.session.post


def fake(url, data=None, headers=None, timeout=None, **kw):
    captured.append(data)
    return R()


d.session.post = fake
try:
    ok, msg = d.CI.code("7541232", "1234")
    assert ok, msg
    assert captured[-1] == "action=studentcheckin&studentid=7541232&checkincode=1234", captured[-1]
    print("1 数字码原样提交 OK:", captured[-1])

    ok, msg = d.CI.code("7541232", "頔酢郏倦")
    body = captured[-1] or ""
    assert ok, msg
    assert "頔" not in body, body                      # 中文不能出现在请求体里
    assert "%E9%A0%94" in body, body                   # 必须是 UTF-8 的 percent 编码
    body.encode("latin-1")                             # 真实发送能过（不抛 UnicodeEncodeError）
    print("2 汉字 percent-encode OK:", body)

    ok, msg = d.CI.code("7541232", "a b+c")
    assert "checkincode=a%20b%2Bc" in (captured[-1] or ""), captured[-1]
    print("3 空格/加号被正确转义 OK:", captured[-1])
finally:
    d.session.post = _orig

print("CODE FLOW OK")
