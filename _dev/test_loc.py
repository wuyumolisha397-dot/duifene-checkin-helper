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
        return R(json.dumps({"msg": 1, "msgbox": "获取成功！", "data": [RAW_ROOM]}),
                 {"msg": 1, "msgbox": "获取成功！", "data": [RAW_ROOM]})
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

print("LOC FLOW OK")
