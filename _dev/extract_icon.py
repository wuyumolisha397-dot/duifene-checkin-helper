# extract_icon.py - 从 exe 里把 RT_GROUP_ICON/RT_ICON 抠出来还原成 .ico
# 用途：打包后确认图标真的写进 PE 资源了，而不是只在 spec 里写了 icon=
import struct
import sys

import pefile

RT_ICON, RT_GROUP_ICON = 3, 14


def _res(pe, rtype):
    """取某一类资源：{资源ID: 原始字节}（只取第一个语言项）"""
    out = {}
    if not hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
        return out
    for t in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if t.id != rtype:
            continue
        for e in t.directory.entries:
            lang = e.directory.entries[0]
            d = lang.data.struct
            out[e.id] = pe.get_data(d.OffsetToData, d.Size)
    return out


def extract(exe, out_ico):
    pe = pefile.PE(exe, fast_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
    )
    icons = _res(pe, RT_ICON)
    groups = _res(pe, RT_GROUP_ICON)
    if not groups:
        raise SystemExit("exe 里没有图标资源")

    g = groups[sorted(groups)[0]]
    _r, _t, cnt = struct.unpack_from("<HHH", g, 0)
    entries = []
    for i in range(cnt):
        w, h, cc, _rsv, planes, bpp, size, rid = struct.unpack_from("<BBBBHHIH", g, 6 + 14 * i)
        blob = icons.get(rid)
        if blob is None:
            raise SystemExit("找不到 RT_ICON id=%s" % rid)
        entries.append((w, h, cc, planes, bpp, size, blob))

    head = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    dirs, body = b"", b""
    for w, h, cc, planes, bpp, size, blob in entries:
        dirs += struct.pack("<BBBBHHII", w, h, cc, 0, planes, bpp, size, offset)
        body += blob
        offset += size
    with open(out_ico, "wb") as f:
        f.write(head + dirs + body)

    print("资源组数: %d  图标项: %d" % (len(groups), len(entries)))
    for w, h, _cc, _p, bpp, size, _b in entries:
        print("  %3dx%-3d %2dbpp %6d bytes" % (w or 256, h or 256, bpp, size))
    print("已还原 ->", out_ico)


if __name__ == "__main__":
    extract(sys.argv[1] if len(sys.argv) > 1 else "dist/duifene_v3.exe",
            sys.argv[2] if len(sys.argv) > 2 else "_dev/exe_icon.ico")
