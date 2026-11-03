// Run: node tests/sniff_select.test.js   (loads background.js with a stubbed `chrome`)
const fs = require("fs"), vm = require("vm"), assert = require("assert"), path = require("path");
// A `chrome` stub where every property is both callable and further indexable.
const stub = () => new Proxy(function () {}, { get: () => stub(), apply: () => undefined });
const chrome = stub();
const ctx = { chrome, console, URL, setTimeout, clearTimeout, setInterval, Promise, Date, fetch: () => Promise.resolve(),
              AbortController, self: {}, globalThis: {} };
vm.createContext(ctx);
const src = fs.readFileSync(path.join(__dirname, "..", "browser_extension", "background.js"), "utf8");
vm.runInContext(src + "\n;this.__sel = selectSniffed;", ctx);
const sel = ctx.__sel;
const M = (url, ts) => ({ url, kind: "manifest", ts });
const F = (url, ts) => ({ url, kind: "file", ts });

// HLS: master + two variants in/under the master's directory -> offer the master
let r = sel([M("https://cdn.x/v/123/master.m3u8", 1), M("https://cdn.x/v/123/720p/index.m3u8", 2),
             M("https://cdn.x/v/123/audio/index.m3u8", 3)]);
assert.strictEqual(r.selected.length, 1);
assert.strictEqual(r.selected[0].url, "https://cdn.x/v/123/master.m3u8");

// order in storage must not matter (earliest wins)
r = sel([M("https://cdn.x/v/123/720p/index.m3u8", 2), M("https://cdn.x/v/123/master.m3u8", 1)]);
assert.strictEqual(r.selected[0].url, "https://cdn.x/v/123/master.m3u8");

// variants as siblings of the master in the same directory
r = sel([M("https://cdn.x/v/123/master.m3u8", 1), M("https://cdn.x/v/123/720.m3u8", 2)]);
assert.strictEqual(r.selected.length, 1);

// two different videos on one page (feed) -> ambiguous, offer nothing
r = sel([M("https://cdn.x/v/111/master.m3u8", 1), M("https://cdn.x/v/222/master.m3u8", 2)]);
assert.strictEqual(r.selected.length, 0); assert.strictEqual(r.ambiguous, 2);

// ad stream from another origin -> ambiguous (conservative, as before)
r = sel([M("https://ads.y/p.m3u8", 1), M("https://cdn.x/v/1/master.m3u8", 2)]);
assert.strictEqual(r.selected.length, 0);

// playlists in the SAME directory (even the site root) are one stream...
r = sel([M("https://cdn.x/index.m3u8", 1), M("https://cdn.x/index_720.m3u8", 2)]);
assert.strictEqual(r.selected.length, 1);
// ...but a root playlist says nothing about a deeper directory: ambiguous
r = sel([M("https://cdn.x/a.m3u8", 1), M("https://cdn.x/v/9/b.m3u8", 2)]);
assert.strictEqual(r.selected.length, 0);
r = sel([M("https://cdn.x/a.m3u8", 1)]); assert.strictEqual(r.selected.length, 1);

// files: exactly one is offered, two are ambiguous, a manifest wins over files
assert.strictEqual(sel([F("https://c/v.mp4", 1)]).selected.length, 1);
assert.strictEqual(sel([F("https://c/v.mp4", 1), F("https://c/w.mp4", 2)]).selected.length, 0);
r = sel([F("https://c/v.mp4", 1), M("https://c/m/master.m3u8", 2)]);
assert.strictEqual(r.selected[0].kind, "manifest");
assert.strictEqual(sel([]).selected.length, 0);
console.log("sniff selection: all assertions passed");
