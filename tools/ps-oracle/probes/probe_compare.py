#!/usr/bin/env python3
"""Compare Photoshop's probe renders with photocraft's.

Usage: probe_compare.py --stage <probe bundle dir> --ps <probe-out> --cli <photocraft-cli> --report <dir> [--tol 2] [--filter RE]

Per probe (Photoshop output dir <ps>/<id>/ holds canvas.png and resaved.psd):
  own      photocraft renders the probe PSD it wrote (Track P only)            vs PS canvas.png
  resaved  photocraft renders Photoshop's re-saved PSD (both tracks)            vs PS canvas.png
  pscomp   the composite Photoshop stored in resaved.psd (psd-tools, if present) vs PS canvas.png
           (sanity: must be ~0, else the canvas export itself is off)
`own` ≠ `resaved` means the difference is in photocraft's PSD writer/reader, not its renderer.
Also: per-region stats for Hue/Sat (8 HSL bands, random, near-grey rows) and Photoshop's and
photocraft's pixel values at fixed sample points per family, so formulas can be fitted from
report/probes.json directly.  Writes report/summary.md, report/probes.json, report/diff/*.png.
"""
import argparse, json, os, re, subprocess, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from compare import compare, load  # noqa: E402

SAMPLES = {
    "clip": {"baseOnly": (60, 60), "overlap": (130, 130), "clippedOutsideBase": (200, 200), "baseEdgeOutside": (35, 80), "strokeBand": (42, 80)},
    "shape": {"edgeL": (60, 110), "edgeL-1": (59, 110), "edgeL+1": (61, 110), "edgeT": (110, 60), "corner": (60, 60)},
    "pattern": {"p0": (40, 30), "p1": (41, 30), "p2": (100, 77), "p3": (180, 120)},
    "gradient": {"c": (128, 128), "l": (10, 128), "r": (245, 128), "t": (128, 10), "b": (128, 245)},
    "misc": {"c": (128, 128), "edge": (60, 80), "out": (50, 50)},
    "huesat": {},
}
HS_REGIONS = [(f"band{i}", i * 16, i * 16 + 16) for i in range(8)] + [("random", 128, 192), ("nearGrey", 192, 256)]


def render(cli, psd, png):
    r = subprocess.run([cli, "run", psd, "--out", png], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[-300:])


def stats(ps, bx, tol, rows=None):
    if rows:
        ps, bx = ps[rows[0]:rows[1]], bx[rows[0]:rows[1]]
    pm = lambda a: np.concatenate([a[..., :3] * a[..., 3:4] / 255.0, a[..., 3:4]], -1)
    d = np.abs(pm(ps) - pm(bx)).max(-1)
    return {"max": round(float(d.max()), 2), "mean": round(float(d.mean()), 3), "pctOverTol": round(float((d > tol).mean() * 100), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--ps", required=True)
    ap.add_argument("--cli", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--tol", type=float, default=2)
    ap.add_argument("--filter")
    a = ap.parse_args()
    man = json.load(open(os.path.join(a.stage, "cases.json")))
    psres = {}
    rj = os.path.join(a.ps, "results.json")
    if os.path.exists(rj):
        for c in json.load(open(rj))["cases"]:
            psres[c["id"]] = c
    os.makedirs(os.path.join(a.report, "diff"), exist_ok=True)
    rend = os.path.join(a.report, "render")
    os.makedirs(rend, exist_ok=True)
    out = []
    for c in man["cases"]:
        if a.filter and not re.search(a.filter, c["id"]):
            continue
        e = {"id": c["id"], "family": c["family"], "track": c["track"], "vary": c["vary"]}
        out.append(e)
        pr = psres.get(c["id"])
        if pr is not None and not pr.get("ok"):
            e["status"] = "PS-ERROR"; e["error"] = pr.get("error"); continue
        canvas = os.path.join(a.ps, c["id"], "canvas.png")
        resaved = os.path.join(a.ps, c["id"], "resaved.psd")
        if not os.path.exists(canvas):
            e["status"] = "PS-MISSING"; continue
        ps = load(canvas)
        e["ps"] = {k: [int(v) for v in ps[y, x]] for k, (x, y) in SAMPLES[c["family"]].items() if y < ps.shape[0] and x < ps.shape[1]}
        variants = []
        if c["track"] == "P":
            variants.append(("own", os.path.join(a.stage, c["psd"])))
        if os.path.exists(resaved):
            variants.append(("resaved", resaved))
        for name, src in variants:
            png = os.path.join(rend, f"{c['id']}__{name}.png")
            try:
                render(a.cli, src, png)
            except Exception as ex:  # noqa: BLE001
                e[name] = {"status": "BOX-ERROR", "error": str(ex)}; continue
            r = compare(canvas, png, a.tol, os.path.join(a.report, "diff", f"{c['id']}__{name}.png"))
            if r["status"] != "SIZE":
                bx = load(png)
                r["box"] = {k: [int(v) for v in bx[y, x]] for k, (x, y) in SAMPLES[c["family"]].items() if y < bx.shape[0] and x < bx.shape[1]}
                if c["family"] == "huesat":
                    r["regions"] = {n: stats(ps, bx, a.tol, (y0, y1)) for n, y0, y1 in HS_REGIONS}
            e[name] = r
        if os.path.exists(resaved):
            try:
                from psd_tools import PSDImage
                comp = os.path.join(rend, f"{c['id']}__pscomp.png")
                PSDImage.open(resaved).topil().save(comp)  # the stored (maximize-compatibility) composite
                e["pscomp"] = compare(canvas, comp, a.tol, os.path.join(a.report, "diff", f"{c['id']}__pscomp.png"))
            except Exception as ex:  # noqa: BLE001
                e["pscomp"] = {"status": "n/a", "error": str(ex)[:200]}
        main_v = e.get("own") or e.get("resaved") or {}
        e["status"] = main_v.get("status", "BOX-ERROR")
    json.dump(out, open(os.path.join(a.report, "probes.json"), "w"), indent=1)
    # summary
    L = ["# Photoshop probe comparison", "", f"tolerance {a.tol} (8-bit, premultiplied RGBA)", "",
         "| family | track | probes | PASS | NEAR | FAIL | SIZE | errors | own≠resaved |", "|---|---|---|---|---|---|---|---|---|"]
    fams = sorted({(e["family"], e["track"]) for e in out})
    for f, t in fams:
        es = [e for e in out if e["family"] == f and e["track"] == t]
        cnt = lambda s: sum(1 for e in es if e.get("status") == s)
        diffw = sum(1 for e in es if "own" in e and "resaved" in e and e["own"].get("max") != e["resaved"].get("max"))
        L.append(f"| {f} | {t} | {len(es)} | {cnt('PASS')} | {cnt('NEAR')} | {cnt('FAIL')} | {cnt('SIZE')} | {sum(1 for e in es if e.get('status') not in ('PASS','NEAR','FAIL','SIZE'))} | {diffw} |")
    L += ["", "## per probe", "", "| probe | vary | status | own max / %>tol | resaved max / %>tol | PS samples |", "|---|---|---|---|---|---|"]
    for e in out:
        fmt = lambda v: "–" if not v else (v.get("status") if "max" not in v else f"{v['max']} / {v['pctOverTol']}")
        L.append(f"| {e['id']} | `{json.dumps(e['vary'], separators=(',', ':'))}` | {e.get('status')} | {fmt(e.get('own'))} | {fmt(e.get('resaved'))} | {json.dumps(e.get('ps', {}), separators=(',', ':')) if e['family'] in ('clip',) else ''} |")
    open(os.path.join(a.report, "summary.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L[:6 + len(fams)]))
    print("report:", a.report)


if __name__ == "__main__":
    main()
