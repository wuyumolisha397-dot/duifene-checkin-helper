import sys, tkinter as tk
sys.path.insert(0, r"C:/Users/admin/WorkBuddy/2026-10-26-17-09-23")
import duifene_v3 as d
import os as _os; _os.environ["DUIFENE_LOG_DIR"] = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "_testlogs")

root = tk.Tk(); app = d.App(root)
root.update(); root.update_idletasks()
cv = app._lfr
sr = cv.cget("scrollregion")
print("window:", root.winfo_width(), "x", root.winfo_height())
print("canvas:", cv.winfo_width(), "x", cv.winfo_height())
print("scrollregion:", sr)
print("course card y:", app._cc.winfo_y(), " h:", app._cc.winfo_height())
print("combobox w:", app._co.winfo_width(), " h:", app._co.winfo_height(), " mapped:", bool(app._co.winfo_ismapped()))
print("start btn y:", app._sb.winfo_rooty() - root.winfo_rooty(), " h:", app._sb.winfo_height(),
      " visible:", app._sb.winfo_rooty() + app._sb.winfo_height() <= root.winfo_rooty() + root.winfo_height())
app._focus_course(); root.update()
print("yview after focus_course:", [round(v,3) for v in cv.yview()])
cv.yview_moveto(1.0); root.update()
print("yview at bottom:", [round(v,3) for v in cv.yview()])
assert app._co.winfo_width() > 120, "下拉框没有正常布局"
assert app._co.winfo_height() > 15, "下拉框高度异常"
assert app._sb.winfo_rooty() + app._sb.winfo_height() <= root.winfo_rooty() + root.winfo_height() + 1, "开始按钮被挤出窗口"
app._collapse_login(); root.update()
assert app._lcol, "收起登录卡失败"
bottom = app._cc.winfo_y() + app._cc.winfo_height()
assert bottom <= cv.winfo_height() + 2, f"课程卡超出可视区: {bottom} > {cv.winfo_height()}"
print("课程卡底部:", bottom, "视口高:", cv.winfo_height(), "→ 无需滚动即可见")
root.after(200, root.destroy); root.mainloop()
print("LAYOUT OK")
