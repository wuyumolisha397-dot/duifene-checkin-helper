# -*- coding: utf-8 -*-
"""真机自检：在指定课上真发起一场签到，用工具签到，校验出勤，再把这场删掉。

    python _dev/selftest_live.py --course 测试 --type 1     # 数字码
    python _dev/selftest_live.py --course 测试 --type 4     # 汉字

流程（任何一步失败都会走 finally 删掉已创建的那场）：
    发起（GET TeachCheckIn.aspx?classid&temps&checktype）→ 读大屏码
    → 工具提交 → 拉学生记录看到「出勤」→ 删除考勤
"""
import os, sys, re, time, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["DUIFENE_LOG_DIR"] = os.path.join(HERE, "_testlogs")

import duifene_v3 as d

ASX = d.HOST + "/_CheckIn/MBCount.ashx"
HDR = {**d.FORM_H, "X-Requested-With": "XMLHttpRequest",
       "Accept": "application/json, text/javascript, */*; q=0.01"}


def list_ci(clid):
    """这门课已有的考勤场次。"""
    r = d.session.post(ASX, data={"action": "gettclasscheckinlistbypage",
                                  "classid": clid, "page": 1, "nums": 20},
                       headers={**HDR, "Referer": d.HOST + "/_CheckIn/MB/TeachCheckInList.aspx"},
                       timeout=12)
    try:
        j = r.json()
    except Exception:
        return [], r.text
    return j.get("rows") or [], None


def delete_ci(ciid):
    r = d.session.post(ASX, data={"action": "deletecheckinandlog", "ciid": ciid},
                       headers={**HDR, "Referer": d.HOST + "/_CheckIn/MB/TeachCheckInList.aspx"},
                       timeout=12)
    try:
        return r.json()
    except Exception:
        return {"raw": (r.text or "")[:120]}


def create(clid, ctype, temps=180):
    """发起一场签到。返回 (url最终地址, 响应)。"""
    url = ("%s/_CheckIn/MB/TeachCheckIn.aspx?classid=%s&temps=%d&checktype=%s"
           "&isrefresh=0&timeinterval=0&roomid=0&match=0.%d"
           % (d.HOST, clid, temps, ctype, int(time.time() * 1000) % 1000000))
    r = d.session.get(url, timeout=15, allow_redirects=True,
                      headers={"Referer": d.HOST + "/_CheckIn/MB/TeachCheckInAdd.aspx"})
    return getattr(r, "url", url), r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default="测试")
    ap.add_argument("--type", default="1", choices=["1", "4"])
    ap.add_argument("--temps", type=int, default=180)
    a = ap.parse_args()

    d.Auth.load()
    cs = d.Course.list()[0] or []
    tgt = next((c for c in cs if a.course in (c.get("CourseName") or "")), None)
    if not tgt:
        print("找不到课程：", a.course); return 2
    nm, clid, cid = tgt["CourseName"], str(tgt["TClassID"]), str(tgt["CourseID"])
    print("课程 %s  TClassID=%s  CourseID=%s  类型=%s" % (nm, clid, cid, a.type))

    before, _ = list_ci(clid)
    old = {str(x.get("ID")) for x in before}
    print("发起前该课已有 %d 场（仅已结束的场次，进行中的不会列出来）" % len(before))

    ciid = None
    code = ""
    try:
        # ── 1) 发起 ──
        u, r = create(clid, a.type, a.temps)
        print("发起 → HTTP %s（%d 秒有效）" % (r.status_code, a.temps))
        # 判定是否发起成功：看大屏拿到哪个场次。班次列表只列已结束的场次，
        # 进行中的那一场永远不在列表里（实测 10 秒场次要等它结束才出现），
        # 所以不能靠「列表里多了一行」来判断。
        b = None
        for i in range(6):
            b = d.CI.board(cid)
            if b: break
            time.sleep(1)
        if not b:
            print("!! 没发起成功：大屏还是没活动（%s）" % d.CI.board_why())
            return 3
        ciid = d._s(b.get("ciid"))
        code = d._s(b.get("key"))
        lb = d.CI.last_board or {}
        print("新场次 ID=%s  大屏 type=%s  码=%r  剩余=%s秒  拼音=%s"
              % (ciid, b.get("type"), code,
                 (lb.get("fields") or {}).get("HFSeconds"), b.get("pinyin") or "-"))
        if d._s(b.get("type")) != a.type:
            print("!! 大屏类型与发起时不一致，停手"); return 5
        if not code:
            print("!! 码是空的"); return 6

        # ── 2) 工具签到 ──
        ok, msg = d.CI.code(d.CI.uid(), code)
        print("工具提交 → %s  %s" % ("成功" if ok else "失败", msg))
        if not ok:
            return 7

        # ── 3) 校验出勤 ──
        rows = d.CI.fetch(clid) or []
        mine = next((x for x in rows if str(x.get("ID")) == ciid), None)
        if mine is None:
            print("!! 学生记录里没找到这场"); return 8
        print("学生记录 → 状态=%s  签到时间=%s  类型=%s"
              % (mine.get("StatusName"), mine.get("CheckInDate"), mine.get("CheckInType")))
        good = d._s(mine.get("StatusName")) in ("出勤", "迟到") or d.row_signed(mine)
        print("判定 →", "千真万确签上了" if good else "没签上（状态异常）")
        return 0 if good else 9
    finally:
        # ── 4) 删掉这场，并确认真的没有残留的进行中签到 ──
        if ciid:
            j = delete_ci(ciid)
            time.sleep(0.8)
            alive = d.CI.board(cid)
            print("删除 %s → %s  残留进行中: %s"
                  % (ciid, j, ("仍有：" + d._s(alive.get("ciid"))) if alive else "无"))


if __name__ == "__main__":
    sys.exit(main())
