#!/usr/bin/env python3
"""Box-side runner: perform every cases.json case with photocraft-cli, writing PNGs with the
same names/layout as the Photoshop runner (<out>/<case id>/<output name>.png) + results.json.

Usage: run_photocraft.py --cli PATH --bundle DIR --out DIR [--filter REGEX] [--jobs N] [--dump-info DIR]

Layer paths are resolved to photocraft layer ids from `photocraft-cli info` of the source PSD
(ids are assigned deterministically on open). Colour: convertToSrgb=false, no ICC, raw values.
"""
import argparse, concurrent.futures as cf, json, os, re, shutil, subprocess, sys, tempfile, time


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def resolve(info, path):
    layers, layer = info["layers"], None
    for step in path:
        cand = None
        if step["index"] < len(layers) and layers[step["index"]]["name"] == step["name"]:
            cand = layers[step["index"]]
        else:
            cand = next((l for l in layers if l["name"] == step["name"]), None)
        if cand is None:
            raise KeyError("layer not found: " + "/".join(s["name"] for s in path))
        layer = cand
        layers = cand.get("children") or []
    return layer["id"]


def op_cmds(info, op):
    lid = resolve(info, op["path"])
    if op["op"] in ("hide", "show"):
        return [("layer.setProps", {"layer": lid, "visible": op["op"] == "show"})]
    if op["op"] == "move":
        return [("layer.moveTo", {"layer": lid, "target": resolve(info, op["target"]), "position": op["position"]})]
    if op["op"] == "rasterize":
        return [("layer.rasterize." + op["what"], {"layer": lid})]
    raise ValueError("unknown op " + op["op"])


def png_size(p):
    with open(p, "rb") as f:
        h = f.read(24)
    return int.from_bytes(h[16:20], "big"), int.from_bytes(h[20:24], "big")


class Runner:
    def __init__(self, cli, bundle, out):
        self.cli, self.bundle, self.out = cli, bundle, out
        self.infos = {}
        cmds = json.loads(sh([cli, "commands", "--json"]).stdout)
        cmds = cmds if isinstance(cmds, list) else cmds.get("commands", cmds)
        qe = next(c for c in cmds if c["id"] == "layer.quickExportAsPng")
        self.has_trim = '"trim"' in qe["params"]

    def info(self, psd):
        if psd not in self.infos:
            r = sh([self.cli, "info", "--compact", os.path.join(self.bundle, psd)])
            if r.returncode:
                raise RuntimeError("info failed: " + r.stderr.strip())
            self.infos[psd] = json.loads(r.stdout)
        return self.infos[psd]

    def run(self, psd, cmds, out=None):
        argv = [self.cli, "run", os.path.join(self.bundle, psd)]
        for cid, params in cmds:
            argv += ["--cmd", cid, "--params", json.dumps(params)]
        if out:
            argv += ["--out", out]
        if len(argv) == 3:
            return ""
        r = sh(argv)
        if r.returncode:
            raise RuntimeError(" ".join(a if len(a) < 80 else a[:77] + "..." for a in argv[3:]) + " -> " + (r.stderr.strip() or r.stdout.strip())[-800:])
        return r.stdout

    def case(self, c):
        t0 = time.time()
        res = {"id": c["id"], "psd": c["psd"], "ok": False, "outputs": [], "warnings": []}
        d = os.path.join(self.out, c["id"])
        os.makedirs(d, exist_ok=True)
        try:
            info = self.info(c["psd"])
            W, H = info["width"], info["height"]
            ops = [x for o in c["ops"] for x in op_cmds(info, o)]
            main, canvas_out, later = list(ops), None, []
            for spec in c["outputs"]:
                r = {"name": spec["name"], "type": spec["type"], "ok": False}
                res["outputs"].append(r)
                p = os.path.join(d, spec["name"] + ".png")
                if spec["type"] == "canvas":
                    canvas_out = p
                    r["method"] = "run --out (flattened composite)"
                elif spec["type"] == "layer":
                    lid = resolve(info, spec["path"])
                    if spec["trim"]:
                        main.append(("layer.quickExportAsPng", {"layer": lid, "path": p}))
                        r["method"] = "layer.quickExportAsPng"
                    elif self.has_trim:
                        main.append(("layer.quickExportAsPng", {"layer": lid, "path": p, "trim": False}))
                        r["method"] = "layer.quickExportAsPng trim=false"
                    else:
                        # this build's Quick Export always trims: Duplicate Layer > New document, save it.
                        later.append((r, ops + [("layer.duplicate", {"layer": lid, "document": "new"})], p))
                        r["method"] = "layer.duplicate document=new + run --out (no quickExport trim param in this build)"
                elif spec["type"] == "slices":
                    later.append((r, spec, d))
                    r["method"] = "file.export.saveForWebLegacy per slice number (png24, convertToSrgb=false)"
            self.run(c["psd"], main, canvas_out)
            for r in res["outputs"]:
                if r["type"] != "slices" and r["method"].startswith(("run --out", "layer.quickExport")):
                    r["ok"] = True
            for r, what, p in later:
                try:
                    if r["type"] == "slices":
                        self.slices(c, ops, what, p, r)
                    else:
                        self.run(c["psd"], what, p)
                        r["ok"] = True
                except Exception as e:  # noqa: BLE001
                    r["error"] = str(e)
            for r in res["outputs"]:
                f = os.path.join(d, r["name"] + ".png")
                if r["ok"] and r["type"] != "slices":
                    if not os.path.exists(f):
                        r["ok"], r["error"] = False, "no file written"
                    else:
                        r["width"], r["height"] = png_size(f)
            res["ok"] = all(r["ok"] for r in res["outputs"])
            if not res["ok"]:
                res["error"] = "; ".join(r.get("error", "") for r in res["outputs"] if not r["ok"]) or "some outputs failed"
        except Exception as e:  # noqa: BLE001
            res["error"] = str(e)
            for r in res["outputs"]:
                if not r["ok"]:
                    r.setdefault("error", "case failed")
        res["ms"] = int((time.time() - t0) * 1000)
        return res

    def slices(self, c, ops, spec, d, r):
        r["files"] = []
        tmp = tempfile.mkdtemp(prefix="pso-sl-")
        try:
            for s in spec["slices"]:
                sd = os.path.join(tmp, str(s["number"]))
                self.run(c["psd"], ops + [("file.export.saveForWebLegacy", {
                    "dir": sd, "format": "png24", "convertToSrgb": False, "metadata": "none", "embedIcc": False,
                    "transparency": True, "slices": "user", "numbers": [s["number"]]})])
                files = [os.path.join(dp, f) for dp, _, fs in os.walk(sd) for f in fs if f.endswith(".png")]
                if len(files) != 1:
                    raise RuntimeError(f"slice {s['number']}: expected 1 file, got {files}")
                nm = "slice_%02d.png" % s["number"]
                shutil.move(files[0], os.path.join(d, nm))
                r["files"].append(nm)
            # Whole Save for Web slice set, named by the exporter (compared with PS's sfw/ by file name).
            self.run(c["psd"], ops + [("file.export.saveForWebLegacy", {
                "dir": os.path.join(tmp, "all"), "format": "png24", "convertToSrgb": False, "metadata": "none",
                "embedIcc": False, "transparency": True, "slices": "all"})])
            sfw = os.path.join(d, "sfw")
            os.makedirs(sfw, exist_ok=True)
            r["sfw"] = []
            for dp, _, fs in os.walk(os.path.join(tmp, "all")):
                for f in fs:
                    if f.endswith(".png"):
                        shutil.move(os.path.join(dp, f), os.path.join(sfw, f))
                        r["sfw"].append(f)
            r["ok"] = True
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", required=True)
    ap.add_argument("--bundle", required=True, help="dir with cases.json and psds/")
    ap.add_argument("--out", required=True)
    ap.add_argument("--filter")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--dump-info", help="also write photocraft info JSON per file id here (for the jsx dry run)")
    a = ap.parse_args()
    man = json.load(open(os.path.join(a.bundle, "cases.json")))
    os.makedirs(a.out, exist_ok=True)
    rn = Runner(os.path.abspath(a.cli), os.path.abspath(a.bundle), os.path.abspath(a.out))
    cases = [c for c in man["cases"] if not a.filter or re.search(a.filter, c["id"])]
    for c in cases:  # warm the info cache serially (cheap) so workers don't race
        try:
            rn.info(c["psd"])
        except Exception as e:  # noqa: BLE001
            print("info failed", c["psd"], e, file=sys.stderr)
    if a.dump_info:
        os.makedirs(a.dump_info, exist_ok=True)
        for f in man["files"]:
            if f["psd"] in rn.infos:
                json.dump(rn.infos[f["psd"]], open(os.path.join(a.dump_info, f["id"] + ".json"), "w"))
    t0 = time.time()
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        results = list(ex.map(rn.case, cases))
    ver = sh([rn.cli, "--version"]).stdout.strip()
    out = {"env": {"app": "photocraft-cli", "version": ver, "cli": rn.cli, "quickExportTrimParam": rn.has_trim,
                   "seconds": round(time.time() - t0, 1)}, "cases": results}
    json.dump(out, open(os.path.join(a.out, "results.json"), "w"), indent=1)
    bad = [r for r in results if not r["ok"]]
    for r in bad:
        print("FAIL", r["id"], r.get("error", "")[:300])
    print(f"photocraft: {len(results) - len(bad)} ok, {len(bad)} failed in {out['env']['seconds']}s -> {a.out}")


if __name__ == "__main__":
    main()
