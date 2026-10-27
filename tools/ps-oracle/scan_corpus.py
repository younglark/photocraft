#!/usr/bin/env python3
"""Scan PSD/PSB files: who wrote them (version-info 1057, XMP CreatorTool 1060),
slices (1050), colour profile (1039), mode/depth, plus the layer kinds photocraft-cli sees.

Usage: scan_corpus.py --cli PATH [--root DIR ...] > scan.json
"""
import argparse, json, os, re, struct, subprocess, sys

MODES = {0: "Bitmap", 1: "Grayscale", 2: "Indexed", 3: "RGB", 4: "CMYK", 7: "Multichannel", 8: "Duotone", 9: "Lab"}


def read_unicode(b, o):
    n = struct.unpack_from(">I", b, o)[0]
    o += 4
    s = b[o:o + 2 * n].decode("utf-16-be", "replace").rstrip("\0")
    return s, o + 2 * n


def resources(path):
    out = {"writer": None, "reader": None, "creatorTool": None, "hasSlices": False, "sliceCount": 0,
           "iccProfile": None, "mode": None, "depth": None, "width": None, "height": None, "psb": False}
    with open(path, "rb") as f:
        hdr = f.read(26)
        if hdr[:4] != b"8BPS":
            return out
        ver, = struct.unpack_from(">H", hdr, 4)
        out["psb"] = ver == 2
        ch, h, w, depth, mode = struct.unpack_from(">HIIHH", hdr, 12)
        out.update(mode=MODES.get(mode, mode), depth=depth, width=w, height=h)
        cm_len, = struct.unpack(">I", f.read(4))
        f.seek(cm_len, 1)
        ir_len, = struct.unpack(">I", f.read(4))
        b = f.read(ir_len)
    o = 0
    while o + 12 <= len(b):
        if b[o:o + 4] not in (b"8BIM", b"MeSa", b"AgHg", b"PHUT", b"DCSR"):
            break
        rid, = struct.unpack_from(">H", b, o + 4)
        nlen = b[o + 6]
        o += 7 + nlen
        if (nlen + 1) % 2:
            o += 1
        size, = struct.unpack_from(">I", b, o)
        o += 4
        data = b[o:o + size]
        o += size + (size & 1)
        try:
            if rid == 1057 and len(data) > 9:
                p = 4 + 1
                writer, p = read_unicode(data, p)
                reader, p = read_unicode(data, p)
                out["writer"], out["reader"] = writer, reader
            elif rid == 1060:
                m = re.search(rb"CreatorTool(?:>|=\")([^<\"]+)", data)
                if m:
                    out["creatorTool"] = m.group(1).decode("utf-8", "replace")
            elif rid == 1050:
                v, = struct.unpack_from(">I", data, 0)
                if v == 6:
                    p = 4 + 16
                    _, p = read_unicode(data, p)
                    n, = struct.unpack_from(">I", data, p)
                    out["sliceCount"] = n
                    out["hasSlices"] = n > 0
                else:
                    out["sliceCount"] = -1  # descriptor-based (v7/8): present, count unknown here
                    out["hasSlices"] = True
            elif rid == 1039:
                # ICC desc tag
                m = re.search(rb"desc\0\0\0\0\0\0\0.(.{1,64}?)\0", data, re.S)
                out["iccProfile"] = m.group(1).decode("latin-1") if m else "embedded"
        except Exception:
            pass
    return out


def walk(layers, prefix=""):
    for l in layers:
        p = prefix + l["name"]
        yield p, l
        if l.get("children"):
            yield from walk(l["children"], p + "/")


def kinds(cli, path):
    try:
        r = subprocess.run([cli, "info", "--compact", path], capture_output=True, timeout=60)
        if r.returncode:
            return {"error": r.stderr.decode()[:200]}
        d = json.loads(r.stdout)
    except Exception as e:
        return {"error": str(e)}
    feats = set()
    ks = {}
    for p, l in walk(d.get("layers", [])):
        k = l.get("kind")
        ks[k] = ks.get(k, 0) + 1
        if l.get("hasMask"): feats.add("mask")
        if l.get("hasVectorMask"): feats.add("vectorMask")
        if l.get("clipped"): feats.add("clipping")
        if l.get("blend") not in (None, "Normal", "PassThrough", "Pass Through"): feats.add("blend:" + str(l.get("blend")))
        if not l.get("visible", True): feats.add("hiddenLayer")
        if l.get("effects") or l.get("hasEffects") or l.get("style"): feats.add("effects")
    return {"kinds": ks, "features": sorted(feats), "layerCount": sum(ks.values())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", required=True)
    ap.add_argument("--root", action="append", required=True)
    a = ap.parse_args()
    rows = []
    for root in a.root:
        for dp, _, fs in os.walk(root):
            for fn in sorted(fs):
                if not fn.lower().endswith((".psd", ".psb")):
                    continue
                p = os.path.join(dp, fn)
                r = {"path": p, "bytes": os.path.getsize(p)}
                r.update(resources(p))
                w = (r.get("writer") or "") + " " + (r.get("creatorTool") or "")
                r["photoshopSaved"] = "Photoshop" in w
                r.update(kinds(a.cli, p))
                rows.append(r)
    json.dump(rows, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
