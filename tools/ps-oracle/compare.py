#!/usr/bin/env python3
"""Diff Photoshop reference PNGs against photocraft-cli PNGs and write a report.

Usage: compare.py --manifest cases.json --ps PS_OUT --box BOX_OUT --report DIR [--tol 2]

Per output: sizes, max channel delta, mean delta, % pixels with any channel delta > tol
(8-bit units). Pixels are compared as PREMULTIPLIED RGBA (so colour under alpha=0 is
ignored, like crates/io/examples/oracle_diff.rs); gray/indexed are expanded to RGBA; ICC
profiles are ignored (raw values). Writes report/summary.md, report/summary.json and
report/diff/<case>__<output>.png (Photoshop | photocraft | heatmap).
"""
import argparse, json, os, sys
import numpy as np
from PIL import Image


def load(p):
    im = Image.open(p)
    if im.mode in ("I;16", "I;16B", "I"):
        a = np.asarray(im, dtype=np.float64) / 257.0
        a = np.stack([a, a, a, np.full_like(a, 255.0)], -1)
        return a
    return np.asarray(im.convert("RGBA"), dtype=np.float64)


def premul(a):
    out = a.copy()
    out[..., :3] = a[..., :3] * a[..., 3:4] / 255.0
    return out


def heat(ps, bx, d, path):
    """Side-by-side: PS | photocraft | heatmap (red = delta, scaled so 32+ is full red)."""
    h = max(ps.shape[0], bx.shape[0]); w = max(ps.shape[1], bx.shape[1])
    def pad(a):
        o = np.zeros((h, w, 4)); o[:a.shape[0], :a.shape[1]] = a; return o
    def over_checker(a):
        yy, xx = np.mgrid[0:h, 0:w]
        bg = np.where(((yy // 8 + xx // 8) % 2)[..., None] == 0, 204.0, 255.0)
        al = a[..., 3:4] / 255.0
        return a[..., :3] * al + bg * (1 - al)
    A, B = over_checker(pad(ps)), over_checker(pad(bx))
    if d is None:
        hm = np.zeros((h, w, 3)); hm[..., 0] = 255  # size mismatch: all red
    else:
        g = (A.mean(-1, keepdims=True) * 0.25)
        dd = np.zeros((h, w)); dd[:d.shape[0], :d.shape[1]] = d
        t = np.clip(dd / 32.0, 0, 1)[..., None]
        hm = g * (1 - t) + np.array([255.0, 0, 0]) * t
        hm[(dd > 0) & (dd <= 2)] = [255, 200, 0]  # within tolerance but not exact: amber
    gap = np.full((h, 4, 3), 128.0)
    img = np.concatenate([A, gap, B, gap, hm], 1)
    scale = max(1, min(8, 256 // max(h, w, 1)))
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    im.save(path)


def compare(ps_p, bx_p, tol, diff_path):
    r = {}
    ps, bx = load(ps_p), load(bx_p)
    r["psSize"] = [ps.shape[1], ps.shape[0]]
    r["boxSize"] = [bx.shape[1], bx.shape[0]]
    if ps.shape != bx.shape:
        r["status"] = "SIZE"
        heat(ps, bx, None, diff_path)
        return r
    d = np.abs(premul(ps) - premul(bx))
    dm = d.max(-1)
    r["max"] = round(float(d.max()), 2)
    r["mean"] = round(float(d.mean()), 4)
    r["pctOverTol"] = round(float((dm > tol + 1e-9).mean() * 100), 4)
    r["alphaMax"] = round(float(d[..., 3].max()), 2)
    r["status"] = "PASS" if r["max"] <= tol else ("NEAR" if r["pctOverTol"] <= 1.0 else "FAIL")
    if r["max"] > 0:
        heat(ps, bx, dm, diff_path)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--ps", required=True)
    ap.add_argument("--box", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--tol", type=float, default=2.0)
    a = ap.parse_args()
    man = json.load(open(a.manifest))
    def results(d):
        p = os.path.join(d, "results.json")
        if not os.path.exists(p):
            return {}, {}
        j = json.load(open(p))
        return {c["id"]: c for c in j.get("cases", [])}, j.get("env", {})
    psr, psenv = results(a.ps)
    bxr, bxenv = results(a.box)
    os.makedirs(os.path.join(a.report, "diff"), exist_ok=True)
    rows = []

    def err_of(res, cid, name):
        c = res.get(cid)
        if not c:
            return "case not run"
        for o in c.get("outputs", []):
            if o.get("name") == name and o.get("error"):
                return o["error"]
        return c.get("error") or "no file"

    for c in man["cases"]:
        cid = c["id"]
        pairs = []
        for o in c["outputs"]:
            if o["type"] == "slices":
                for s in o["slices"]:
                    nm = "slice_%02d" % s["number"]
                    pairs.append((nm, o["name"], f"{nm}.png"))
                # Save for Web extras, matched by file name
                psd_sfw = os.path.join(a.ps, cid, "sfw")
                if os.path.isdir(psd_sfw):
                    for f in sorted(os.listdir(psd_sfw)):
                        if f.lower().endswith(".png"):
                            pairs.append(("sfw/" + f[:-4], o["name"], "sfw/" + f))
            else:
                pairs.append((o["name"], o["name"], o["name"] + ".png"))
        for label, oname, rel in pairs:
            row = {"case": cid, "file": c["file"], "op": cid.split("__", 1)[1], "output": label}
            pp, bp = os.path.join(a.ps, cid, rel), os.path.join(a.box, cid, rel)
            if not os.path.exists(pp):
                row["status"] = "PS-MISSING"; row["error"] = err_of(psr, cid, oname)
            elif not os.path.exists(bp):
                row["status"] = "PC-MISSING"; row["error"] = err_of(bxr, cid, oname)
            else:
                try:
                    row.update(compare(pp, bp, a.tol, os.path.join(a.report, "diff", f"{cid}__{label.replace('/', '_')}.png")))
                except Exception as e:  # noqa: BLE001
                    row["status"] = "ERROR"; row["error"] = str(e)
            pc = psr.get(cid) or {}
            if pc.get("warnings"):
                row["psWarnings"] = pc["warnings"]
            if pc.get("missingFonts"):
                row["missingFonts"] = pc["missingFonts"]
            if pc.get("ops"):
                row["psOpMethods"] = [x.get("method") or x.get("error") for x in pc["ops"]]
            rows.append(row)

    order = {"FAIL": 0, "SIZE": 1, "ERROR": 2, "PC-MISSING": 3, "PS-MISSING": 4, "NEAR": 5, "PASS": 6}
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    by_op = {}
    for r in rows:
        k = r["op"].rstrip("0123456789")
        b = by_op.setdefault(k, {})
        b[r["status"]] = b.get(r["status"], 0) + 1
    summary = {"tolerance": a.tol, "metric": "premultiplied RGBA, 8-bit units", "counts": counts, "byOp": by_op,
               "photoshop": psenv, "photocraft": bxenv, "rows": rows}
    json.dump(summary, open(os.path.join(a.report, "summary.json"), "w"), indent=1)

    L = ["# photocraft-cli vs Adobe Photoshop", ""]
    L.append(f"Photoshop: {psenv.get('version', '?')} ({psenv.get('os', '')}), colour settings: {psenv.get('colorSettings', '?')}  ")
    L.append(f"photocraft: {bxenv.get('version', '?')}  ")
    L.append(f"Metric: premultiplied RGBA, 8-bit units; PASS = max delta <= {a.tol:g}; NEAR = <= 1% of pixels over {a.tol:g}; FAIL otherwise; SIZE = dimensions differ.")
    L.append("")
    L.append("**Totals:** " + ", ".join(f"{k} {counts[k]}" for k in sorted(counts, key=lambda k: order.get(k, 9))))
    L.append("")
    L.append("| op | " + " | ".join(sorted({s for b in by_op.values() for s in b}, key=lambda k: order.get(k, 9))) + " |")
    sts = sorted({s for b in by_op.values() for s in b}, key=lambda k: order.get(k, 9))
    L[-1] = "| op | " + " | ".join(sts) + " |"
    L.append("|" + "---|" * (len(sts) + 1))
    for k in sorted(by_op):
        L.append(f"| {k} | " + " | ".join(str(by_op[k].get(s, "")) for s in sts) + " |")
    L.append("")
    L.append("| status | case | output | max | mean | %>tol | PS size | PC size | notes |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (order.get(r["status"], 9), -(r.get("pctOverTol") or 0), r["case"])):
        notes = []
        if r.get("error"): notes.append(r["error"][:160].replace("|", "/").replace("\n", " "))
        if r.get("missingFonts"): notes.append("missing fonts: " + ", ".join(r["missingFonts"]))
        if r.get("psWarnings"): notes.append("PS: " + "; ".join(r["psWarnings"])[:160])
        sz = lambda v: f"{v[0]}x{v[1]}" if v else ""
        L.append(f"| {r['status']} | {r['case']} | {r['output']} | {r.get('max', '')} | {r.get('mean', '')} | {r.get('pctOverTol', '')} | "
                 f"{sz(r.get('psSize'))} | {sz(r.get('boxSize'))} | {' '.join(notes)} |")
    open(os.path.join(a.report, "summary.md"), "w").write("\n".join(L) + "\n")
    print("totals:", counts, "->", os.path.join(a.report, "summary.md"))


if __name__ == "__main__":
    main()
