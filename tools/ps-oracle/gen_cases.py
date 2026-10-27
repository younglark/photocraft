#!/usr/bin/env python3
"""Generate tools/ps-oracle/cases.json from a curated PSD list.

Layers are addressed by *path*: a list of {"name", "index"} steps from the top
level down, where index is the 0-based position among siblings counted from the
TOP (Photoshop's doc.layers order and photocraft-cli `info` order agree).
Both runners resolve by index and verify the name.

Usage: gen_cases.py --cli target/release/photocraft-cli \
         --corpus /workspace/photocraft/corpus [--scan scan.json] > cases.json
"""
import argparse, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_corpus  # noqa: E402

# (corpus-relative path, op families to generate, note)
ALL = {"render", "hide", "show", "move", "rasterize", "exportLayer", "exportGroup", "slices"}
CURATED = [
    ("psd-tools/clipping-mask.psd", ALL, "groups, shapes, clipping"),
    ("psd-tools/group.psd", ALL, "simple group"),
    ("psd-tools/hidden-groups.psd", ALL, "hidden group"),
    ("psd-tools/hidden-layer.psd", ALL, "hidden layer"),
    ("psd-tools/mask.psd", ALL, "layer mask"),
    ("psd-tools/masks.psd", ALL, "type, groups, masks, fx"),
    ("psd-tools/layer_effects.psd", ALL, "type layers with every effect"),
    ("psd-tools/blend-and-clipping.psd", ALL, "blend modes + clipping"),
    ("psd-tools/placedLayer.psd", ALL, "smart objects"),
    ("psd-tools/smart-object-slice.psd", ALL, "smart object + slices"),
    ("psd-tools/slices.psd", ALL, "10 slices"),
    ("psd-tools/layers-minimal/type-layer.psd", ALL, "single type layer"),
    ("psd-tools/layers-minimal/shape-layer.psd", ALL, "single shape layer"),
    ("psd-tools/layers-minimal/smartobject-layer.psd", ALL, "single smart object"),
    ("psd-tools/transparency/knockout-isolated-groups.psd", ALL, "knockout / isolation"),
    ("psd-tools/transparency/transparency-group.psd", ALL, "transparency groups"),
    ("psd-tools/effects/shape-fx.psd", ALL, "group fx"),
    ("psd-tools/effects/stroke-effects.psd", ALL, "strokes"),
    ("psd-tools/effects/blend-modes.psd", {"render", "hide", "rasterize", "exportLayer"}, "fx blend modes (PS 27.9)"),
    ("psd-tools/effects/group-clips-child-stroke.psd", ALL, "group clipping + stroke (PS 27.9)"),
    ("psd-tools/adjustments/curves_rgb.psd", ALL, "curves"),
    ("psd-tools/adjustments/huesaturation_rgb.psd", ALL, "hue/sat"),
    ("psd-tools/fill_adjustments.psd", ALL, "many adjustment kinds"),
    ("psd-tools/clipping-mask2.psd", ALL, "clipped adjustments"),
    ("psd-tools/passthrough_blendmodes1.psd", ALL, "pass-through groups"),
    ("psd-tools/blend-modes/rgb-blend-modes.psd", {"render", "hide", "rasterize", "exportLayer", "exportGroup"}, "all blend modes, 54 SOs"),
    ("psd-tools/blend-modes/multiply.psd", {"render", "hide", "move", "exportLayer"}, "multiply"),
    ("psd-tools/blend-modes/soft-light.psd", {"render", "hide", "move", "exportLayer"}, "soft light"),
    ("psd-tools/blend-modes/color-dodge.psd", {"render", "hide", "move", "exportLayer"}, "color dodge"),
    ("psd-tools/blend-modes/hue.psd", {"render", "hide", "move", "exportLayer"}, "hue"),
    ("psd-tools/mask-density-layervectormask.psd", ALL, "mask density + vector mask"),
    ("psd-tools/artboard.psd", {"render", "hide"}, "artboard"),
    ("psd-tools/adjustment-fillers.psd", ALL, "fill layers"),
    ("psd-tools/layer_comps.psd", ALL, "Adobe RGB (1998) tagged"),
    ("psd/ag-psd/read/group-drop-shadows/src.psd", ALL, "group drop shadows (PS 26.5)"),
    ("psd/ag-psd/read-write/text/src.psd", ALL, "text"),
    ("psd/ag-psd/read-write/shapes/src.psd", ALL, "shapes + smart objects"),
    ("psd/ag-psd/read-write/float-color/src.psd", ALL, "float colours, text (PS 24.1)"),
    ("psd/ag-psd/read-write/group-blend/src.psd", ALL, "group blend"),
]

LEAF_KINDS = {"Pixel", "Shape", "Type", "Smart Object", "Fill"}


def info(cli, path):
    return json.loads(subprocess.run([cli, "info", "--compact", path], check=True, capture_output=True, text=True).stdout)


def slices(cli, path):
    r = subprocess.run([cli, "run", path, "--cmd", "slice.list"], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    for line in r.stdout.splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if j.get("command") == "slice.list":
            return j["result"]["slices"]
    return []


def flatten(layers, parent=(), parent_visible=True):
    """Yield (layer, path, effectively_visible, siblings, index) top-first."""
    for i, l in enumerate(layers):
        p = parent + ({"name": l["name"], "index": i},)
        vis = parent_visible and l["visible"]
        yield l, list(p), vis, layers, i
        if l.get("children") is not None:
            yield from flatten(l["children"], p, vis)


def area(l):
    b = l.get("bounds")
    return b[2] * b[3] if b else 0


def has_fx(l):
    fx = l.get("effects")
    return bool(fx) and fx.get("enabled", True) and any(it.get("enabled") for it in fx.get("items", []))


def is_clip_base(sibs, i):
    return i > 0 and sibs[i - 1].get("clipped")


def slug(rel):
    s = re.sub(r"\.psd$", "", rel)
    s = re.sub(r"^psd-tools/|^psd/ag-psd/", "", s)
    s = re.sub(r"/src$", "", s)
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")


def canvas():
    return {"name": "canvas", "type": "canvas"}


def build(rel, fams, d, sl):
    sid = slug(rel)
    allv = list(flatten(d["layers"]))
    top = d["layers"]
    cases = []

    def add(op, ops, outputs, note=None):
        c = {"id": f"{sid}__{op}", "ops": ops, "outputs": outputs}
        if note:
            c["note"] = note
        cases.append(c)

    # Background-like: bottom top-level pixel layer named Background.
    def is_bg(l, sibs, i, path):
        return len(path) == 1 and i == len(sibs) - 1 and l["kind"] == "Pixel" and l["name"] == "Background"

    leaves = [(l, p, v, s, i) for (l, p, v, s, i) in allv if l["kind"] in LEAF_KINDS and area(l) > 0]
    visible_leaves = [t for t in leaves if t[2]]
    export_cands = [t for t in visible_leaves if not t[0]["clipped"] and not is_bg(*t[:1], t[3], t[4], t[1])]
    export_cands.sort(key=lambda t: -area(t[0]))

    if "render" in fams:
        add("render", [], [canvas()])

    if "hide" in fams and len(allv) > 1:
        cands = [t for t in allv if t[2] and not is_bg(t[0], t[3], t[4], t[1]) and (t[0]["kind"] in LEAF_KINDS or t[0]["kind"] == "Group")]
        cands.sort(key=lambda t: (len(t[1]) > 1, -area(t[0])))
        if cands:
            add("hide", [{"op": "hide", "path": cands[0][1]}], [canvas()], f"hide {cands[0][0]['kind']}")

    if "show" in fams:
        hidden = [t for t in allv if not t[0]["visible"]]
        # prefer one whose ancestors are visible
        hidden.sort(key=lambda t: (len(t[1]),))
        if hidden:
            add("show", [{"op": "show", "path": hidden[0][1]}], [canvas()], f"show {hidden[0][0]['kind']}")

    if "move" in fams and len(top) >= 3:
        def movable(i):
            l = top[i]
            return not l.get("clipped") and not is_clip_base(top, i) and not is_bg(l, top, i, [0])
        n = len(top)
        if movable(0) and n - 2 > 0:
            add("moveBelow", [{"op": "move", "path": [{"name": top[0]["name"], "index": 0}],
                               "target": [{"name": top[n - 2]["name"], "index": n - 2}], "position": "below"}], [canvas()])
        j = n - 2
        if movable(j) and j > 0:
            add("moveAbove", [{"op": "move", "path": [{"name": top[j]["name"], "index": j}],
                               "target": [{"name": top[0]["name"], "index": 0}], "position": "above"}], [canvas()])
    if "move" in fams:
        groups = [i for i, l in enumerate(top) if l["kind"] == "Group"]
        movers = [i for i, l in enumerate(top) if l["kind"] in LEAF_KINDS and not l.get("clipped")
                  and not is_clip_base(top, i) and not is_bg(l, top, i, [0])]
        pair = next(((m, g) for m in movers for g in groups if m != g), None)
        if pair:
            m, g = pair
            add("moveInto", [{"op": "move", "path": [{"name": top[m]["name"], "index": m}],
                              "target": [{"name": top[g]["name"], "index": g}], "position": "into"}], [canvas()],
                "into = top of the group (Photoshop drop-on-group)")

    if "rasterize" in fams:
        picks = {
            "type": lambda l: l["kind"] == "Type",
            "shape": lambda l: l["kind"] == "Shape",
            "smartObject": lambda l: l["kind"] == "Smart Object",
            "layerStyle": lambda l: l["kind"] != "Group" and has_fx(l),
        }
        for what, f in picks.items():
            c = [t for t in leaves if f(t[0]) and t[2]] or [t for t in leaves if f(t[0])]
            if not c:
                continue
            l, p = c[0][0], c[0][1]
            outs = [canvas()]
            if not l["clipped"]:
                outs.append({"name": "layer", "type": "layer", "path": p, "trim": True})
            add(f"rasterize-{what}", [{"op": "rasterize", "path": p, "what": what}], outs, f"{l['kind']} '{l['name']}'")

    if "exportLayer" in fams and export_cands:
        l, p = export_cands[0][0], export_cands[0][1]
        add("exportLayer", [], [{"name": "trimmed", "type": "layer", "path": p, "trim": True},
                                {"name": "full", "type": "layer", "path": p, "trim": False}], f"{l['kind']} '{l['name']}'")
        # a second, small/nested one for variety
        nested = [t for t in export_cands[1:] if len(t[1]) > 1 or t[0]["kind"] != l["kind"]]
        if nested:
            l2, p2 = nested[0][0], nested[0][1]
            add("exportLayer2", [], [{"name": "trimmed", "type": "layer", "path": p2, "trim": True},
                                     {"name": "full", "type": "layer", "path": p2, "trim": False}], f"{l2['kind']} '{l2['name']}'")

    if "exportGroup" in fams:
        gs = [t for t in allv if t[0]["kind"] == "Group" and t[2] and not t[0]["clipped"]
              and any(c["kind"] != "Group" and c["visible"] and area(c) > 0 for c in t[0].get("children", []))]
        if gs:
            add("exportGroup", [], [{"name": "group", "type": "layer", "path": gs[0][1], "trim": True}], f"group '{gs[0][0]['name']}'")

    if "slices" in fams:
        W, H = d["width"], d["height"]
        user = []
        for s in sl:
            if s.get("origin") not in ("user", "layer") or s.get("kind") != "image":
                continue
            x, y, w, h = s["rect"]
            x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
            if x1 > x0 and y1 > y0:
                user.append({"number": s["number"], "name": s["name"], "rect": [x0, y0, x1 - x0, y1 - y0]})
        if user:
            add("slices", [], [{"name": "slices", "type": "slices", "slices": user}],
                "rects from the PSD's slice resource; PS crops its own composite to them")
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", required=True)
    ap.add_argument("--corpus", required=True)
    a = ap.parse_args()
    out = {"version": 1,
           "colour": "raw document values, no colour conversion; 8-bit RGB(A) PNG; ICC ignored by compare",
           "pathSemantics": "path = [{name,index}] top-level first; index counts siblings from the top",
           "files": [], "cases": []}
    for rel, fams, note in CURATED:
        src = os.path.join(a.corpus, rel)
        d = info(a.cli, src)
        res = scan_corpus.resources(src)
        sl = slices(a.cli, src) if "slices" in fams else []
        fid = slug(rel)
        out["files"].append({"id": fid, "psd": "psds/" + rel, "note": note, "width": d["width"], "height": d["height"],
                             "mode": d.get("mode"), "depth": d.get("depth"),
                             "creatorTool": res.get("creatorTool") or res.get("reader") or res.get("writer"),
                             "photoshopSaved": "Photoshop" in ((res.get("writer") or "") + (res.get("creatorTool") or "")),
                             "iccProfile": res.get("iccProfile")})
        for c in build(rel, fams, d, sl):
            c["psd"] = "psds/" + rel
            c["file"] = fid
            out["cases"].append(c)
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
