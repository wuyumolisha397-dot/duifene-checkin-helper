"""定位签到的坐标来源优先级：教室坐标（服务端）> 人工校准缓存 > 兜底。"""
import sys, json
sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
import duifene_v3 as d

ROOM = {"roomid": "4345388632720912", "name": "1",
        "lng": "114.40061188", "lat": "22.70745659", "range": "150.0"}
# 服务端原始格式（getroom 返回的就是这套字段名）
RAW_ROOM = {"RoomID": 4345388632720912, "RoomName": "1",
            "Longitude": 114.40061188, "Latitude": 22.70745659, "Range": 150.0}

STATE = {"posts": [], "room_calls": 0, "room_ok": True}
MEM = {}


class R:
    status_code = 200
    def __init__(s, text="", payload=None):
        s.text = text
        s._p = payload
    def json(s):
        return s._p if s._p is not None else {}


def fake_post(url, data=None, headers=None, timeout=None, **kw):
    if "getroom" in (data or ""):
        STATE["room_calls"] += 1
        if not STATE["room_ok"]:
            return R(json.dumps({"msg": -1, "msgbox": "获取失败"}), {"msg": -1, "msgbox": "获取失败"})
        rooms = STATE.get("rooms") or [RAW_ROOM]
        payload = {"msg": 1, "msgbox": "获取成功！", "data": rooms}
        return R(json.dumps(payload), payload)
    STATE["posts"].append(data)
    return R(json.dumps({"msg": 1, "msgbox": "签到成功"}),
             {"msg": 1, "msgbox": "签到成功"})


d.session.post = fake_post
d.CI._uid = "1000001"
d.LocMgr.load = lambda: dict(MEM)
d.LocMgr.save = lambda v: MEM.update(v)

loop = d.Loop()
loop._cid = "300001"; loop.ci = "505011"; loop.cn = "测试课程"


def signin_xy():
    """取出最后一次 signin 提交里的经纬度（会被 ±0.000089 扰动）。"""
    data = STATE["posts"][-1]
    kv = dict(p.split("=", 1) for p in data.split("&") if "=" in p)
    return float(kv["longitude"]), float(kv["latitude"])


def near(v, t, tol=2e-4):
    return abs(float(v) - float(t)) < tol


# ── 1) 本地无缓存 → 用服务端教室坐标 ──
MEM.clear(); STATE["posts"].clear()
ok, msg = d.Loop._do(loop, "3", {})
x, y = signin_xy()
assert ok and near(x, ROOM["lng"]) and near(y, ROOM["lat"]), (ok, msg, x, y)
print(f"1 无缓存 → 教室坐标 OK: {x},{y} | {msg}")

# ── 2) 缓存只是 sync 预填的默认值 → 仍用教室坐标 ──
MEM["测试课程"] = {"lng": d.DEFAULT_LNG, "lat": d.DEFAULT_LAT, "note": ""}
STATE["posts"].clear()
ok, _ = d.Loop._do(loop, "3", {})
x, y = signin_xy()
assert ok and near(x, ROOM["lng"]) and near(y, ROOM["lat"]), (x, y)
print(f"2 默认值缓存 → 仍用教室坐标 OK: {x},{y}")

# ── 2b) 缓存里还留着旧版默认坐标（离教室 700 米）→ 必须忽略 ──
MEM["测试课程"] = {"lng": "114.39437", "lat": "22.70462", "note": ""}
STATE["posts"].clear()
ok, _ = d.Loop._do(loop, "3", {})
x, y = signin_xy()
assert ok and near(x, ROOM["lng"]) and near(y, ROOM["lat"]), (x, y)
print(f"2b 旧默认坐标被忽略 OK: {x},{y}")

# ── 3) 人工校准过的缓存 → 优先用它 ──
MEM["测试课程"] = {"lng": "114.5", "lat": "22.8", "note": "手工校准"}
STATE["posts"].clear()
ok, _ = d.Loop._do(loop, "3", {})
x, y = signin_xy()
assert ok and near(x, "114.5") and near(y, "22.8"), (x, y)
print(f"3 人工校准 → 用缓存 OK: {x},{y}")

# ── 4) 活动自带坐标 → 最高优先级 ──
STATE["posts"].clear()
ok, _ = d.Loop._do(loop, "3", {"Longitude": "114.31", "Latitude": "22.61"})
x, y = signin_xy()
assert ok and near(x, "114.31") and near(y, "22.61"), (x, y)
print(f"4 活动自带坐标 OK: {x},{y}")

# ── 5) getroom 失败 → 退回兜底坐标（仍是校区位置，不是旧的那个点） ──
MEM.clear(); d.CI._rooms.clear()
STATE["room_ok"] = False
STATE["posts"].clear()
ok, msg = d.Loop._do(loop, "3", {})
x, y = signin_xy()
assert ok and near(x, d.DEFAULT_LNG) and near(y, d.DEFAULT_LAT), (x, y)
assert near(d.DEFAULT_LNG, ROOM["lng"], 1e-6) and near(d.DEFAULT_LAT, ROOM["lat"], 1e-6)
print(f"5 取教室失败 → 兜底坐标 OK: {x},{y}")

# ── 6) 缓存命中：10 分钟内不重复请求 getroom ──
STATE["room_ok"] = True
d.CI._rooms.clear()
STATE["room_calls"] = 0
for _ in range(3):
    d.Loop._do(loop, "3", {})
assert STATE["room_calls"] == 1, STATE["room_calls"]
print(f"6 教室坐标缓存 OK: 3 次签到只请求 {STATE['room_calls']} 次")

# ── 7) 缓存过期后会重新请求 ──
old = d.CI._rooms["300001"]
d.CI._rooms["300001"] = (old[0], old[1] - d.ROOM_CACHE_S - 1)
d.Loop._do(loop, "3", {})
assert STATE["room_calls"] == 2, STATE["room_calls"]
print(f"7 缓存过期后重取 OK: {STATE['room_calls']} 次")

# ── 8) 落点只用教室附近 12 米内，绝不把「容差 150 米」当成提交范围 ──
MEM.clear(); d.CI._rooms.clear(); STATE["room_ok"] = True
far = []
for i in range(12):
    STATE["posts"].clear()
    d.Loop._do(loop, "3", {"ID": str(9000 + i)})
    x, y = signin_xy()
    far.append(d.loc_dist_m(ROOM["lng"], ROOM["lat"], x, y))
assert max(far) <= d.LOC_NEAR_M + 0.5, max(far)
assert min(far) >= d.LOC_NEAR_M * 0.25 - 0.5, min(far)
print(f"8 落点限制在教室 {min(far):.1f}~{max(far):.1f} 米 OK（容差 150 米只是上限）")

# ── 9) 同一次活动重试 → 坐标固定，不乱跳 ──
seen = []
for _ in range(4):
    STATE["posts"].clear()
    d.Loop._do(loop, "3", {"ID": "9100"})
    seen.append(signin_xy())
assert len(set(seen)) == 1, seen
print(f"9 同一活动 4 次重试坐标一致 OK: {seen[0]}")

# ── 10) 换活动才换位置 ──
a = d.loc_near(ROOM["lng"], ROOM["lat"], "A")
b = d.loc_near(ROOM["lng"], ROOM["lat"], "B")
assert a != b, (a, b)
print(f"10 不同活动位置不同 OK: {a} vs {b}")

# ── 11) 文本里的经纬度解析（粘贴地图坐标/链接） ──
CASES = [
    ("114.40061188,22.70745659", 114.40061188, 22.70745659),
    ("22.70745659,114.40061188", 114.40061188, 22.70745659),   # 顺序颠倒也要认
    ("114.40061 22.70745", 114.40061, 22.70745),
    ("https://uri.amap.com/marker?position=114.400611,22.707456&name=x", 114.400611, 22.707456),
]
for txt, wlng, wlat in CASES:
    glng, glat = d.parse_lnglat(txt)
    assert glng and glat, (txt, glng, glat)
    assert abs(float(glng) - wlng) < 1e-6 and abs(float(glat) - wlat) < 1e-6, (txt, glng, glat)
for txt in ("", "abc", "签到码 1234", "114.40"):
    assert d.parse_lnglat(txt) == ("", ""), (txt, d.parse_lnglat(txt))
print("11 坐标文本解析 OK（含地图链接、顺序颠倒、无效输入）")

# ── 12) 教师页把本次中心塞在 key 里时，优先于教室坐标 ──
MEM.clear(); d.CI._rooms.clear(); STATE["room_ok"] = True
_orig_board = d.CI.board
try:
    d.CI.board = lambda cid: {"type": "3", "key": "114.31000000,22.61000000", "roomid": "0"}
    STATE["posts"].clear()
    ok, _ = d.Loop._do(loop, "3", {"ID": "9200"})
    x, y = signin_xy()
    assert ok and near(x, "114.31") and near(y, "22.61"), (x, y)
    print("12 签到页给的中心优先 OK: %s,%s" % (x, y))
finally:
    d.CI.board = _orig_board

# ── 13) 圆心猜错时，下一次自动换下一个候选（不再对着同一点撞满次数）──
STATE["rooms"] = [
    {"RoomID": 4345388632720912, "RoomName": "往往",
     "Longitude": 114.39525, "Latitude": 22.70155, "Range": 150.0},
    {"RoomID": 900, "RoomName": "教学楼B",
     "Longitude": 114.39500, "Latitude": 22.70200, "Range": 150.0},
]
MEM.clear()
MEM["测试课程"] = {"ok_lng": "114.39000000", "ok_lat": "22.70000000",
                   "ok_at": d.datetime.now().strftime(d.DATE_FMT)}
d.CI._rooms.clear(); loop._bc = None
_ob = d.CI.board
d.CI.board = lambda cid: {}
try:
    # 按真实调用方式模拟：_exec 每轮把 count+1 再传进来，候选索引由 count 推出来
    seen = []
    for k in range(1, 4):
        d.Loop._do(loop, "3", {"ID": "9300"}, {"count": k, "next": 0})
        seen.append(loop._last_loc[:2])
    print("13 依次换圆心:", " -> ".join("%s,%s" % p for p in seen))
    assert len(set(seen)) == 3, seen                      # 三次三个不同点
    assert near(seen[0][0], "114.39"), seen[0]            # 第 1 次：上次成功点
    assert near(seen[1][0], "114.39525"), seen[1]         # 第 2 次：教室 往往
    assert near(seen[2][0], "114.39500"), seen[2]         # 第 3 次：教室 教学楼B
    print("13 圆心候选逐个换 OK（成功点 → 教室A → 教室B）")
finally:
    d.CI.board = _ob
    STATE.pop("rooms", None)

# ── 14) 同学坐标：广播抄来的点排最前，太远的广播直接丢 ──
d.Peer.clear()
assert d.Peer.add("114.396", "22.702", 8) is True, "合格坐标应入队"
assert d.Peer.add("114.5", "22.9", 5000) is False, "远点应被丢弃"
assert d.Peer.add("114.3965", "22.7015", 3) is True
pts = d.Peer.best()
assert len(pts) == 2 and abs(pts[0][0] - 114.3965) < 1e-9, pts
MEM.clear(); STATE["rooms"] = [RAW_ROOM]; d.CI._rooms.clear()
cands = d.loc_candidates("300001", "测试课程", {}, {})
assert cands[0][0] == "同学坐标", cands
assert cands[:2] == [("同学坐标", "114.3965", "22.7015", ""),
                     ("同学坐标", "114.396", "22.702", "")], cands
assert cands[2][0] == "教室 1", cands
print("14 同学坐标排第一、远点被丢弃 OK:", [c[0] for c in cands])
d.Peer.clear()
STATE.pop("rooms", None)

print("LOC FLOW OK")
