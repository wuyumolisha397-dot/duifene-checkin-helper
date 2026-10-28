import sys, json
sys.path.insert(0, r"C:\Users\admin\WorkBuddy\2026-10-26-17-09-23")
import duifene_v3 as d
import os as _os; _os.environ["DUIFENE_LOG_DIR"] = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "_testlogs")

fails = []
def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + f"  got={got!r} want={want!r}")
    if not ok: fails.append(name)

class Resp:
    def __init__(self, payload=None, text=None, code=200):
        self._p = payload
        self.text = text if text is not None else (json.dumps(payload, ensure_ascii=False) if payload is not None else "")
        self.status_code = code
    def json(self):
        if self._p is None: raise ValueError("no json")
        return self._p

class FakeSession:
    def __init__(self, get_body="", post_payload=None):
        self.cookies = {}
        self.headers = {}
        self.get_body = get_body
        self.post_payload = post_payload
        self.calls = []
    def get(self, url, timeout=None, **kw):
        self.calls.append(("GET", url))
        return Resp(text=self.get_body)
    def post(self, url, data=None, headers=None, timeout=None, **kw):
        self.calls.append(("POST", url, data))
        return Resp(payload=self.post_payload, text=json.dumps(self.post_payload or {}, ensure_ascii=False))

print("--- 授权码失效 ---")
d.log._w = None
d.session = FakeSession(get_body="<script>window.location='/_UserCenter/MB/LostInfo.aspx'</script>")
r = d.Auth.wx("https://x.com/a?code=" + "a" * 32 + "&state=1")
check("LostInfo 判失败", r, False)

print("--- 授权码格式错误 ---")
d.session = FakeSession()
check("无 code 判失败", d.Auth.wx("https://x.com/a?state=1"), False)

print("--- 登录未生效 ---")
d.session = FakeSession(get_body="<html>ok</html>", post_payload={"msg": "0", "msgbox": "当前为退出"})
check("checklogin 不通判失败", d.Auth.wx("https://x.com/a?code=" + "b" * 32), False)

print("--- 课程接口 ---")
d.session = FakeSession(post_payload={"msg": "0", "msgbox": "禁止访问！"})
cs, err = d.Course.list()
check("禁止访问 → 错误透出", (cs, err), (None, "禁止访问！"))

d.session = FakeSession(post_payload=[{"CourseName": "高数", "TClassID": "1", "CourseID": "2"}])
cs, err = d.Course.list()
check("正常列表", (len(cs), err), (1, None))

d.session = FakeSession(post_payload={"msg": "1", "data": [{"CourseName": "A"}]})
cs, err = d.Course.list()
check("data 包裹", (len(cs), err), (1, None))

d.session = FakeSession(post_payload=[])
cs, err = d.Course.list()
check("空列表", (cs, err), ([], None))

class BadResp(FakeSession):
    def post(self, *a, **k): return Resp(text="<html>login</html>")
d.session = BadResp()
cs, err = d.Course.list()
check("非 JSON 提示", err.startswith("返回的不是 JSON"), True)

print()
print("FAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
