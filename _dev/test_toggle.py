import sys, tkinter as tk
sys.path.insert(0, r"C:/Users/admin/WorkBuddy/2026-10-26-17-09-23")
import duifene_v3 as d
root = tk.Tk(); app = d.App(root)
# 不依赖真实登录态：先把登录卡收起来，再测展开/收起
app._collapse_login(); root.update()
print("初始收起:", app._lcol, "按钮:", app._ltg.cget("text"))
app._toggle_login(); root.update()
print("展开后:", app._lcol, "按钮:", app._ltg.cget("text"), "登录卡可见:", bool(app._lbody.winfo_ismapped()))
assert app._lcol is False and bool(app._lbody.winfo_ismapped())
app._toggle_login(); root.update()
print("再收起:", app._lcol, "按钮:", app._ltg.cget("text"), "登录卡可见:", bool(app._lbody.winfo_ismapped()))
assert app._lcol is True and not app._lbody.winfo_ismapped()
app._collapse_login(); app._collapse_login(); root.update()
print("重复收起不炸:", app._lcol)
app._expand_login(); app._expand_login(); root.update()
print("重复展开不炸:", app._lcol, bool(app._lbody.winfo_ismapped()))
root.after(150, root.destroy); root.mainloop()
print("TOGGLE OK")
