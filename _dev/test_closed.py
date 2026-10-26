"""验证"活动已关闭"判定：命中后必须停手，不得继续重试。"""
import sys, time
sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
import duifene_v3 as d

# ── 1. 服务端说活动已结束 → 标记完成、不再重试 ──
d.CI.done = set()
L = d.Loop()
L._tried = {}
L._cid = "300001"; L.ci = "505019"; L.cn = "测试"

closed_msg = "所在班级当前没有正在进行的签到，或者签到已结束！2026-10-26 18:00:32"
calls = []
d.Loop._do = lambda self, ct, p: (calls.append(ct), (False, closed_msg))[1]

L._exec("3", {"ID": "2548484"}, "2548484")
assert "2548484" in d.CI.done, "已关闭的活动必须被标记，否则会一直重试"
assert L._tried["2548484"]["count"] == 1
assert L._tried["2548484"].get("next", 0) == 0, "已关闭的活动不该排重试"
print("已关闭 -> 标记完成、不排重试  OK")

# 每种关闭文案都要认
for w in d.CLOSED_WORDS:
    d.CI.done = set()
    L._tried = {}
    d.Loop._do = lambda self, ct, p, w=w: (False, f"服务端提示：{w}")
    L._exec("1", {"ID": "x1", "CheckInCode": "1234"}, "x1")
    assert "x1" in d.CI.done, f"未识别关闭文案: {w}"
print(f"关闭文案 {len(d.CLOSED_WORDS)} 种全部识别  OK")

# ── 2. 可重试的失败（答案错 / 超范围）→ 继续重试 ──
d.CI.done = set()
L._tried = {}
d.Loop._do = lambda self, ct, p: (False, "签到未成功")
L._exec("1", {"ID": "99", "CheckInCode": "1234"}, "99")
assert "99" not in d.CI.done
assert L._tried["99"]["next"] > time.time(), "可重试错误应安排下次重试"
print("可重试错误 -> 安排重试  OK")

# ── 3. 重试间隔必须短于签到窗口（实测窗口 9~30 秒）──
assert d.RETRY_GAP_S <= 5, f"重试间隔 {d.RETRY_GAP_S}s 对 9 秒窗口没意义"
assert d.MAX_TRIES * d.RETRY_GAP_S <= 30 or d.RETRY_GAP_S <= 3
print(f"重试参数 {d.MAX_TRIES} 次 × {d.RETRY_GAP_S}s  OK")

# ── 4. 限流不重试，且不误判为关闭 ──
d.CI.done = set()
L._tried = {}
d.Loop._do = lambda self, ct, p: (False, f"服务端限流，静默 {d.COOLDOWN_S} 秒")
L._exec("1", {"ID": "77", "CheckInCode": "1234"}, "77")
assert "77" not in d.CI.done, "限流不该把活动标记为完成"
print("限流 -> 不标记、不误判  OK")

# ── 5. 轮询间隔解析：允许 0.5，不接受 0 ──
def parse(txt):
    try: return max(500, int(float(txt or "2") * 1000))
    except Exception: return d.POLL_BASE_MS

assert parse("0.5") == 500
assert parse("0") == 500
assert parse("3") == 3000
assert parse("abc") == d.POLL_BASE_MS
print("轮询间隔解析 0.5->500ms  OK")

print("\nCLOSED LOGIC OK")
