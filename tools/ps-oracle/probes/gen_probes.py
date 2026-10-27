#!/usr/bin/env python3
"""Black-box probes: small synthetic documents where exactly one parameter varies, rendered by
Photoshop (flatten PNG + Photoshop's own re-saved PSD) and by photocraft, to fit Photoshop's
formulas per root cause.

Two tracks:
  P  photocraft-authored PSDs (photocraft-cli run --new … --cmd … --out psds/<id>.psd). Photoshop
     opens them, saves canvas.png and resaved.psd. Fill/shape/adjustment/effect content is live
     (descriptors), so Photoshop re-renders it; resaved.psd rendered by photocraft separates
     writer artefacts from renderer differences.
  J  Photoshop-authored: the case carries a `build` recipe (action descriptors, see ps_runner.jsx);
     Photoshop makes the document from scratch, saves canvas.png + resaved.psd, and photocraft
     renders Photoshop's resaved.psd.

Usage: gen_probes.py --cli <photocraft-cli> --stage <dir>   (writes <dir>/{cases.json,psds/,inputs/})
"""
import argparse, colorsys, json, os, random, subprocess, sys

W = H = 256
probes = []  # dicts: id, family, track, vary, cmds | build, src


def P(pid, family, vary, cmds, src=None, note=None):
    probes.append(dict(id=pid, family=family, track="P", vary=vary, cmds=cmds, src=src, note=note))


def J(pid, family, vary, build, note=None):
    probes.append(dict(id=pid, family=family, track="J", vary=vary, build=build, note=note))


def c(cmd, **params):
    return (cmd, params)


NEW = {"width": W, "height": H, "background": "white"}

# ------------------------------------------------------------------ 1 clipping-group flags
BASE_FX = {
    "colorOverlay": c("layer.layerStyle.colorOverlay", color="#0000ff", opacity=50),
    "gradientOverlay": c("layer.layerStyle.gradientOverlay", **{"from": "#0000ff", "to": "#ffff00", "opacity": 50, "angle": 0}),
    "innerGlow": c("layer.layerStyle.innerGlow", color="#0000ff", opacity=100, blend="normal", size=16, choke=0),
    "satin": c("layer.layerStyle.satin", color="#0000ff", opacity=100, blend="normal", angle=45, distance=12, size=12),
    "innerShadow": c("layer.layerStyle.innerShadow", color="#0000ff", opacity=100, blend="normal", angle=135, distance=10, size=6, choke=0),
    "strokeInside": c("layer.layerStyle.stroke", size=10, position="inside", color="#0000ff"),
    "strokeOutside": c("layer.layerStyle.stroke", size=10, position="outside", color="#0000ff"),
    "strokeCenter": c("layer.layerStyle.stroke", size=10, position="center", color="#0000ff"),
    "dropShadow": c("layer.layerStyle.dropShadow", color="#0000ff", opacity=100, blend="normal", angle=135, distance=12, size=0, spread=0),
    "bevelInner": c("layer.layerStyle.bevelEmboss", style="inner", size=12),
}


def clip_doc(base_fx, flags, fill=100, clipped="fx", base_blend=None, clip_blend=None, extra_base=()):
    cmds = [c("shape.create", kind="rect", rect=[40, 40, 120, 120], fill="#ffffff", name="base")]
    if base_fx:
        cmds.append(BASE_FX[base_fx])
    bo = {"blendClippedLayersAsGroup": flags[0], "blendInteriorEffectsAsGroup": flags[1], "transparencyShapesLayer": flags[2], "fillOpacity": fill}
    if base_blend:
        bo["blend"] = base_blend
    cmds.append(c("layer.layerStyle.blendingOptions", **bo))
    cmds += list(extra_base)
    if clipped == "fx":
        cmds += [c("shape.create", kind="rect", rect=[100, 100, 120, 120], fill="#000000", name="clipped"),
                 c("layer.layerStyle.colorOverlay", color="#ff0000", opacity=100)]
    elif clipped == "plain":
        cmds += [c("shape.create", kind="rect", rect=[100, 100, 120, 120], fill="#ff0000", name="clipped")]
    elif clipped == "two":
        cmds += [c("shape.create", kind="rect", rect=[100, 100, 120, 120], fill="#ff0000", name="clipped1"),
                 c("layer.createClippingMask"),
                 c("shape.create", kind="rect", rect=[20, 110, 120, 40], fill="#00ff00", name="clipped2")]
    cmds.append(c("layer.createClippingMask"))
    if clip_blend:
        cmds.append(c("layer.setProps", blend=clip_blend))
    return cmds


TF = (True, False)
for bc in TF:
    for bi in TF:
        for ts in TF:
            for fill in (100, 50):
                for cl in ("fx", "plain"):
                    P(f"clip-co-bc{int(bc)}-bi{int(bi)}-ts{int(ts)}-f{fill}-{cl}", "clip",
                      dict(baseFx="colorOverlay", blendClipped=bc, blendInterior=bi, transparencyShapes=ts, fill=fill, clipped=cl),
                      clip_doc("colorOverlay", (bc, bi, ts), fill, cl))
for fx in [k for k in BASE_FX if k != "colorOverlay"]:
    for bc in TF:
        for bi in TF:
            P(f"clip-{fx}-bc{int(bc)}-bi{int(bi)}", "clip", dict(baseFx=fx, blendClipped=bc, blendInterior=bi), clip_doc(fx, (bc, bi, True)))
for bc in TF:
    for bi in TF:
        P(f"clip-nofx-bc{int(bc)}-bi{int(bi)}", "clip", dict(baseFx=None, blendClipped=bc, blendInterior=bi), clip_doc(None, (bc, bi, True)))
        P(f"clip-two-bc{int(bc)}-bi{int(bi)}", "clip", dict(baseFx="colorOverlay", blendClipped=bc, blendInterior=bi, clipped="two"), clip_doc("colorOverlay", (bc, bi, True), clipped="two"))
        P(f"clip-mulclip-bc{int(bc)}-bi{int(bi)}", "clip", dict(baseFx="colorOverlay", blendClipped=bc, blendInterior=bi, clipBlend="Multiply"),
          clip_doc("colorOverlay", (bc, bi, True), clipped="plain", clip_blend="Multiply"))
        P(f"clip-mulbase-bc{int(bc)}-bi{int(bi)}", "clip", dict(baseFx="colorOverlay", blendClipped=bc, blendInterior=bi, baseBlend="multiply"),
          clip_doc("colorOverlay", (bc, bi, True), base_blend="multiply"))
# masks hide effects (base layer mask: reveal only x<120; vector mask = the shape itself)
for lm in TF:
    for bc in TF:
        P(f"clip-lmhide{int(lm)}-bc{int(bc)}", "clip", dict(baseFx="strokeOutside", layerMaskHidesEffects=lm, blendClipped=bc),
          clip_doc("strokeOutside", (bc, False, True), extra_base=[c("select.rect", x=0, y=0, width=120, height=256),
                                                                   c("layer.layerMask.revealSelection"),
                                                                   c("layer.layerStyle.blendingOptions", layerMaskHidesEffects=lm)]))
for vm in TF:
    P(f"clip-vmhide{int(vm)}", "clip", dict(baseFx="strokeOutside", vectorMaskHidesEffects=vm),
      clip_doc("strokeOutside", (True, False, True), extra_base=[c("layer.layerStyle.blendingOptions", vectorMaskHidesEffects=vm)]))
# the corpus file's exact quadrant set-up (white base, blue 50 % overlay, black clipped + red overlay), knockout on the clipped layer
for ko in ("shallow", "deep"):
    for bc in TF:
        cmds = clip_doc("colorOverlay", (bc, False, True))
        cmds.append(c("layer.layerStyle.blendingOptions", knockout=ko, fillOpacity=0))
        P(f"clip-ko{ko}-bc{int(bc)}", "clip", dict(baseFx="colorOverlay", blendClipped=bc, clippedKnockout=ko, clippedFill=0), cmds)

# ------------------------------------------------------------------ 2 Hue/Saturation
HS_INPUT = "inputs/huesat-input.png"
HS_SL = [(1.0, .5), (.5, .5), (.15, .5), (.03, .5), (1.0, .25), (1.0, .75), (.5, .15), (.5, .88)]


def make_hs_input(path):
    from PIL import Image
    im = Image.new("RGB", (W, H))
    px = im.load()
    for band, (s, l) in enumerate(HS_SL):
        for y in range(band * 16, band * 16 + 16):
            for x in range(W):
                r, g, b = colorsys.hls_to_rgb(x / W, l, s)
                px[x, y] = (round(r * 255), round(g * 255), round(b * 255))
    rnd = random.Random(20261027)
    for y in range(128, 192):
        for x in range(W):
            px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    for y in range(192, 256):  # near-neutral: chroma <= 12, the case where rounding order decides the hue
        for x in range(W):
            g = rnd.randrange(256)
            px[x, y] = tuple(max(0, min(255, g + rnd.randint(-6, 6))) for _ in range(3))
    im.save(path)


def hs(pid, vary, **params):
    P(pid, "huesat", vary, [c("layer.newAdjustmentLayer.hueSaturation", **params)], src=HS_INPUT)


for h in (-180, -90, -30, -1, 1, 30, 90, 180):
    hs(f"hs-hue{h}", dict(hue=h), hue=h)
for s in (-100, -50, -25, -1, 1, 25, 50, 75, 99, 100):
    hs(f"hs-sat{s}", dict(saturation=s), saturation=s)
for l in (-100, -50, -37, -10, -1, 1, 10, 37, 50, 100):
    hs(f"hs-light{l}", dict(lightness=l), lightness=l)
for h, s, l in ((122, 100, -37), (40, 25, -10), (66, 0, 28), (-27, 0, 10), (0, -97, 0), (0, 100, 0), (0, 100, 37), (90, -50, 20)):
    hs(f"hs-combo{h}_{s}_{l}", dict(hue=h, saturation=s, lightness=l), hue=h, saturation=s, lightness=l)
for h, s, l in ((0, 25, 0), (120, 100, 0), (240, 50, 0), (30, 25, 50), (30, 25, -50), (200, 0, 0), (200, 100, 100), (60, 75, -100), (300, 40, 5)):
    hs(f"hs-colorize{h}_{s}_{l}", dict(colorize=True, hue=h, saturation=s, lightness=l), colorize=True, hue=h, saturation=s, lightness=l)
RANGES = {"reds": [315, 345, 15, 45], "yellows": [15, 45, 75, 105], "greens": [75, 105, 135, 165], "cyans": [135, 165, 195, 225], "blues": [195, 225, 255, 285], "magentas": [255, 285, 315, 345]}
for rn, rr in RANGES.items():
    for k, v in (("hue", 60), ("saturation", 50), ("saturation", -100), ("lightness", 50), ("lightness", -50)):
        hs(f"hs-{rn}-{k}{v}", dict(range=rn, **{k: v}), **{rn: {"hue": 0, "saturation": 0, "lightness": 0, "range": rr, k: v}})
for nm, rr in (("narrow", [345, 355, 5, 15]), ("wide", [270, 340, 20, 90]), ("hard", [330, 330, 30, 30]), ("asym", [300, 350, 10, 120]),
               ("shifted", [111, 191, 279, 368]), ("negwrap", [-98, -38, 40, 133])):
    hs(f"hs-reds-falloff-{nm}", dict(range="reds", bounds=rr, hue=90), reds={"hue": 90, "saturation": 0, "lightness": 0, "range": rr})
hs("hs-overlap-reds-yellows", dict(overlap=True), reds={"hue": 40, "saturation": 0, "lightness": 0, "range": [315, 345, 25, 55]},
   yellows={"hue": 40, "saturation": 0, "lightness": 0, "range": [15, 45, 75, 105]})
hs("hs-master+reds", dict(hue=30, rangeHue=60), hue=30, reds={"hue": 60, "saturation": 0, "lightness": 0, "range": RANGES["reds"]})
hs("hs-master-sat+blues-sat", dict(saturation=-50, rangeSat=80), saturation=-50, blues={"hue": 0, "saturation": 80, "lightness": 0, "range": RANGES["blues"]})
hs("hs-master-light+greens-light", dict(lightness=30, rangeLight=-40), lightness=30, greens={"hue": 0, "saturation": 0, "lightness": -40, "range": RANGES["greens"]})
# the four layers of corpus huesaturation_rgb.psd, one at a time
CORPUS_HS = {
    "hs1": dict(hue=66, lightness=28, magentas={"hue": 26, "saturation": 0, "lightness": 46, "range": [255, 285, 315, 427]},
                reds={"hue": 180, "saturation": 0, "lightness": -59, "range": [-98, -38, 40, 133]}, yellows={"hue": 16, "saturation": 0, "lightness": 100, "range": [15, 45, 75, 105]}),
    "hs2": dict(hue=-27, lightness=10, blues={"hue": 38, "saturation": -78, "lightness": 43, "range": [222, 260, 353, 383]},
                cyans={"hue": -17, "saturation": 20, "lightness": -91, "range": [135, 165, 195, 225]}, magentas={"hue": 36, "saturation": 0, "lightness": -26, "range": [-85, -49, 50, 137]},
                reds={"hue": 19, "saturation": 14, "lightness": 26, "range": [-45, -15, 15, 90]}, yellows={"hue": -38, "saturation": 93, "lightness": 27, "range": [-30, -1, 29, 59]}),
    "hs3": dict(saturation=-97, cyans={"hue": 0, "saturation": -61, "lightness": 0, "range": [-81, 46, 107, 147]}, greens={"hue": -38, "saturation": -83, "lightness": 0, "range": [267, 315, 343, 387]},
                magentas={"hue": 0, "saturation": 100, "lightness": 0, "range": [82, 133, 151, 171]}, reds={"hue": 32, "saturation": 93, "lightness": 0, "range": [207, 239, 351, 397]}),
    "hs4": dict(hue=122, saturation=100, lightness=-37),
}
for k, v in CORPUS_HS.items():
    hs(f"hs-corpus-{k}", dict(corpus=k), **v)

# ------------------------------------------------------------------ 3 gradients
GR = {"from": "#000000", "to": "#ffffff"}
for st in ("linear", "radial", "angle", "reflected", "diamond"):
    for ang in (0, 30, 90, 135):
        P(f"gfill-{st}-a{ang}", "gradient", dict(kind="fillLayer", style=st, angle=ang),
          [c("layer.newFillLayer.gradient", style=st, angle=ang, **GR)])
    P(f"gfill-{st}-rev", "gradient", dict(kind="fillLayer", style=st, angle=0, reverse=True), [c("layer.newFillLayer.gradient", style=st, angle=0, reverse=True, **GR)])


def gshape(rect, **g):
    g.setdefault("stops", [[0, "#000000"], [1, "#ffffff"]])
    return c("shape.create", kind="rect", rect=rect, fill={"gradient": g})


R0 = [48, 80, 128, 64]  # off-centre, non-square: separates canvas from layer reference boxes
for st in ("linear", "radial", "angle", "reflected", "diamond"):
    for al in TF:
        P(f"gshape-{st}-align{int(al)}", "gradient", dict(kind="shapeFill", style=st, align=al, angle=30), [gshape(R0, style=st, angle=30, align=al)])
for st in ("linear", "radial"):
    for sc in (25, 50, 150):
        P(f"gshape-{st}-scale{sc}", "gradient", dict(kind="shapeFill", style=st, scale=sc), [gshape(R0, style=st, angle=0, scale=sc)])
    for off in ([0.1, 0], [0, -0.15], [-0.2, 0.2]):  # fractions of the frame
        P(f"gshape-{st}-off{off[0]}_{off[1]}", "gradient", dict(kind="shapeFill", style=st, offset=off), [gshape(R0, style=st, angle=0, offset=off)])
for dz in TF:
    for st in ("linear", "radial"):
        P(f"gdither-{st}-d{int(dz)}", "gradient", dict(kind="shapeFill", style=st, dither=dz, dark=True),
          [gshape([0, 0, W, H], style=st, angle=0, dither=dz, stops=[[0, "#000000"], [1, "#202020"]])])
    P(f"gdither-color-d{int(dz)}", "gradient", dict(kind="shapeFill", dither=dz, stops="red-blue"),
      [gshape([0, 0, W, H], style="linear", angle=0, dither=dz, stops=[[0, "#ff0000"], [1, "#0000ff"]])])
P("gshape-midpoint25", "gradient", dict(kind="shapeFill", midpoints=[0.25]), [gshape([0, 0, W, H], style="linear", angle=0, midpoints=[0.25])])
P("gshape-3stop-opacity", "gradient", dict(kind="shapeFill", stops="r-g-b", opacityStops=[[0, 1], [1, 0]]),
  [gshape([0, 0, W, H], style="linear", angle=0, stops=[[0, "#ff0000"], [0.5, "#00ff00"], [1, "#0000ff"]], opacityStops=[[0, 1], [1, 0]])])
# gradient overlay (layer style) on the same off-centre rect
for st in ("linear", "radial", "angle", "reflected", "diamond"):
    for ang in (0, 45, 90):
        P(f"gov-{st}-a{ang}", "gradient", dict(kind="overlay", style=st, angle=ang),
          [c("shape.create", kind="rect", rect=R0, fill="#808080"), c("layer.layerStyle.gradientOverlay", style=st, angle=ang, **GR)])
for sc in (25, 50, 150):
    P(f"gov-linear-scale{sc}", "gradient", dict(kind="overlay", style="linear", scale=sc),
      [c("shape.create", kind="rect", rect=R0, fill="#808080"), c("layer.layerStyle.gradientOverlay", style="linear", angle=0, scale=sc, **GR)])
P("gov-linear-rev", "gradient", dict(kind="overlay", reverse=True),
  [c("shape.create", kind="rect", rect=R0, fill="#808080"), c("layer.layerStyle.gradientOverlay", style="linear", angle=0, reverse=True, **GR)])
for m in ("classic", "perceptual", "linear", "smooth"):
    for sm in (0, 100):
        P(f"gov-method-{m}-s{sm}", "gradient", dict(kind="overlay", method=m, smoothness=sm, stops="red-blue"),
          [c("shape.create", kind="rect", rect=[0, 0, W, H], fill="#808080"),
           c("layer.layerStyle.gradientOverlay", style="linear", angle=0, method=m, smoothness=sm, **{"from": "#ff0000", "to": "#0000ff"})])


# Track J: Photoshop-authored gradient fill layers (method, dither, smoothness: what the CLI cannot write)
def rgb(r, g, b):
    return {"_obj": "RGBColor", "red": float(r), "grain": float(g), "blue": float(b)}


def grad_desc(colors, smooth=4096.0):
    return {"_obj": "gradientClassEvent", "name": "probe", "gradientForm": {"_enum": "gradientForm", "_value": "customStops"},
            "interfaceIconFrameDimmed": float(smooth),
            "colors": [{"_obj": "colorStop", "color": rgb(*col), "type": {"_enum": "colorStopType", "_value": "userStop"},
                        "location": {"_int": loc}, "midpoint": {"_int": 50}} for loc, col in colors],
            "transparency": [{"_obj": "transferSpec", "opacity": {"_unit": "percentUnit", "_value": 100.0}, "location": {"_int": 0}, "midpoint": {"_int": 50}},
                             {"_obj": "transferSpec", "opacity": {"_unit": "percentUnit", "_value": 100.0}, "location": {"_int": 4096}, "midpoint": {"_int": 50}}]}


def grad_fill_step(method, dither, smooth, colors, style="linear", angle=0.0, note=None):
    return {"_obj": "make", "$note": note, "null": {"_ref": "contentLayer"},
            "using": {"_obj": "contentLayer", "type": {"_obj": "gradientLayer",
                      "gradientsInterpolationMethod": {"_enum": "gradientInterpolationMethodType", "_value": method},
                      "angle": {"_unit": "angleUnit", "_value": angle}, "type": {"_enum": "gradientType", "_value": style},
                      "dither": dither, "align": True, "scale": {"_unit": "percentUnit", "_value": 100.0},
                      "gradient": grad_desc(colors, smooth)}}}


RB = [(0, (255, 0, 0)), (4096, (0, 0, 255))]
DARK = [(0, (0, 0, 0)), (4096, (32, 32, 32))]
for m in ("classic", "perceptual", "linear", "smooth"):
    for d in TF:
        J(f"jgfill-{m}-d{int(d)}", "gradient", dict(kind="fillLayer(PS)", method=m, dither=d, stops="red-blue"),
          {"new": {"width": W, "height": H, "fill": "white"}, "steps": [grad_fill_step(m, d, 4096.0, RB, note="gradient fill layer")]})
for sm in (0.0, 2048.0):
    J(f"jgfill-classic-smooth{int(sm)}", "gradient", dict(kind="fillLayer(PS)", method="classic", smoothness=sm / 4096, stops="r-g-b"),
      {"new": {"width": W, "height": H, "fill": "white"}, "steps": [grad_fill_step("classic", False, sm, [(0, (255, 0, 0)), (2048, (0, 255, 0)), (4096, (0, 0, 255))])]})
for d in TF:
    J(f"jgfill-dark-d{int(d)}", "gradient", dict(kind="fillLayer(PS)", method="classic", dither=d, dark=True),
      {"new": {"width": W, "height": H, "fill": "white"}, "steps": [grad_fill_step("classic", d, 4096.0, DARK)]})

# ------------------------------------------------------------------ 4 patterns
PAT_INPUT = "inputs/pattern-tile.png"


def make_tile(path):
    from PIL import Image
    im = Image.new("RGB", (13, 7))
    for y in range(7):
        for x in range(13):
            im.putpixel((x, y), (x * 19 % 256, y * 36 % 256, (x * y * 7 + 40) % 256))
    im.putpixel((0, 0), (255, 255, 255))
    im.save(path)


PAT_PRE = [c("edit.definePattern", name="probe-tile"), c("image.canvasSize", width=W, height=H, anchor="topLeft", extensionColor="white"),
           c("select.all"), c("edit.fill", contents="color", color="#ffffff"), c("select.deselect")]
for ph in ([0, 0], [3, 2], [-5, 1], [6, 0], [0, 4]):
    P(f"pfill-phase{ph[0]}_{ph[1]}", "pattern", dict(kind="fillLayer", phase=ph), PAT_PRE + [c("layer.newFillLayer.pattern", pattern="probe-tile", phase=ph)], src=PAT_INPUT)
for sc in (50, 200, 37, 150):
    P(f"pfill-scale{sc}", "pattern", dict(kind="fillLayer", scale=sc), PAT_PRE + [c("layer.newFillLayer.pattern", pattern="probe-tile", scale=sc)], src=PAT_INPUT)
P("pfill-angle30", "pattern", dict(kind="fillLayer", angle=30), PAT_PRE + [c("layer.newFillLayer.pattern", pattern="probe-tile", angle=30)], src=PAT_INPUT)
for ln in TF:
    P(f"pfill-shape-link{int(ln)}", "pattern", dict(kind="shapeFill", link=ln), PAT_PRE + [c("select.rect", x=37, y=23, width=150, height=100),
      c("layer.newFillLayer.pattern", pattern="probe-tile", link=ln), c("select.deselect")], src=PAT_INPUT)
for ln in TF:
    for ph in ([0, 0], [4, 3]):
        P(f"pov-link{int(ln)}-phase{ph[0]}_{ph[1]}", "pattern", dict(kind="overlay", link=ln, phase=ph),
          PAT_PRE + [c("shape.create", kind="rect", rect=[37, 23, 150, 100], fill="#808080"),
                     c("layer.layerStyle.patternOverlay", pattern="probe-tile", link=ln, phaseX=ph[0], phaseY=ph[1])], src=PAT_INPUT)
for sc in (50, 200):
    P(f"pov-scale{sc}", "pattern", dict(kind="overlay", scale=sc), PAT_PRE + [c("shape.create", kind="rect", rect=[37, 23, 150, 100], fill="#808080"),
      c("layer.layerStyle.patternOverlay", pattern="probe-tile", scale=sc)], src=PAT_INPUT)

# ------------------------------------------------------------------ 5 vector shapes / strokes
for dx in (0, .25, .5, .75):
    P(f"shape-rect-x{dx}", "shape", dict(kind="rect", offset=dx), [c("shape.create", kind="rect", rect=[40 + dx, 40 + dx, 100, 60], fill="#000000")])
P("shape-rect-w100.3", "shape", dict(kind="rect", width=100.3), [c("shape.create", kind="rect", rect=[40, 40, 100.3, 60.6], fill="#000000")])
for ang in (15, 45):
    import math
    cx, cy, r = 128, 128, 70
    pts = [[cx + r * math.cos(math.radians(ang + 90 * k)), cy + r * math.sin(math.radians(ang + 90 * k))] for k in range(4)]
    P(f"shape-rot{ang}", "shape", dict(kind="rotatedSquare", angle=ang), [c("shape.create", kind="path", path={"subpaths": [{"closed": True, "knots": pts}]}, fill="#000000")])
for d, off in ((10.5, 0), (64, 0), (63.3, .5), (31, .25)):
    P(f"shape-ellipse-d{d}-o{off}", "shape", dict(kind="ellipse", diameter=d, offset=off), [c("shape.create", kind="ellipse", rect=[60 + off, 60 + off, d, d], fill="#000000")])
P("shape-triangle", "shape", dict(kind="polygon", sides=3), [c("shape.create", kind="polygon", rect=[40, 40, 170, 150], sides=3, fill="#000000")])
for wgt in (1, 2):
    P(f"shape-line-w{wgt}", "shape", dict(kind="line", weight=wgt), [c("shape.create", kind="line", **{"from": [20, 30], "to": [230, 200]}, weight=wgt, fill="#000000")])
for al in ("inside", "center", "outside"):
    for wdt in (1, 2, 3, 4, 5):
        P(f"sstroke-rect-{al}-w{wdt}", "shape", dict(kind="shapeStroke", geom="rect", align=al, width=wdt),
          [c("shape.create", kind="rect", rect=[60, 60, 120, 100], fill="#ffff00", stroke={"width": wdt, "color": "#000000", "align": al})])
        P(f"sstroke-circle-{al}-w{wdt}", "shape", dict(kind="shapeStroke", geom="circle", align=al, width=wdt),
          [c("shape.create", kind="ellipse", rect=[60, 60, 120, 120], fill="#ffff00", stroke={"width": wdt, "color": "#000000", "align": al})])
        P(f"fxstroke-rect-{al}-w{wdt}", "shape", dict(kind="fxStroke", geom="rect", align=al, width=wdt),
          [c("shape.create", kind="rect", rect=[60, 60, 120, 100], fill="#ffff00"), c("layer.layerStyle.stroke", size=wdt, position=al, color="#000000")])
    for wdt in (1, 3):
        P(f"fxstroke-circle-{al}-w{wdt}", "shape", dict(kind="fxStroke", geom="circle", align=al, width=wdt),
          [c("shape.create", kind="ellipse", rect=[60, 60, 120, 120], fill="#ffff00"), c("layer.layerStyle.stroke", size=wdt, position=al, color="#000000")])
    for wdt in (1, 2):
        P(f"sstroke-rect.5-{al}-w{wdt}", "shape", dict(kind="shapeStroke", geom="rect+0.5", align=al, width=wdt),
          [c("shape.create", kind="rect", rect=[60.5, 60.5, 120, 100], fill="#ffff00", stroke={"width": wdt, "color": "#000000", "align": al})])


# Track J shapes: Photoshop's own rectangle / ellipse tool output (float coordinates, shape stroke)
def px(v):
    return {"_unit": "pixelsUnit", "_value": float(v)}


def shape_step(kind, l, t, r, b, stroke=None, note=None):
    sh = {"_obj": kind, "unitValueQuadVersion": {"_int": 1}, "top": px(t), "left": px(l), "bottom": px(b), "right": px(r)}
    if kind == "rectangle":
        for k in ("topRight", "topLeft", "bottomLeft", "bottomRight"):
            sh[k] = px(0)
    using = {"_obj": "contentLayer", "type": {"_obj": "solidColorLayer", "color": rgb(255, 255, 0)}, "shape": sh}
    if stroke:
        w, al = stroke
        using["strokeStyle"] = {"_obj": "strokeStyle", "strokeStyleVersion": {"_int": 2}, "strokeEnabled": True, "fillEnabled": True,
                                "strokeStyleLineWidth": px(w), "strokeStyleLineDashOffset": {"_unit": "pointsUnit", "_value": 0.0},
                                "strokeStyleMiterLimit": 100.0,
                                "strokeStyleLineCapType": {"_enum": "strokeStyleLineCapType", "_value": "strokeStyleButtCap"},
                                "strokeStyleLineJoinType": {"_enum": "strokeStyleLineJoinType", "_value": "strokeStyleMiterJoin"},
                                "strokeStyleLineAlignment": {"_enum": "strokeStyleLineAlignment", "_value": {"inside": "strokeStyleAlignInside", "center": "strokeStyleAlignCenter", "outside": "strokeStyleAlignOutside"}[al]},
                                "strokeStyleScaleLock": False, "strokeStyleStrokeAdjust": False, "strokeStyleLineDashSet": [],
                                "strokeStyleBlendMode": {"_enum": "blendMode", "_value": "normal"},
                                "strokeStyleOpacity": {"_unit": "percentUnit", "_value": 100.0},
                                "strokeStyleContent": {"_obj": "solidColorLayer", "color": rgb(0, 0, 0)}, "strokeStyleResolution": 72.0}
    return {"_obj": "make", "$note": note, "null": {"_ref": "contentLayer"}, "using": using}


for kind, geo in (("rectangle", (40.25, 40.5, 140.25, 100.5)), ("ellipse", (60.5, 60.5, 180.5, 180.5)), ("rectangle", (60, 60, 180, 160)), ("ellipse", (60, 60, 180, 180))):
    J(f"jshape-{kind}-{geo[0]}", "shape", dict(kind="PS " + kind, rect=geo), {"new": {"width": W, "height": H, "fill": "white"}, "steps": [shape_step(kind, *geo)]})
for al in ("inside", "center", "outside"):
    for wdt in (1, 3):
        J(f"jsstroke-rect-{al}-w{wdt}", "shape", dict(kind="PS shapeStroke", geom="rect", align=al, width=wdt),
          {"new": {"width": W, "height": H, "fill": "white"}, "steps": [shape_step("rectangle", 60, 60, 180, 160, (wdt, al))]})
        J(f"jsstroke-circle-{al}-w{wdt}", "shape", dict(kind="PS shapeStroke", geom="circle", align=al, width=wdt),
          {"new": {"width": W, "height": H, "fill": "white"}, "steps": [shape_step("ellipse", 60, 60, 180, 180, (wdt, al))]})


# Track J Hue/Saturation over the same input: cross-checks photocraft's descriptor writer
def hs_steps(master, ranges=(), colorize=False):
    adj = [{"_obj": "hueSatAdjustmentV2", "hue": {"_int": master[0]}, "saturation": {"_int": master[1]}, "lightness": {"_int": master[2]}}]
    for idx, (b0, b1, b2, b3), (h, s, l) in ranges:
        adj.append({"_obj": "hueSatAdjustmentV2", "localRange": {"_int": idx}, "beginRamp": {"_int": b0}, "beginSustain": {"_int": b1},
                    "endSustain": {"_int": b2}, "endRamp": {"_int": b3}, "hue": {"_int": h}, "saturation": {"_int": s}, "lightness": {"_int": l}})
    return [{"_obj": "make", "$note": "hue/sat layer", "null": {"_ref": "adjustmentLayer"},
             "using": {"_obj": "adjustmentLayer", "type": {"_obj": "hueSaturation", "presetKind": {"_enum": "presetKindType", "_value": "presetKindDefault"}, "colorize": False}}},
            {"_obj": "set", "$note": "hue/sat values", "null": {"_ref": "adjustmentLayer", "_enum": "ordinal", "_value": "targetEnum"},
             "to": {"_obj": "hueSaturation", "presetKind": {"_enum": "presetKindType", "_value": "presetKindCustom"}, "colorize": colorize, "adjustment": adj}}]


for nm, master, rng, col in (("combo122_100_-37", (122, 100, -37), (), False), ("light-37", (0, 0, -37), (), False), ("sat50", (0, 50, 0), (), False),
                             ("reds-hue60", (0, 0, 0), ((1, (315, 345, 15, 45), (60, 0, 0)),), False), ("colorize120_100_0", (120, 100, 0), (), True)):
    J(f"jhs-{nm}", "huesat", dict(kind="PS-authored", master=master, ranges=[list(r[1]) + list(r[2]) for r in rng], colorize=col),
      {"open": HS_INPUT, "steps": hs_steps(master, rng, col)})

# ------------------------------------------------------------------ 6 lower priority (brief)
for op in (100, 50):
    P(f"misc-dissolve-o{op}", "misc", dict(kind="dissolve", opacity=op), [c("shape.create", kind="rect", rect=[40, 40, 176, 176], fill="#ff0000"), c("layer.setProps", blend="Dissolve", opacity=op / 100)])
for sz, sp in ((5, 0), (20, 0), (20, 50)):
    P(f"misc-dropshadow-s{sz}-sp{sp}", "misc", dict(kind="dropShadow", size=sz, spread=sp),
      [c("shape.create", kind="rect", rect=[60, 60, 120, 100], fill="#ffff00"), c("layer.layerStyle.dropShadow", color="#000000", opacity=100, blend="normal", angle=135, distance=10, size=sz, spread=sp)])
for sz in (5, 20):
    P(f"misc-outerglow-s{sz}", "misc", dict(kind="outerGlow", size=sz), [c("shape.create", kind="rect", rect=[60, 60, 120, 100], fill="#ffff00"), c("layer.layerStyle.outerGlow", color="#0000ff", opacity=100, blend="normal", size=sz)])
for tech in ("smooth", "chiselHard"):
    P(f"misc-bevel-{tech}", "misc", dict(kind="bevel", technique=tech), [c("shape.create", kind="rect", rect=[60, 60, 120, 100], fill="#ffff00"), c("layer.layerStyle.bevelEmboss", style="inner", technique=tech, size=10)])


# ------------------------------------------------------------------ write
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--filter")
    a = ap.parse_args()
    import re
    os.makedirs(os.path.join(a.stage, "psds"), exist_ok=True)
    os.makedirs(os.path.join(a.stage, "inputs"), exist_ok=True)
    make_hs_input(os.path.join(a.stage, HS_INPUT))
    make_tile(os.path.join(a.stage, PAT_INPUT))
    cases, files, errors = [], [], []
    for p in probes:
        if a.filter and not re.search(a.filter, p["id"]):
            continue
        case = {"id": p["id"], "family": p["family"], "track": p["track"], "vary": p["vary"], "ops": [],
                "outputs": [{"name": "canvas", "type": "canvas"}, {"name": "resaved", "type": "psd"}]}
        if p["track"] == "P":
            rel = f"psds/{p['id']}.psd"
            argv = [a.cli, "run"] + ([os.path.join(a.stage, p["src"])] if p["src"] else ["--new", json.dumps(NEW)])
            for cmd, params in p["cmds"]:
                argv += ["--cmd", cmd, "--params", json.dumps(params)]
            argv += ["--out", os.path.join(a.stage, rel)]
            r = subprocess.run(argv, capture_output=True, text=True)
            if r.returncode != 0:
                errors.append((p["id"], r.stderr.strip()[-400:]))
                continue
            case["psd"] = rel
            case["file"] = p["id"]
            case["cmds"] = [[cmd, params] for cmd, params in p["cmds"]]
            files.append({"id": p["id"], "psd": rel, "width": W, "height": H})
        else:
            case["psd"] = None
            case["build"] = p["build"]
        cases.append(case)
    man = {"version": 1, "kind": "ps-probes", "colour": "no conversion; 8-bit sRGB documents", "files": files, "cases": cases}
    json.dump(man, open(os.path.join(a.stage, "cases.json"), "w"), indent=1)
    fam = {}
    for cs in cases:
        fam.setdefault((cs["family"], cs["track"]), 0)
        fam[(cs["family"], cs["track"])] += 1
    for k in sorted(fam):
        print(f"{k[0]:10s} {k[1]} {fam[k]}")
    print(len(cases), "probes,", len(errors), "generation errors")
    for e in errors:
        print("GEN-ERROR", e[0], e[1], file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
