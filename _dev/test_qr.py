"""二维码通道：令牌映射、取图快慢通道、state 入口、各种结论文案。

核心回归点：
  1. G.aspx 绝不能直接请求（它会 302 到微信 oauth，跟过去只有授权页，签不到）
  2. 令牌要映射成 /_CheckIn/MB/QrCodeCheckOK.aspx?state=<令牌>
  3. 「您已经签到成功！」也要算成功
  4. 拿不到最新图时退回大屏图
"""
import sys, io, json, base64, re
sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import duifene_v3 as d
import qrcode

TOKEN = "AAAABBBBCCCCDDDDEEEEFFFF000011112222333344445555"
# 真实二维码的内容形态（实测取样）
REAL = "https://www.duifene.com/G.aspx?OnlyId=2&e=" + TOKEN
DIRECT = f"https://www.duifene.com/_CheckIn/MB/QrCodeCheckOK.aspx?state={TOKEN}"
WX = ("https://open.weixin.qq.com/connect/oauth2/authorize?appid=wx1b5650884f657981"
      "&redirect_uri=https%3a%2f%2fwww.duifene.com%2f_CheckIn%2fMB%2f"
      "QrCodeCheckOK.aspx%3fstate%3d" + TOKEN +
      "&response_type=code&scope=snsapi_userinfo&state=1#wechat_redirect")


def qr_b64(content, fmt="JPEG"):
    img = qrcode.make(content)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, fmt, quality=90)
    return base64.b64encode(buf.getvalue()).decode()


# ── 1) 文本 → 目标地址：一律落到 state 入口 ──
for name, text in [
    ("真实 G.aspx", REAL),
    ("直接地址", DIRECT),
    ("微信外壳", WX),
    ("裸令牌", TOKEN),
]:
    u, why = d.parse_qr_target(text)
    assert u, f"{name}: 没解析出目标（{why}）"
    assert u == DIRECT, f"{name}: 期望 {DIRECT}，实际 {u}"
    assert "weixin.qq.com" not in u, f"{name}: 不该把微信域名当目标"
    assert "G.aspx" not in u, f"{name}: 不该把 G.aspx 当目标"
    print(f"{name:10} → state={u.split('state=')[1][:20]}…")

for bad in ("https://www.baidu.com/s?wd=1", ""):
    u, why = d.parse_qr_target(bad)
    assert u is None, f"{bad!r} 应被拒"
print("非对分易/空内容 → 拒绝 OK")

# ── 2) 解码：JPEG / PNG 都要认 ──
for fmt in ("JPEG", "PNG"):
    txt = d.CI.qr_image_text(qr_b64(REAL, fmt))
    assert txt == REAL, f"{fmt} 解码失败: {txt!r}"
print("解码 JPEG/PNG OK")


class R:
    status_code = 200
    history = []

    def __init__(s, text="", payload=None, url=""):
        s.text = text
        s._p = payload
        s.url = url

    def json(s):
        return s._p if s._p is not None else {}


STATE = {"board": "", "qr": None, "gets": [], "posts": [], "alive": set(),
         "page": "签到成功！", "tcode": ""}


def fake_post(url, data=None, headers=None, timeout=None, **kw):
    if "TeachCheckIn.aspx" in url:
        return R(STATE["board"])
    if "action=getcodeimage" in (data or ""):
        STATE["posts"].append(data)
        if STATE["qr"] is None:
            p = {"msg": -1, "msgbox": "签到已结束！"}
            return R(json.dumps(p), p)
        p = {"msg": 1, "data": STATE["qr"]}
        return R(json.dumps(p), p)
    STATE["posts"].append(data)
    return R('{"msg":1}', {"msg": 1, "msgbox": "签到成功"})


def fake_get(url, **kw):
    STATE["gets"].append(url)
    if "G.aspx" in url:
        # 真实行为：302 到微信 oauth，落地页 1033 字节且 DivOK 空
        return R("<html><body></body></html>",
                 url="https://open.weixin.qq.com/connect/oauth2/authorize")
    m = re.search(r"state=([0-9A-Za-z]+)", url)
    tok = m.group(1) if m else ""
    if not tok:
        return R('<div id="DivOK"><span>参数错误,请重新尝试</span></div>')
    if STATE["tcode"] == "jx":
        return R('<div id="DivOK"><span>无法解读此签到二维码， 请扫描最新的签到二维码。</span></div>')
    if tok not in STATE["alive"]:
        return R('<div id="DivOK"><span>此签到二维码已过期， 请扫描最新的签到二维码。</span></div>')
    return R(f'<div id="DivOK"><span>{STATE["page"]}</span></div>')


d.session.post = fake_post
d.session.get = fake_get
d.CI._uid = "1000001"


def board_html(t, key):
    return (f'<input type="hidden" id="HFChecktype" value="{t}" />'
            f'<input type="hidden" id="HFCheckInID" value="2548578" />'
            f'<input type="hidden" id="HFCheckCodeKey" value="{key}" />'
            f'<input type="hidden" id="HFCheckCodePinyin" value="" />')


def no_gasp():
    bad = [u for u in STATE["gets"] if "G.aspx" in u]
    assert not bad, f"绝不能请求 G.aspx（会跳微信 oauth）：{bad}"


# ── 3) 最快通道：一次取图就签上，且从不碰 G.aspx ──
STATE["alive"] = {TOKEN}
STATE["qr"] = qr_b64(REAL)
STATE["board"] = board_html("2", qr_b64(REAL))
STATE["gets"] = []
ok, msg = d.CI.qr_fast("300001")
assert ok, f"应签成功: {msg}"
assert STATE["gets"][-1] == DIRECT, f"实际请求: {STATE['gets'][-1]}"
no_gasp()
print("最快通道签成功 OK →", msg)

# ── 4) 「您已经签到成功！」也算成功（重放场景）──
STATE["page"] = "您已经签到成功！"
ok, msg = d.CI.qr_fast("300001")
assert ok and "此前" in msg, msg
no_gasp()
print("重复签到识别 OK →", msg)

# ── 5) 取图接口没图 → 退回大屏图，仍能签上 ──
STATE["page"] = "签到成功！"
OLD = "0123456789ABCDEF0123456789ABCDEF" + "OLDOLDOLDOLDOLD1"
BOARD_TOK = "0123456789ABCDEF0123456789ABCDEF" + "A1B2C3D4E5F60718"
STATE["qr"] = None
STATE["board"] = board_html("2", qr_b64("https://www.duifene.com/G.aspx?OnlyId=2&e=" + BOARD_TOK))
STATE["alive"] = {BOARD_TOK}
STATE["gets"] = []
ok, msg = d.CI.qr_fast("300001")
assert ok, f"兜底就该成功: {msg}"
assert BOARD_TOK in STATE["gets"][-1], STATE["gets"][-1]
no_gasp()
print("取图失败 → 大屏兜底成功 OK")

# ── 6) 令牌都过期 → 说人话，不能是「参数错误」这种糊话 ──
STATE["alive"] = set()
STATE["qr"] = qr_b64(REAL)
STATE["board"] = board_html("2", qr_b64(REAL))
ok, msg = d.CI.qr_fast("300001")
assert not ok and ("刷新" in msg or "过期" in msg), msg
no_gasp()
print("全过期提示 OK →", msg[:30])

# ── 7) t=jx「无法解读」要单独认出来（令牌已换新）──
STATE["alive"] = {TOKEN}
STATE["tcode"] = "jx"
ok, msg = d.CI.qr_link(REAL)
assert not ok and "解读" in msg, msg
STATE["tcode"] = ""
print("无法解读文案 OK →", msg[:26])

# ── 8) 大屏类型不是二维码 → 不瞎试 ──
STATE["qr"] = None
STATE["board"] = board_html("4", "春风化雨")
done, msg = d.CI.qr_fast("300001")
assert done is None and "汉字" in msg, msg
print("类型不符 OK →", msg)

# ── 9) 没有活动 → 交给上层转人工 ──
STATE["board"] = board_html("0", "")
done, msg = d.CI.qr_fast("300001")
assert done is None and ("没有进行中的签到" in msg or "结束" in msg), msg
print("无活动 OK →", msg)

# ── 10) 老的「过期链接」也要能判 ──
STATE["alive"] = set()
ok, msg = d.CI.qr_link("https://www.duifene.com/G.aspx?OnlyId=2&e=" + "0" * 48)
assert not ok and ("刷新" in msg or "过期" in msg), msg
print("过期链接判定 OK")

print("QR FLOW OK")
