"""教师页大屏通道：二维码 / 汉字 / 数字码 都能从 TeachCheckIn.aspx 自动取到。"""
import sys, io, json, base64
sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
import duifene_v3 as d
import qrcode

# ── 造一张真实二维码图片（base64 JPG），内容与老师大屏一致 ──
TOKEN = "Kq7vR2mXpL9aT4nZ"
qr_content = f"https://www.duifene.com/_CheckIn/MB/QrCodeCheckOK.aspx?state={TOKEN}"
img = qrcode.make(qr_content)
buf = io.BytesIO()
img.convert("RGB").save(buf, "JPEG", quality=90)
QR_B64 = base64.b64encode(buf.getvalue()).decode()


def board_html(t, key, pinyin=""):
    return (f'<html><body>'
            f'<input type="hidden" name="HFChecktype" id="HFChecktype" value="{t}" />'
            f'<input type="hidden" name="HFCheckInID" id="HFCheckInID" value="2549000" />'
            f'<input type="hidden" name="HFCheckCodeKey" id="HFCheckCodeKey" value="{key}" />'
            f'<input type="hidden" name="HFCheckCodePinyin" id="HFCheckCodePinyin" value="{pinyin}" />'
            f'</body></html>')


class R:
    status_code = 200
    def __init__(s, text="", payload=None):
        s.text = text
        s._p = payload
    def json(s):
        return s._p if s._p is not None else {}


STATE = {"board": None, "posts": [], "gets": []}
def fake_post(url, data=None, headers=None, timeout=None, **kw):
    if "TeachCheckIn.aspx" in url:
        return R(STATE["board"] or "")
    STATE["posts"].append((url, data))
    return R(json.dumps({"msg": 1, "msgbox": "签到成功"}), {"msg": 1, "msgbox": "签到成功"})
def fake_get(url, **kw):
    STATE["gets"].append(url)
    return R('<html><div id="DivOK">签到成功</div></html>')

d.session.post = fake_post
d.session.get = fake_get
d.CI._uid = "1000001"
loop = d.Loop()
loop._cid = "300001"; loop.ci = "505019"; loop.cn = "测试课程"

# ── 1) 二维码：教师页给 base64 图片 → 识别 → 带 state 命中 QrCodeCheckOK ──
STATE["board"] = board_html("2", QR_B64)
b = d.CI.board("300001")
assert b and b["type"] == "2" and len(b["key"]) > 100, "没解析出二维码 base64"
print("board type=2, key 长度", len(b["key"]))

txt = d.CI.qr_image_text(QR_B64)
assert txt == qr_content, f"二维码识别不对: {txt!r}"
print("二维码图片识别 OK ->", txt[-32:])

ok, msg = d.Loop._do(loop, "2", {})
assert ok, f"二维码签到失败: {msg}"
assert STATE["gets"] and f"state={TOKEN}" in STATE["gets"][-1], STATE["gets"][-1:]
print("二维码自动签到 OK ->", STATE["gets"][-1][-46:])

# ── 2) 汉字：大屏顺序直接提交 ──
STATE["board"] = board_html("4", "春风化雨", "chun,chun,hua,yu")
ok, msg = d.Loop._do(loop, "4", {})
assert ok and "checkincode=春风化雨" in STATE["posts"][-1][1], STATE["posts"][-1]
print("汉字自动签到 OK ->", STATE["posts"][-1][1])

# ── 3) 数字码：服务端没下发时从大屏取 ──
STATE["board"] = board_html("1", "4821")
ok, msg = d.Loop._do(loop, "1", {})
assert ok and "checkincode=4821" in STATE["posts"][-1][1], STATE["posts"][-1]
print("数字码自动签到 OK ->", STATE["posts"][-1][1])

# ── 4) 教师页没有活动 → 明确失败（不再转人工），且不误判成成功 ──
STATE["board"] = board_html("0", "")
ok, msg = d.Loop._do(loop, "2", {})
assert not ok, msg
assert "接口没返回图片数据" in msg or "没有" in msg, msg
print("无活动 -> 明确失败 OK:", msg[:40])

# _exec 失败后应进入 2 秒重试节奏，而不是停手
loop._tried = {}
loop._exec("2", {"ID": "777"}, "777")
assert loop._tried["777"]["count"] == 1
assert loop._tried["777"]["next"] > 0, loop._tried
assert "human" not in loop._tried["777"], loop._tried["777"]
print("_exec 失败后按间隔重试 OK:", loop._tried["777"])

# ── 5) 大屏类型对不上（活动是定位，但这条是二维码）→ 说明原因，可重试 ──
STATE["board"] = board_html("3", "")
ok, msg = d.Loop._do(loop, "2", {})
assert not ok and "定位" in msg, msg
print("类型不符 OK:", msg[:46])

# ── 6) 空 key / 脏 base64 不能崩；图没准备好属于可重试 ──
STATE["board"] = board_html("2", "not-a-real-image")
ok, msg = d.Loop._do(loop, "2", {})
assert not ok and ("识别失败" in msg or "图片" in msg), msg
print("坏图 -> 可重试 OK:", msg[:40])

print("BOARD FLOW OK")
