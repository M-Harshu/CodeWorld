// Loads the extension against a stub of the vscode API and exercises activation + the CodeLens provider.
const Module = require('module')
const calls = { commands: [], lens: null }
const stub = {
  workspace: { getConfiguration: () => ({ get: (k) => ({ serverUrl: 'http://127.0.0.1:1', apiToken: '', repoId: 'demo', codeLens: true }[k]), update: async () => {} }),
    asRelativePath: (u) => u.fsPath || String(u), workspaceFolders: [{ name: 'demo', uri: { fsPath: '/x' } }], onDidSaveTextDocument: () => ({ dispose() {} }) },
  window: { createOutputChannel: () => ({ clear() {}, appendLine() {}, show() {}, dispose() {} }), createStatusBarItem: () => ({ show() {}, dispose() {} }) },
  languages: { createDiagnosticCollection: () => ({ clear() {}, set() {}, dispose() {} }), registerCodeLensProvider: (_, p) => { calls.lens = p; return { dispose() {} } } },
  commands: { registerCommand: (id) => { calls.commands.push(id); return { dispose() {} } } },
  StatusBarAlignment: { Left: 1 }, EventEmitter: class { constructor() { this.event = () => {} } fire() {} },
  Range: class { constructor(...a) { this.a = a } }, CodeLens: class { constructor(r, c) { this.range = r; this.command = c } },
  Uri: { joinPath: () => ({}) }, DiagnosticSeverity: {}, ConfigurationTarget: {}, ProgressLocation: {}, ViewColumn: {},
}
const orig = Module._load
Module._load = function (req, ...r) { return req === 'vscode' ? stub : orig.call(this, req, ...r) }
global.fetch = async (url) => ({ ok: true, json: async () => (url.includes('/lens') ? [{ line: 3, callers: 2, tested: false, effects: ['db'], route: null, public_api: false }] : []) })
const ext = require('../extension.js')
const subs = []
ext.activate({ subscriptions: subs })
const need = ['selectRepo', 'ask', 'impactAtCursor', 'reviewChanges', 'proposePatch', 'reindex', 'openMap', 'openLocation'].map((c) => 'codeworld.' + c)
for (const c of need) if (!calls.commands.includes(c)) throw new Error('missing command ' + c)
calls.lens.provideCodeLenses({ uri: { scheme: 'file', fsPath: 'a.py' } }).then((l) => {
  if (!l.length || !/2 callers · untested ✗ · ⚠ db/.test(l[0].command.title)) throw new Error('bad lens: ' + JSON.stringify(l))
  console.log('extension smoke test OK:', l[0].command.title)
})
