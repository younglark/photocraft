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
  const l = { name: j.name, id: j.id, typename: isSet ? 'LayerSet' : 'ArtLayer', visible: j.visible, parent,
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
  d.trim = () => {};
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
class AD { constructor() { this.v = {}; } putReference(k, r) { this.v[k] = r; } putString(k, s) { this.v[k] = s; } putInteger(k, i) { this.v[k] = i; }
  putBoolean(k, b) { this.v[k] = b; } putEnumerated(k, t, e) { this.v[k] = e; } }
class AR { putIdentifier(c, id) { this.id = id; } putClass(c) { this.cls = c; } putEnumerated() { this.target = true; } }
const ctx = {
  arguments: [bundle, outDir, process.env.FILTER || ''],
  File, Folder, ActionDescriptor: AD, ActionReference: AR,
  charIDToTypeID: s => 'c:' + s, stringIDToTypeID: s => 's:' + s,
  DialogModes: { NO: 'NO', ALL: 'ALL' }, Units: { PIXELS: 'PX' }, TypeUnits: { PIXELS: 'PX' },
  ElementPlacement: { PLACEBEFORE: 'PLACEBEFORE', PLACEAFTER: 'PLACEAFTER', PLACEATBEGINNING: 'PLACEATBEGINNING', INSIDE: 'INSIDE' },
  RasterizeType: { TEXTCONTENTS: 'TEXTCONTENTS', SHAPE: 'SHAPE', ENTIRELAYER: 'ENTIRELAYER', FILLCONTENT: 'FILLCONTENT' },
  SaveOptions: { DONOTSAVECHANGES: 'DNS' }, Extension: { LOWERCASE: 'lc' }, TrimType: { TRANSPARENT: 'T' },
  BitsPerChannelType: { EIGHT: 'EIGHT' }, LayerKind: { TEXT: 'TEXT' }, SaveDocumentType: { PNG: 'PNG' }, ExportType: { SAVEFORWEB: 'SFW' },
  PNGSaveOptions: function () {}, ExportOptionsSaveForWeb: function () {},
  $: { os: 'mock', fileName: jsxPath },
  decodeURI,
  executeAction(ev, d) {
    const doc = active;
    if (ev === 's:rasterizeLayer') { const l = findId(doc.layers, d.v['c:null'].id); if (!l) throw new Error('mock: no layer ' + d.v['c:null'].id); log.push('am rasterize ' + l.name + ' ' + d.v['s:what']); return; }
    if (ev === 'c:Mk  ') { const r = d.v['c:Usng']; const l = findId(doc.layers, r.id); if (!l) throw new Error('mock: no layer'); mkDoc('pso-layer', doc.width.as(), doc.height.as(), [mkLayer({ name: l.name, id: nextId++, kind: 'Pixel', visible: l.visible }, null)]); return; }
    if (ev === 'c:slct') return;
    throw new Error('mock: unexpected event ' + ev);
  },
  app: {
    version: 'mock', get activeDocument() { return active; }, set activeDocument(d) { active = d; },
    documents: docs, preferences: { rulerUnits: 'PX', typeUnits: 'PX' }, displayDialogs: 'ALL',
    fonts: { getByName: () => ({}) },
    open(f) {
      const rel = path.relative(bundle, f.fsName);
      const id = byPsd[rel];
      const info = JSON.parse(fs.readFileSync(path.join(infoDir, id + '.json'), 'utf8'));
      return mkDoc(id, info.width, info.height, info.layers.map(j => mkLayer(j, null)));
    },
  },
};
ctx.global = ctx;
const ret = vm.runInNewContext(src, ctx, { filename: jsxPath });
console.log('dry-run returned:', ret);
const res = JSON.parse(fs.readFileSync(path.join(outDir, 'results.json'), 'utf8'));
const bad = res.cases.filter(c => !c.ok);
for (const c of bad) console.log('FAIL', c.id, c.error);
const warn = res.cases.filter(c => c.warnings.length);
for (const c of warn) console.log('WARN', c.id, c.warnings.join('; '));
console.log(`${res.cases.length} cases, ${bad.length} failed, ${log.length} mock calls, open docs left: ${docs.length}`);
process.exit(bad.length || docs.length ? 1 : 0);
