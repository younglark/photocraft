// 1) ES3 syntax check of ps_runner.jsx with acorn (ExtendScript is ES3).
// 2) Dry run: executes ps_runner.jsx in node against a mock Photoshop DOM built from
//    photocraft-cli `info` JSON, so every case's path resolution, op dispatch, output
//    bookkeeping and results.json writing run for real (pixels are not produced).
// Usage: node check_jsx.js <ps_runner.jsx> <bundle_dir> <info_dir> <out_dir>
//   info_dir holds <file id>.json (photocraft-cli info --compact output), made by run_photocraft.py --dump-info.
const fs = require('fs'), path = require('path'), vm = require('vm');
let acorn;
try { acorn = require('acorn'); } catch (e) { acorn = require(process.env.ACORN || '/tmp/jscheck/node_modules/acorn'); }
const [jsxPath, bundle, infoDir, outDir] = process.argv.slice(2);
const raw = fs.readFileSync(jsxPath, 'utf8');
const src = raw.replace(/^#.*$/mg, '');
acorn.parse(src, { ecmaVersion: 3, allowReserved: false });
console.log('ES3 parse OK:', jsxPath);
if (!bundle) process.exit(0);

const manifest = JSON.parse(fs.readFileSync(path.join(bundle, 'cases.json'), 'utf8'));
const byPsd = {};
for (const f of manifest.files) byPsd[f.psd] = f.id;
const log = [];
let nextId = 100000;

function mkLayer(j, parent) {
  const isSet = j.kind === 'Group';
  const l = { name: j.name, id: j.id, isBackgroundLayer: !!j.isBackground, bounds: (j.bounds || [0, 0, 1, 1]).map((v, i, b) => unit(i < 2 ? v : b[i - 2] + v)), typename: isSet ? 'LayerSet' : 'ArtLayer', visible: j.visible, parent,
    kind: j.kind === 'Type' ? 'TEXT' : 'NORMAL', layers: [] };
  if (j.kind === 'Type') l.textItem = { font: 'ArialMT' };
  if (isSet) l.layers = j.children.map(c => mkLayer(c, l));
  l.move = function (target, where) {
    const from = this.parent.layers; from.splice(from.indexOf(this), 1);
    let dest, idx;
    if (where === 'PLACEBEFORE' || where === 'PLACEAFTER') { dest = target.parent.layers; idx = dest.indexOf(target) + (where === 'PLACEAFTER' ? 1 : 0); this.parent = target.parent; }
    else if (where === 'PLACEATBEGINNING') { dest = target.layers; idx = 0; this.parent = target; }
    else throw new Error('mock: bad placement ' + where);
    dest.splice(idx, 0, this); log.push('move ' + this.name + ' ' + where + ' ' + target.name);
  };
  l.rasterize = function (t) { log.push('dom rasterize ' + this.name + ' ' + t); };
  l.duplicate = function () { throw new Error('mock: not used'); };
  return l;
}
function findId(ls, id) { for (const l of ls) { if (l.id === id) return l; const r = findId(l.layers, id); if (r) return r; } return null; }
function unit(v) { return { as: () => v }; }
const docs = [];
let active = null;
function mkDoc(name, w, h, layers) {
  const d = { name, width: unit(w), height: unit(h), layers, mode: 'DocumentMode.RGB', bitsPerChannel: 'EIGHT', colorProfileName: 'sRGB IEC61966-2.1' };
  layers.forEach(l => l.parent = d);
  d.saveAs = (f, o, copy) => { if (!copy) throw new Error('mock: saveAs must be a copy'); fs.writeFileSync(f.fsName, ''); log.push('save ' + f.fsName); };
  d.close = () => { docs.splice(docs.indexOf(d), 1); active = docs[docs.length - 1] || null; };
  d.duplicate = (n) => { const c = mkDoc(n, w, h, []); return c; };
  d.crop = (b) => { if (b.length !== 4 || b[2] <= b[0] || b[3] <= b[1]) throw new Error('mock: bad crop'); };
  d.trim = () => { if (process.env.TRIM_FAIL && name === 'pso-layer') throw new Error('mock: missing linked smart object file'); };
  d.exportDocument = () => {};
  docs.push(d); active = d;
  return d;
}
class File {
  constructor(p) { this.fsName = String(p); this.name = path.basename(this.fsName); }
  get exists() { return fs.existsSync(this.fsName); }
  open(m) { this.m = m; this.buf = ''; return m === 'w' || fs.existsSync(this.fsName); }
  read() { return fs.readFileSync(this.fsName, 'utf8'); }
  write(s) { this.buf += s; }
  close() { if (this.m === 'w') fs.writeFileSync(this.fsName, this.buf); }
  remove() { fs.unlinkSync(this.fsName); }
}
class Folder {
  constructor(p) { this.fsName = String(p); }
  get exists() { return fs.existsSync(this.fsName); }
  create() { fs.mkdirSync(this.fsName, { recursive: true }); return true; }
  getFiles() { return fs.readdirSync(this.fsName).map(n => new File(path.join(this.fsName, n))); }
}
// Typed mock descriptors: every put checks its argument types the way Photoshop's do
// (typeIDs are the mock's 'c:'/'s:' strings, numbers must be finite), and keeps a tagged copy
// so build steps can be dumped and compared with the JSON they came from.
const isId = (x) => typeof x === 'string' && /^[cs]:/.test(x);
const num = (x, what) => { if (typeof x !== 'number' || !isFinite(x)) throw new Error('mock: ' + what + ' wants a number, got ' + JSON.stringify(x)); return x; };
const key = (k) => { if (!isId(k)) throw new Error('mock: descriptor key is not a typeID: ' + JSON.stringify(k)); return k; };
class AD { constructor() { this.v = {}; }
  putReference(k, r) { if (!(r instanceof AR)) throw new Error('mock: putReference wants an ActionReference'); this.v[key(k)] = r; }
  putString(k, s) { if (typeof s !== 'string') throw new Error('mock: putString'); this.v[key(k)] = s; }
  putInteger(k, i) { if (!Number.isInteger(i)) throw new Error('mock: putInteger wants an integer, got ' + i); this.v[key(k)] = { int: i }; }
  putDouble(k, x) { this.v[key(k)] = { dbl: num(x, 'putDouble') }; }
  putUnitDouble(k, u, x) { if (!isId(u)) throw new Error('mock: unit'); this.v[key(k)] = { unit: u, value: num(x, 'putUnitDouble') }; }
  putBoolean(k, b) { if (typeof b !== 'boolean') throw new Error('mock: putBoolean'); this.v[key(k)] = b; }
  putEnumerated(k, t, e) { if (!isId(t) || !isId(e)) throw new Error('mock: putEnumerated'); this.v[key(k)] = e; this.enumTypes = Object.assign(this.enumTypes || {}, { [k]: t }); }
  putClass(k, c) { if (!isId(c)) throw new Error('mock: putClass'); this.v[key(k)] = { cls: c }; }
  putObject(k, c, d) { if (!isId(c) || !(d instanceof AD)) throw new Error('mock: putObject'); this.v[key(k)] = { obj: c, d }; }
  putList(k, l) { if (!(l instanceof AL)) throw new Error('mock: putList wants an ActionList'); this.v[key(k)] = l; }
  putPath(k, f) { if (!(f instanceof File)) throw new Error('mock: putPath wants a File'); if (!f.exists) throw new Error('mock: putPath: missing ' + f.fsName); this.v[key(k)] = { path: f.fsName }; }
  toJSON() { return this.v; } }
class AL { constructor() { this.items = []; }
  putDouble(x) { this.items.push({ dbl: num(x, 'list.putDouble') }); } putInteger(i) { if (!Number.isInteger(i)) throw new Error('mock: list int'); this.items.push({ int: i }); }
  putString(s) { this.items.push(s); } putBoolean(b) { this.items.push(b); }
  putUnitDouble(u, x) { this.items.push({ unit: u, value: num(x, 'list.putUnitDouble') }); }
  putEnumerated(t, e) { this.items.push(e); } putClass(c) { this.items.push({ cls: c }); }
  putObject(c, d) { if (!isId(c) || !(d instanceof AD)) throw new Error('mock: list.putObject'); this.items.push({ obj: c, d }); }
  putList(l) { this.items.push(l); } putReference(r) { this.items.push(r); } putPath(f) { this.items.push({ path: f.fsName }); }
  toJSON() { return this.items; } }
class AR { constructor() { this.parts = []; }
  putIdentifier(c, id) { this.id = id; this.parts.push([c, 'id', id]); } putClass(c) { this.cls = c; this.parts.push([c]); }
  putEnumerated(c, t, e) { this.target = true; this.parts.push([c, t, e]); } putProperty(c, p) { this.parts.push([c, 'prop', p]); }
  putIndex(c, i) { this.parts.push([c, 'index', i]); } putName(c, n) { this.parts.push([c, 'name', n]); } putOffset(c, o) { this.parts.push([c, 'offset', o]); }
  toJSON() { return this.parts; } }
const dumps = [];
const ctx = {
  arguments: [bundle, outDir, process.env.FILTER || ''],
  File, Folder, ActionDescriptor: AD, ActionReference: AR, ActionList: AL,
  PhotoshopSaveOptions: function () {}, QueryStateType: { ALWAYS: 'ALWAYS', ASK: 'ASK', NEVER: 'NEVER' },
  NewDocumentMode: { RGB: 'RGB' }, DocumentFill: { WHITE: 'WHITE', TRANSPARENT: 'TRANSPARENT' },
  charIDToTypeID: s => 'c:' + s, stringIDToTypeID: s => 's:' + s,
  DialogModes: { NO: 'NO', ALL: 'ALL' }, Units: { PIXELS: 'PX' }, TypeUnits: { PIXELS: 'PX' },
  ElementPlacement: { PLACEBEFORE: 'PLACEBEFORE', PLACEAFTER: 'PLACEAFTER', PLACEATBEGINNING: 'PLACEATBEGINNING', INSIDE: 'INSIDE' },
  RasterizeType: { TEXTCONTENTS: 'TEXTCONTENTS', SHAPE: 'SHAPE', ENTIRELAYER: 'ENTIRELAYER', FILLCONTENT: 'FILLCONTENT' },
  SaveOptions: { DONOTSAVECHANGES: 'DNS' }, Extension: { LOWERCASE: 'lc' }, TrimType: { TRANSPARENT: 'T' },
  BitsPerChannelType: { EIGHT: 'EIGHT', SIXTEEN: 'SIXTEEN' }, LayerKind: { TEXT: 'TEXT' }, SaveDocumentType: { PNG: 'PNG' }, ExportType: { SAVEFORWEB: 'SFW' },
  PNGSaveOptions: function () {}, ExportOptionsSaveForWeb: function () {},
  $: { os: 'mock', fileName: jsxPath },
  decodeURI,
  executeAction(ev, d) {
    const doc = active;
    // Real PS 27.5 behaviour: rasterizeLayer / make-document-using-layer act on the TARGET
    // (selected) layer and ignore identifier references; only `slct` honours an id.
    const tgt = (r) => { if (!r || !r.target) throw new Error('mock: PS 27.5 ignores id refs here; use target'); if (!doc.activeLayer) throw new Error('mock: nothing selected'); return doc.activeLayer; };
    if (ev === 's:rasterizeLayer') { const l = tgt(d.v['c:null']); log.push('am rasterize ' + l.name + ' ' + d.v['s:what']); return; }
    if (ev === 's:rasterizePlaced') { log.push('am rasterizePlaced ' + doc.activeLayer.name); return; }
    if (ev === 'c:Mk  ') { const l = tgt(d.v['c:Usng']); mkDoc('pso-layer', doc.width.as(), doc.height.as(), [mkLayer({ name: l.name, id: nextId++, kind: 'Pixel', visible: l.visible }, null)]); return; }
    if (ev === 'c:slct') { const l = findId(doc.layers, d.v['c:null'].id); if (!l) throw new Error('mock: select: no layer ' + d.v['c:null'].id); doc.activeLayer = l; return; }
    // build-step events (probe cases made in Photoshop): accept, record, add a layer for `make`
    if (ev === 's:make' || ev === 's:set') {
      if (!d.v['c:null']) throw new Error('mock: ' + ev + ' without a null reference');
      if (ev === 's:make') {
        const u = d.v['s:using'];
        if (!u || !u.obj) throw new Error('mock: make without a using object');
        const l = mkLayer({ name: u.obj.slice(2), id: nextId++, kind: 'Pixel', visible: true, bounds: [0, 0, doc.width.as(), doc.height.as()] }, doc);
        doc.layers.unshift(l); doc.activeLayer = l;
      } else if (!(d.v['c:null'].target)) throw new Error('mock: set must target the active layer');
      dumps.push({ ev, d: JSON.parse(JSON.stringify(d)) });
      log.push('am ' + ev); return;
    }
    throw new Error('mock: unexpected event ' + ev);
  },
  app: {
    version: 'mock', get activeDocument() { return active; }, set activeDocument(d) { active = d; },
    documents: Object.assign(docs, { add(w, h, res, name, mode, fill, ar, bits, prof) {
      if (![w, h, res].every(Number.isFinite)) throw new Error('mock: documents.add sizes'); if (fill === undefined || bits === undefined) throw new Error('mock: documents.add args');
      return mkDoc(name, w, h, fill === 'WHITE' ? [mkLayer({ name: 'Background', id: nextId++, kind: 'Pixel', visible: true, isBackground: true, bounds: [0, 0, w, h] }, null)] : [mkLayer({ name: 'Layer 1', id: nextId++, kind: 'Pixel', visible: true, bounds: [0, 0, w, h] }, null)]); } }),
    preferences: { rulerUnits: 'PX', typeUnits: 'PX', maximizeCompatibility: 'ASK' }, displayDialogs: 'ALL',
    fonts: { getByName: () => ({}) },
    open(f) {
      const rel = path.relative(bundle, f.fsName);
      if (!fs.existsSync(f.fsName)) throw new Error('mock: open: missing ' + f.fsName);
      if (/\.png$/i.test(rel)) return mkDoc(path.basename(rel), 256, 256, [mkLayer({ name: 'Background', id: nextId++, kind: 'Pixel', visible: true, isBackground: true, bounds: [0, 0, 256, 256] }, null)]);
      const id = byPsd[rel];
      const info = JSON.parse(fs.readFileSync(path.join(infoDir, id + '.json'), 'utf8'));
      return mkDoc(id, info.width, info.height, info.layers.map(j => mkLayer(j, null)));
    },
  },
};
ctx.global = ctx;
function app_prefs_check(c) { if (c.app.preferences.maximizeCompatibility !== 'ASK') { console.log('FAIL maximizeCompatibility not restored'); process.exitCode = 1; return false; } return true; }
const ret = vm.runInNewContext(src, ctx, { filename: jsxPath });
console.log('dry-run returned:', ret);
const res = JSON.parse(fs.readFileSync(path.join(outDir, 'results.json'), 'utf8'));
const bad = res.cases.filter(c => !c.ok);
for (const c of bad) console.log('FAIL', c.id, c.error);
const warn = res.cases.filter(c => c.warnings.length);
for (const c of warn) console.log('WARN', c.id, c.warnings.join('; '));
if (process.env.DUMP) fs.writeFileSync(process.env.DUMP, JSON.stringify(dumps, null, 1));
if (app_prefs_check(ctx)) console.log('preferences restored');
console.log(`${res.cases.length} cases, ${bad.length} failed, ${log.length} mock calls, open docs left: ${docs.length}`);
process.exit(bad.length || docs.length || process.exitCode ? 1 : 0);
