# -*- coding: utf-8 -*-
"""签到自检：把「数字码」「汉字」两条链路能查的都查一遍，上课前跑一次。

用法：
    ./.venv/Scripts/python.exe _dev/selfcheck.py                # 只做只读与离线自检
    ./.venv/Scripts/python.exe _dev/selfcheck.py --course 测试
    ./.venv/Scripts/python.exe _dev/selfcheck.py --sign         # 有进行中签到时真提交

说明：真实签到必须有「进行中的签到」才能验；没有时这一项只能是 SKIP。
其余各项都是真请求（只读）或本地桩，不会改动服务端任何数据。
"""
import os, sys, json, argparse
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["DUIFENE_LOG_DIR"] = os.path.join(HERE, "_testlogs")   # 别污染真实日志

import duifene_v3 as d

RESULTS = []


def check(name, fn, optional=False):
    """跑一项，打印对齐结果；返回该项的值供后续使用。"""
    try:
        val = fn()
        ok = True if val is None else bool(val[0])
        msg = "" if val is None else str(val[1] or "")
    except Exception as ex:
        ok, msg, val = False, "%s: %s" % (type(ex).__name__, ex), None
    tag = "PASS" if ok else ("SKIP" if optional else "FAIL")
    RESULTS.append((name, tag, msg))
    dots = "." * max(3, 18 - len(name) * 2)
    print("  %-16s %s %-4s %s" % (name, dots, tag, msg))
    return val


class R:
    status_code = 200

    def __init__(s, text=""):
        s.text = text
        s.url = ""

    def json(s):
        return json.loads(s.text)


def stub_post(body_out, reply):
    """把 session.post 换成本地桩，收集提交体。"""
    real = d.session.post

    def fake(url, data=None, headers=None, timeout=None, **kw):
        body_out.append((url, data))
        return R(json.dumps(reply))
    return real, fake


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default="", help="自检用哪门课（默认：测试，找不到就用第一门）")
    ap.add_argument("--sign", action="store_true", help="有进行中签到时真的提交")
    a = ap.parse_args()

    print("\n对分易签到助手 自检  %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # ── 1) 登录态 ──
    def _auth():
        d.Auth.load()
        uid = d.CI.uid()
        if not uid:
            raise RuntimeError("拿不到 UID，Cookie 可能已失效，请重新登录")
        if not d.Auth.ok():
            raise RuntimeError("登录态无效，请重新登录")
        return True, "UID %s" % uid
    uid = None
    check("登录态", _auth)

    # ── 2) 课程列表 ──
    courses = []

    def _courses():
        lst = d.Course.list()[0] or []
        courses.extend(lst)
        if not lst:
            raise RuntimeError("课程列表为空")
        return True, "%d 门课" % len(lst)
    check("课程列表", _courses)

    # 选一门课
    target = None
    want = a.course.strip()
    for c in courses:
        nm = (c.get("CourseName") or "")
        if want and want not in nm:
            continue
        target = c
        break
    if target is None and courses:
        target = courses[0]
    if target is None:
        print("  ! 没有可用课程，后面的接口项跳过")
        return 1
    tname = target.get("CourseName") or "?"
    tclid = str(target.get("TClassID") or "")
    tcid = str(target.get("CourseID") or "")
    print("  自检课程: %s（TClassID %s / CourseID %s）" % (tname, tclid, tcid))

    # ── 3) 今日签到记录接口 ──
    rows = []

    def _rows():
        got = d.CI.fetch(tclid)
        if got is None:
            raise RuntimeError("取不到记录（%s）" % (d.CI.last_err if hasattr(d.CI, "last_err") else "见日志"))
        rows.extend(got)
        return True, "%d 条记录" % len(got)
    check("签到记录接口", _rows)

    # ── 4) 大屏页字段（取码的唯一来源）──
    code_now = ""

    def _board():
        b = d.CI.board(tcid)
        lb = d.CI.last_board or {}
        need = ("HFChecktype", "HFCheckCodeKey", "HFSeconds")
        f = lb.get("fields") or {}
        miss = [k for k in need if k not in f]
        if miss:
            raise RuntimeError("页面缺字段 %s（大屏页可能改版）" % ",".join(miss))
        if b is None:
            return True, "字段齐全；当前无进行中签到（type=%s）" % (lb.get("type") or "?")
        nonlocal code_now
        code_now = d._s(b.get("key"))
        return True, "进行中：type=%s 码=%s" % (b.get("type"), code_now or "空")
    check("大屏取码字段", _board)

    # ── 5) 数字码提交链路（本地桩，验请求体）──
    def _num():
        body = []
        real, fake = stub_post(body, {"msg": 1, "msgbox": "签到成功"})
        d.session.post = fake
        try:
            ok, msg = d.CI.code(d.CI.uid(), "7882")
        finally:
            d.session.post = real
        if not body:
            raise RuntimeError("没有发出请求")
        data = body[-1][1] or ""
        if "checkincode=7882" not in data:
            raise RuntimeError("提交体不对：%r" % data)
        if "action=studentcheckin" not in data:
            raise RuntimeError("缺 action：%r" % data)
        if not ok:
            raise RuntimeError("成功响应被判成失败：%s" % msg)
        return True, "checkincode=7882 已发出，判定「%s」" % msg
    check("数字码提交", _num)

    # ── 6) 汉字提交链路（本地桩，重点验 percent-encode）──
    def _han():
        word = "春风化雨"
        body = []
        real, fake = stub_post(body, {"msg": 1, "msgbox": "签到成功"})
        d.session.post = fake
        try:
            ok, msg = d.CI.code(d.CI.uid(), word)
        finally:
            d.session.post = real
        data = (body[-1][1] if body else "") or ""
        if word in data:
            raise RuntimeError("中文原样发出去了，真实环境会 latin-1 崩溃")
        if "%E6%98%A5" not in data.upper():
            raise RuntimeError("没有 percent-encode：%r" % data)
        if not ok:
            raise RuntimeError("成功响应被判成失败：%s" % msg)
        return True, "%s → %s…" % (word, data.split("checkincode=")[-1][:18])
    check("汉字提交(编码)", _han)

    # ── 7) 服务端应答判定 ──
    def _verdict():
        cases = [
            ({"msg": 1, "msgbox": "签到成功"}, True, "正常成功"),
            ({"msg": -2, "msgbox": "已经签到过了"}, True, "重复签到算成功"),
            ({"msg": 0, "msgbox": "签到码错误"}, False, "错码要判失败"),
            ({"msg": 0, "msgbox": "没有正在进行中的签到"}, False, "无活动要判失败"),
        ]
        for reply, want_ok, why in cases:
            ok, _ = d.CI._verdict(R(json.dumps(reply)))
            if ok != want_ok:
                raise RuntimeError("%s 判错了：%s" % (why, reply))
        return True, "%d 种应答判定正确" % len(cases)
    check("应答判定", _verdict)

    # ── 8) 已结束的活动不再空转 ──
    def _dead():
        old = d.CI.last_board
        d.CI.last_board = {"type": "0", "fields": {"HFChecktype": "0"}}
        try:
            t = (datetime.now() - timedelta(minutes=10)).strftime(d.DATE_FMT)
            hit = d.board_dead({"ID": "1", "CreaterDate": t})
            fresh = d.board_dead({"ID": "2", "CreaterDate": datetime.now().strftime(d.DATE_FMT)})
        finally:
            d.CI.last_board = old
        if not hit:
            raise RuntimeError("10 分钟前发起、大屏 type=0，却没判成已结束")
        if fresh:
            raise RuntimeError("刚发起就被判死，会误杀")
        return True, "老的判死、刚发起的不误杀"
    check("过期判死", _dead)

    # ── 9) 真实签到（必须有进行中的签到）──
    def _real():
        if not code_now:
            return None, "当前没有进行中的签到，无法验证真实提交"
        if not a.sign:
            return True, "发现可用码 %s，加 --sign 可实签" % code_now
        ok, msg = d.CI.code(d.CI.uid(), code_now)
        if not ok:
            raise RuntimeError("真签失败：%s" % msg)
        return True, "真签成功（%s）" % msg
    check("真实签到", _real, optional=True)

    bad = [r for r in RESULTS if r[1] == "FAIL"]
    skip = [r for r in RESULTS if r[1] == "SKIP"]
    print("\n结果: %d 项通过，%d 项失败，%d 项跳过"
          % (len(RESULTS) - len(bad) - len(skip), len(bad), len(skip)))
    if bad:
        print("失败项：" + "、".join(n for n, _, _ in bad))
    print("")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
