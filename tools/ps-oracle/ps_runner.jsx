// photocraft ps-oracle: Photoshop-side reference runner (ExtendScript, ES3).
//
// Run through run-photoshop.sh (osascript `do javascript <source> with arguments {...}`):
//   arguments[0] = bundle dir (contains cases.json and psds/)
//   arguments[1] = output dir (reference PNGs + results.json + DONE)
//   arguments[2] = optional case-id filter (JavaScript RegExp source)
// Or File > Scripts > Browse… : it then asks for the bundle folder and writes to <bundle>/ps-out.
//
// For every case: open the PSD fresh, apply ops (hide/show/move/rasterize), write outputs,
// close every document it opened WITHOUT saving. Every case is wrapped in try/catch; results
// are rewritten to results.json after each case so a crash keeps partial results.
//
// Colour: no conversion anywhere. PNGs come from Save As PNG (as a copy) on the document or on a
// "Duplicate Layer > New document" document, which keeps the document's raw channel values and
// transparency. 16/32-bit documents are converted to 8 bits on a duplicate first. No Save for
// Web / Quick Export (they may convert to sRGB) except the optional slices SFW extra.

#target photoshop

var ARGS = (typeof arguments !== "undefined" && arguments) ? arguments : [];

function cTID(s) { return charIDToTypeID(s); }
function sTID(s) { return stringIDToTypeID(s); }

// ---------------------------------------------------------------- JSON (ExtendScript has none)
function jsonParse(s) { return eval("(" + s + ")"); }
function jsonQuote(s) {
    var out = '"';
    for (var i = 0; i < s.length; i++) {
        var c = s.charAt(i), n = s.charCodeAt(i);
        if (c === '"') out += '\\"';
        else if (c === "\\") out += "\\\\";
        else if (c === "\n") out += "\\n";
        else if (c === "\r") out += "\\r";
        else if (c === "\t") out += "\\t";
        else if (n < 32 || n > 126) { var h = n.toString(16); while (h.length < 4) h = "0" + h; out += "\\u" + h; }
        else out += c;
    }
    return out + '"';
}
function jsonStr(v, ind) {
    ind = ind || "";
    var ind2 = ind + " ";
    if (v === null || v === undefined) return "null";
    var t = typeof v;
    if (t === "number") return isFinite(v) ? String(v) : "null";
    if (t === "boolean") return v ? "true" : "false";
    if (t === "string") return jsonQuote(v);
    if (v instanceof Array) {
        if (!v.length) return "[]";
        var a = [];
        for (var i = 0; i < v.length; i++) a.push(ind2 + jsonStr(v[i], ind2));
        return "[\n" + a.join(",\n") + "\n" + ind + "]";
    }
    var parts = [];
    for (var k in v) if (v.hasOwnProperty(k)) parts.push(ind2 + jsonQuote(k) + ": " + jsonStr(v[k], ind2));
    if (!parts.length) return "{}";
    return "{\n" + parts.join(",\n") + "\n" + ind + "}";
}

// ---------------------------------------------------------------- files
function readText(path) {
    var f = new File(path);
    f.encoding = "UTF-8";
    if (!f.open("r")) throw new Error("cannot read " + path);
    var s = f.read();
    f.close();
    return s;
}
function writeText(path, s) {
    var f = new File(path);
    f.encoding = "UTF-8";
    f.lineFeed = "Unix";
    if (!f.open("w")) throw new Error("cannot write " + path);
    f.write(s);
    f.close();
}
function ensureDir(path) { var d = new Folder(path); if (!d.exists) d.create(); return d; }
function nowMs() { return new Date().getTime(); }

// ---------------------------------------------------------------- layers
function pathStr(p) {
    var a = [];
    for (var i = 0; i < p.length; i++) a.push(p[i].name);
    return a.join("/");
}

// Resolve a [{name,index}] path; index counts siblings from the top (doc.layers order).
function resolve(doc, path, warnings) {
    var cont = doc, layer = null;
    for (var i = 0; i < path.length; i++) {
        var st = path[i], ls = cont.layers, cand = null;
        if (st.index < ls.length && ls[st.index].name === st.name) cand = ls[st.index];
        else if (cont === doc && st.index === ls.length - 1 && ls[st.index].isBackgroundLayer) {
            // PS shows the Background layer under its localized name (e.g. "背景"), not the stored one ("Фон").
            cand = ls[st.index];
            warnings.push("path step '" + st.name + "' matched the Background layer by index (PS name '" + cand.name + "')");
        } else {
            for (var j = 0; j < ls.length; j++) if (ls[j].name === st.name) { cand = ls[j]; break; }
            if (cand) warnings.push("path step '" + st.name + "' found by name, not at index " + st.index);
        }
        if (!cand) throw new Error("layer not found: " + pathStr(path) + " (step " + i + ")");
        layer = cand;
        if (i < path.length - 1) {
            if (layer.typename !== "LayerSet") throw new Error("not a group: " + st.name);
            cont = layer;
        }
    }
    return layer;
}

function layerId(layer) {
    var id = layer.id;
    if (id === undefined || id === null) throw new Error("layer.id unavailable");
    return id;
}

function refById(id) {
    var r = new ActionReference();
    r.putIdentifier(cTID("Lyr "), id);
    return r;
}

function refTarget() {
    var r = new ActionReference();
    r.putEnumerated(cTID("Lyr "), cTID("Ordn"), cTID("Trgt"));
    return r;
}

// Select a layer by id without making it visible (doc.activeLayer = x would show it).
// NB: on PS 27.5 `rasterizeLayer` and `make document using layer` IGNORE an identifier
// reference and act on the target (selected) layer, so every AM call selects first and
// then uses the target-enum reference, exactly as ScriptListener records it.
function selectLayer(doc, layer) {
    app.activeDocument = doc;
    selectById(layerId(layer));
    var a = doc.activeLayer;
    if (a.id !== layer.id) throw new Error("select failed: active layer is '" + a.name + "', wanted '" + layer.name + "'");
}
function selectById(id) {
    var d = new ActionDescriptor();
    d.putReference(cTID("null"), refById(id));
    d.putBoolean(cTID("MkVs"), false);
    executeAction(cTID("slct"), d, DialogModes.NO);
}

// ---------------------------------------------------------------- ops
function opMove(doc, op, warnings) {
    var layer = resolve(doc, op.path, warnings);
    var target = resolve(doc, op.target, warnings);
    if (op.position === "above") { layer.move(target, ElementPlacement.PLACEBEFORE); return "dom PLACEBEFORE"; }
    if (op.position === "below") { layer.move(target, ElementPlacement.PLACEAFTER); return "dom PLACEAFTER"; }
    if (op.position === "into") {
        if (target.typename !== "LayerSet") throw new Error("move into: target is not a group");
        if (target.layers.length > 0) { layer.move(target.layers[0], ElementPlacement.PLACEBEFORE); return "dom PLACEBEFORE first child"; }
        layer.move(target, ElementPlacement.PLACEATBEGINNING);
        return "dom PLACEATBEGINNING";
    }
    throw new Error("bad position " + op.position);
}

var RASTER_AM = { type: "type", shape: "shape", smartObject: "placed", layerStyle: "layerStyle", layer: "entire", fillContent: "content", vectorMask: "vectorMask" };

function opRasterize(doc, op, warnings) {
    var layer = resolve(doc, op.path, warnings);
    var am = RASTER_AM[op.what];
    var err1 = null;
    if (am) {
        try {
            selectLayer(doc, layer);
            var d = new ActionDescriptor();
            d.putReference(cTID("null"), refTarget());
            d.putEnumerated(sTID("what"), sTID("rasterizeItem"), sTID(am));
            executeAction(sTID("rasterizeLayer"), d, DialogModes.NO);
            return "am rasterizeLayer(target) what=" + am;
        } catch (e) { err1 = String(e); }
    }
    if (op.what === "smartObject") {
        try {
            selectLayer(doc, layer);
            executeAction(sTID("rasterizePlaced"), undefined, DialogModes.NO);
            warnings.push("AM rasterizeLayer failed (" + err1 + "); used rasterizePlaced");
            return "am rasterizePlaced";
        } catch (e2) { err1 += " | rasterizePlaced: " + e2; }
    }
    // DOM fallback (needs the layer selected; DOM rasterize works on the object directly).
    var dom = { type: RasterizeType.TEXTCONTENTS, shape: RasterizeType.SHAPE, layer: RasterizeType.ENTIRELAYER, fillContent: RasterizeType.FILLCONTENT };
    if (dom[op.what] !== undefined) {
        layer.rasterize(dom[op.what]);
        warnings.push("AM rasterize failed (" + err1 + "); used DOM");
        return "dom rasterize " + op.what;
    }
    throw new Error("rasterize " + op.what + " failed: " + err1);
}

function applyOp(doc, op, warnings) {
    if (op.op === "hide" || op.op === "show") {
        var l = resolve(doc, op.path, warnings);
        l.visible = (op.op === "show");
        return "dom visible=" + l.visible;
    }
    if (op.op === "move") return opMove(doc, op, warnings);
    if (op.op === "rasterize") return opRasterize(doc, op, warnings);
    throw new Error("unknown op " + op.op);
}

// ---------------------------------------------------------------- outputs
function pngOpts() {
    var o = new PNGSaveOptions();
    try { o.compression = 6; } catch (e) {}
    o.interlaced = false;
    return o;
}

// Save `doc` as an 8-bit PNG copy; returns {width,height}. Converts depth on a duplicate.
function savePng(doc, path) {
    app.activeDocument = doc;
    var d = doc, tmp = null;
    if (doc.bitsPerChannel !== BitsPerChannelType.EIGHT) {
        tmp = doc.duplicate("pso-8bit");
        tmp.bitsPerChannel = BitsPerChannelType.EIGHT;
        d = tmp;
    }
    d.saveAs(new File(path), pngOpts(), true, Extension.LOWERCASE);
    var r = { width: Number(d.width.as("px")), height: Number(d.height.as("px")) };
    if (tmp) tmp.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = doc;
    return r;
}

// Save `doc` as a layered PSD copy (Photoshop's own encoding of the probe, re-rendered on the box).
function savePsd(doc, path) {
    app.activeDocument = doc;
    var o = new PhotoshopSaveOptions();
    o.layers = true;
    o.embedColorProfile = true;
    try { o.alphaChannels = true; } catch (e) {}
    try { o.spotColors = true; } catch (e) {}
    doc.saveAs(new File(path), o, true, Extension.LOWERCASE);
}

// Duplicate Layer > Destination: New document (AM `make document using layer`, target form).
// Returns the new document; it is closed again here if anything after its creation fails.
function dupLayerToNewDoc(doc, layer, warnings) {
    var before = app.documents.length;
    selectLayer(doc, layer);
    var d = new ActionDescriptor();
    var r = new ActionReference();
    r.putClass(cTID("Dcmn"));
    d.putReference(cTID("null"), r);
    d.putString(cTID("Nm  "), "pso-layer");
    d.putReference(cTID("Usng"), refTarget());
    d.putInteger(cTID("Vrsn"), 5);
    executeAction(cTID("Mk  "), d, DialogModes.NO);
    if (app.documents.length <= before) throw new Error("duplicate to new document made no document");
    var nd = app.activeDocument;
    try {
        if (nd.layers.length !== 1 || nd.layers[0].name !== layer.name)
            throw new Error("new document holds '" + (nd.layers.length ? nd.layers[0].name : "") + "' (" + nd.layers.length + " layers), wanted '" + layer.name + "'");
        // the copied layer itself is shown (photocraft and PS quick-export render the layer even if hidden)
        if (!nd.layers[0].visible) nd.layers[0].visible = true;
    } catch (e) {
        nd.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = doc;
        throw e;
    }
    return nd;
}

function unionBounds(nd) {
    var b = null;
    for (var i = 0; i < nd.layers.length; i++) {
        var l = nd.layers[i];
        if (!l.visible) continue;
        var x = [Number(l.bounds[0].as("px")), Number(l.bounds[1].as("px")), Number(l.bounds[2].as("px")), Number(l.bounds[3].as("px"))];
        if (!b) b = x; else b = [Math.min(b[0], x[0]), Math.min(b[1], x[1]), Math.max(b[2], x[2]), Math.max(b[3], x[3])];
    }
    if (!b) return null;
    var W = Number(nd.width.as("px")), H = Number(nd.height.as("px"));
    b = [Math.max(0, b[0]), Math.max(0, b[1]), Math.min(W, b[2]), Math.min(H, b[3])];
    return (b[2] > b[0] && b[3] > b[1]) ? b : null;
}

// Trim a "make document using layer" document to its content. Fallbacks, in order: a linked
// smart object whose file is missing makes trim AND crop refuse ("缺少链接的智能对象文件"), so
// rasterize the (temporary) copy and trim again; then crop to the layer bounds; then keep the
// whole canvas and say so (res.untrimmed = true, the comparison reports it as SIZE).
function trimLayerDoc(nd, spec, res, warnings) {
    var errs = [];
    try { nd.trim(TrimType.TRANSPARENT, true, true, true, true); res.method += " + trim transparent"; return; }
    catch (e) { errs.push("trim: " + e); }
    try {
        app.activeDocument = nd;
        selectById(layerId(nd.layers[0]));
        var d = new ActionDescriptor();
        d.putReference(cTID("null"), refTarget());
        d.putEnumerated(sTID("what"), sTID("rasterizeItem"), sTID("placed"));
        executeAction(sTID("rasterizeLayer"), d, DialogModes.NO);
        nd.trim(TrimType.TRANSPARENT, true, true, true, true);
        warnings.push(spec.name + ": trim refused (" + errs[0] + "); rasterized the copy's smart object, then trimmed");
        res.method += " + rasterize placed (copy) + trim transparent";
        return;
    } catch (e2) { errs.push("rasterize+trim: " + e2); }
    try {
        var b = unionBounds(nd);
        if (!b) throw new Error("layer bounds empty");
        nd.crop(b);
        warnings.push(spec.name + ": " + errs.join(" | ") + "; cropped to layer bounds");
        res.method += " + crop to layer bounds";
        return;
    } catch (e3) { errs.push("crop: " + e3); }
    warnings.push(spec.name + ": could not trim (" + errs.join(" | ") + "); saved the untrimmed canvas");
    res.method += " (UNTRIMMED: trim, rasterize and crop all refused)";
    res.untrimmed = true;
}

function outLayer(doc, spec, dir, res, warnings) {
    var layer = resolve(doc, spec.path, warnings);
    var nd = dupLayerToNewDoc(doc, layer, warnings);
    try {
        res.method = "make document using layer(target)";
        if (spec.trim) trimLayerDoc(nd, spec, res, warnings);
        var sz = savePng(nd, dir + "/" + spec.name + ".png");
        res.width = sz.width; res.height = sz.height;
    } finally {
        nd.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = doc;
    }
}

function outSlices(doc, spec, dir, res, warnings) {
    res.files = [];
    for (var i = 0; i < spec.slices.length; i++) {
        var s = spec.slices[i];
        var nm = "slice_" + (s.number < 10 ? "0" : "") + s.number;
        var dup = doc.duplicate("pso-slice");
        try {
            dup.crop([s.rect[0], s.rect[1], s.rect[0] + s.rect[2], s.rect[1] + s.rect[3]]);
            savePng(dup, dir + "/" + nm + ".png");
            res.files.push(nm + ".png");
        } finally {
            dup.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc;
        }
    }
    res.method = "duplicate + crop to slice rect + save PNG";
    // Save for Web extra removed: PS pops a modal "target folder does not exist" that blocks the batch.
}

function writeOutput(doc, spec, dir, warnings) {
    var res = { name: spec.name, type: spec.type, ok: false };
    var t0 = nowMs();
    try {
        if (spec.type === "canvas") {
            var sz = savePng(doc, dir + "/" + spec.name + ".png");
            res.width = sz.width; res.height = sz.height;
            res.method = "save as PNG copy";
        } else if (spec.type === "layer") {
            outLayer(doc, spec, dir, res, warnings);
        } else if (spec.type === "psd") {
            savePsd(doc, dir + "/" + spec.name + ".psd");
            res.method = "save as PSD copy (layers, maximize compatibility)";
        } else if (spec.type === "slices") {
            outSlices(doc, spec, dir, res, warnings);
        } else throw new Error("unknown output type " + spec.type);
        res.ok = true;
    } catch (e) {
        res.error = String(e) + (e.line ? " (line " + e.line + ")" : "");
    }
    res.ms = nowMs() - t0;
    return res;
}

// ---------------------------------------------------------------- fonts
var fontCache = {};
function missingFonts(doc) {
    var miss = [];
    function walk(ls) {
        for (var i = 0; i < ls.length; i++) {
            var l = ls[i];
            if (l.typename === "LayerSet") walk(l.layers);
            else if (l.kind === LayerKind.TEXT) {
                var f = null;
                try { f = l.textItem.font; } catch (e) { miss.push("?" + l.name); continue; }
                if (fontCache[f] === undefined) {
                    try { app.fonts.getByName(f); fontCache[f] = true; } catch (e2) { fontCache[f] = false; }
                }
                if (!fontCache[f]) miss.push(f);
            }
        }
    }
    try { walk(doc.layers); } catch (e) { miss.push("scan failed: " + e); }
    return miss;
}

// ---------------------------------------------------------------- build-from-scratch probes
// A case with `build` instead of `psd` makes its document in Photoshop:
//   build = { "new": {"width","height","fill":"white|transparent"} | "open": "<bundle-relative image>",
//             "steps": [ <action descriptor in batchPlay-like JSON>, ... ] }
// A step is {"_obj": <event stringID>, key: value, ...} run with executeAction(…, DialogModes.NO).
// Values: number → double, true/false → boolean, "s" → string, {"_int": n} → integer,
// {"_unit": "pixelsUnit", "_value": v} → unit double, {"_enum": type, "_value": v} → enumerated,
// {"_class": c} → class, {"_obj": c, ...} → object, [ ... ] → list, {"_path": rel} → path in the bundle,
// {"_ref": c | [ {"_ref": c, "_enum"/"_value" | "_id" | "_name" | "_index" | "_property"}, ... ]} → reference.
// Keys and ids are stringIDs; "#Abcd" is a charID; the key "null" is the charID "null".
// Each step's outcome is recorded in the case's `steps` (a failed step fails the case).
function amKey(k) { return (k === "null") ? cTID("null") : (k.charAt(0) === "#" ? cTID(k.substr(1)) : sTID(k)); }
function amId(v) { return (v.charAt(0) === "#") ? cTID(v.substr(1)) : sTID(v); }
function amRef(v) {
    var r = new ActionReference();
    var parts = (typeof v._ref === "string") ? [v] : v._ref;
    for (var i = 0; i < parts.length; i++) {
        var p = parts[i], c = amId(p._ref);
        if (p._property !== undefined) r.putProperty(c, amId(p._property));
        else if (p._enum !== undefined) r.putEnumerated(c, amId(p._enum), amId(p._value));
        else if (p._id !== undefined) r.putIdentifier(c, p._id);
        else if (p._index !== undefined) r.putIndex(c, p._index);
        else if (p._name !== undefined) r.putName(c, p._name);
        else if (p._offset !== undefined) r.putOffset(c, p._offset);
        else r.putClass(c);
    }
    return r;
}
function amPut(target, isList, k, v, bundle) {
    // target: an ActionDescriptor (key k) or an ActionList (k unused). Explicit calls, no
    // Function.apply on host methods.
    var t = typeof v, L = isList;
    if (t === "number") { if (L) target.putDouble(v); else target.putDouble(k, v); return; }
    if (t === "boolean") { if (L) target.putBoolean(v); else target.putBoolean(k, v); return; }
    if (t === "string") { if (L) target.putString(v); else target.putString(k, v); return; }
    if (v instanceof Array) { var l = amList(v, bundle); if (L) target.putList(l); else target.putList(k, l); return; }
    if (v._ref !== undefined) { var r = amRef(v); if (L) target.putReference(r); else target.putReference(k, r); return; }  // before _enum: a reference may carry _enum
    if (v._int !== undefined) { if (L) target.putInteger(v._int); else target.putInteger(k, v._int); return; }
    if (v._unit !== undefined) { if (L) target.putUnitDouble(amId(v._unit), v._value); else target.putUnitDouble(k, amId(v._unit), v._value); return; }
    if (v._enum !== undefined) { if (L) target.putEnumerated(amId(v._enum), amId(v._value)); else target.putEnumerated(k, amId(v._enum), amId(v._value)); return; }
    if (v._class !== undefined) { if (L) target.putClass(amId(v._class)); else target.putClass(k, amId(v._class)); return; }
    if (v._path !== undefined) { var f = new File(bundle + "/" + v._path); if (L) target.putPath(f); else target.putPath(k, f); return; }
    if (v._obj !== undefined) { var d = amDesc(v, bundle); if (L) target.putObject(amId(v._obj), d); else target.putObject(k, amId(v._obj), d); return; }
    throw new Error("am: cannot encode " + jsonStr(v));
}
function amDesc(o, bundle) {
    var d = new ActionDescriptor();
    for (var k in o) if (o.hasOwnProperty(k) && k !== "_obj" && k.charAt(0) !== "$") amPut(d, false, amKey(k), o[k], bundle);
    return d;
}
function amList(a, bundle) {
    var l = new ActionList();
    for (var i = 0; i < a.length; i++) amPut(l, true, null, a[i], bundle);
    return l;
}
function buildDoc(bundle, b, r) {
    var doc;
    if (b.open) {
        doc = app.open(new File(bundle + "/" + b.open));
    } else {
        var n = b["new"];
        doc = app.documents.add(n.width, n.height, 72, "probe", NewDocumentMode.RGB,
                                n.fill === "transparent" ? DocumentFill.TRANSPARENT : DocumentFill.WHITE, 1, BitsPerChannelType.EIGHT, "sRGB IEC61966-2.1");
    }
    app.activeDocument = doc;
    r.steps = [];
    var steps = b.steps || [];
    for (var i = 0; i < steps.length; i++) {
        var st = steps[i];
        try {
            executeAction(amId(st._obj), amDesc(st, bundle), DialogModes.NO);
            r.steps.push({ step: i, event: st._obj, note: st.$note || null, ok: true });
        } catch (e) {
            r.steps.push({ step: i, event: st._obj, note: st.$note || null, ok: false, error: String(e) });
            throw new Error("build step " + i + " (" + st._obj + (st.$note ? " " + st.$note : "") + ") failed: " + e);
        }
    }
    return doc;
}

// ---------------------------------------------------------------- main
function closeExtra(keep) {
    for (var guard = 0; guard < 50 && app.documents.length > 0; guard++) {
        var found = null;
        for (var i = 0; i < app.documents.length; i++) {
            var d = app.documents[i], isKeep = false;
            for (var j = 0; j < keep.length; j++) if (keep[j] === d) isKeep = true;
            if (!isKeep) { found = d; break; }
        }
        if (!found) return;
        try { found.close(SaveOptions.DONOTSAVECHANGES); } catch (e) { return; }
    }
}

function runCase(bundle, out, c) {
    var r = { id: c.id, psd: c.psd, ok: false, ops: [], outputs: [], warnings: [] };
    var t0 = nowMs();
    var keep = [];
    for (var i = 0; i < app.documents.length; i++) keep.push(app.documents[i]);
    try {
        var doc;
        if (c.build) doc = buildDoc(bundle, c.build, r);
        else {
            var f = new File(bundle + "/" + c.psd);
            if (!f.exists) throw new Error("missing " + f.fsName);
            doc = app.open(f);
        }
        app.activeDocument = doc;
        r.docMode = String(doc.mode);
        r.docBits = String(doc.bitsPerChannel);
        try { r.profile = doc.colorProfileName; } catch (e) { r.profile = null; }
        r.missingFonts = missingFonts(doc);
        for (var k = 0; k < c.ops.length; k++) {
            var o = c.ops[k];
            try { r.ops.push({ op: o.op, ok: true, method: applyOp(doc, o, r.warnings) }); }
            catch (eo) { r.ops.push({ op: o.op, ok: false, error: String(eo) }); throw new Error("op " + k + " (" + o.op + ") failed: " + eo); }
        }
        var dir = ensureDir(out + "/" + c.id).fsName;
        var allOk = true;
        for (var m = 0; m < c.outputs.length; m++) {
            var res = writeOutput(doc, c.outputs[m], dir, r.warnings);
            if (!res.ok) allOk = false;
            r.outputs.push(res);
        }
        r.ok = allOk;
        if (!allOk) r.error = "some outputs failed";
    } catch (e) {
        r.error = String(e) + (e.line ? " (line " + e.line + ")" : "");
    } finally {
        closeExtra(keep);
    }
    r.ms = nowMs() - t0;
    return r;
}

function main() {
    var bundle = ARGS.length > 0 ? String(ARGS[0]) : null;
    if (!bundle) {
        var fd = Folder.selectDialog("ps-oracle bundle folder (contains cases.json)");
        if (!fd) return "cancelled";
        bundle = fd.fsName;
    }
    var out = ARGS.length > 1 && ARGS[1] ? String(ARGS[1]) : bundle + "/ps-out";
    var filter = ARGS.length > 2 && ARGS[2] ? new RegExp(String(ARGS[2])) : null;
    ensureDir(out);
    var doneF = new File(out + "/DONE");
    if (doneF.exists) doneF.remove();

    var manifest = jsonParse(readText(bundle + "/cases.json"));
    var saved = { ruler: app.preferences.rulerUnits, type: app.preferences.typeUnits, dialogs: app.displayDialogs, compat: null };
    // Probe PSDs are re-read on the box: keep the composite (maximize compatibility) in them.
    try { saved.compat = app.preferences.maximizeCompatibility; app.preferences.maximizeCompatibility = QueryStateType.ALWAYS; } catch (e) { saved.compat = null; }
    app.preferences.rulerUnits = Units.PIXELS;
    app.preferences.typeUnits = TypeUnits.PIXELS;
    app.displayDialogs = DialogModes.NO;

    var env = { app: "Adobe Photoshop", version: app.version, build: (function () { try { return app.build; } catch (e) { return null; } })(),
                os: $.os, colorSettings: (function () { try { return app.colorSettings; } catch (e) { return null; } })(),
                started: new Date().toString() };
    var results = { env: env, cases: [] };
    var nOk = 0, nFail = 0;
    try {
        for (var i = 0; i < manifest.cases.length; i++) {
            var c = manifest.cases[i];
            if (filter && !filter.test(c.id)) continue;
            var r = runCase(bundle, out, c);
            if (r.ok) nOk++; else nFail++;
            results.cases.push(r);
            results.progress = { done: results.cases.length, ok: nOk, failed: nFail };
            writeText(out + "/results.json", jsonStr(results) + "\n");
        }
    } finally {
        app.preferences.rulerUnits = saved.ruler;
        app.preferences.typeUnits = saved.type;
        app.displayDialogs = saved.dialogs;
        if (saved.compat !== null) { try { app.preferences.maximizeCompatibility = saved.compat; } catch (e) {} }
    }
    results.env.finished = new Date().toString();
    writeText(out + "/results.json", jsonStr(results) + "\n");
    writeText(out + "/DONE", "ok=" + nOk + " failed=" + nFail + "\n");
    return "ps-oracle: " + nOk + " ok, " + nFail + " failed -> " + out;
}

main();
