// background.js — VidDown Companion (MV3 service worker)

const VIDDOWN_PORT = 47892;
const BASE_URL     = `http://127.0.0.1:${VIDDOWN_PORT}`;
const REQUEST_TIMEOUT_MS = 55_000;

// ── Prefetch cache ──────────────────────────────────────────────────────────
// url → { status: "loading"|"done"|"error", result: null|{ok,title,qualities} }
const _prefetchCache = new Map();
const CACHE_TTL_MS   = 5 * 60 * 1000;  // 5 min

function _cacheSet(url, status, result = null) {
  _prefetchCache.set(url, { status, result, ts: Date.now() });
}

function _cacheGet(url) {
  const entry = _prefetchCache.get(url);
  if (!entry) return null;
  if (Date.now() - entry.ts > CACHE_TTL_MS) {
    _prefetchCache.delete(url);
    return null;
  }
  return entry;
}

async function _waitForPrefetch(url, maxMs = 55_000, onHeartbeat = null) {
  const step = 200;
  let sinceBeat = 0;
  for (let t = 0; t < maxMs; t += step) {
    await new Promise(r => setTimeout(r, step));
    const e = _cacheGet(url);
    if (e && e.status !== "loading") return e.result;
    // A bare wait sends nothing across the port, so the service worker could
    // be recycled mid-wait and the panel would hang until its own timeout.
    sinceBeat += step;
    if (onHeartbeat && sinceBeat >= 10_000) { sinceBeat = 0; try { onHeartbeat(); } catch {} }
  }
  return { ok: false, error: "Prefetch timed out" };
}

// ── Core fetch helpers ──────────────────────────────────────────────────────
function fetchWithTimeout(url, opts = {}, ms = REQUEST_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const id   = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { ...opts, signal: ctrl.signal }).finally(() => clearTimeout(id));
}

// ── Bridge token ────────────────────────────────────────────────────────────
// VidDown only answers real requests that carry a per-run token, which /ping
// hands out. Web pages cannot read that response, so only this extension can
// obtain it. The token changes whenever VidDown restarts, so a 403 triggers one
// re-fetch and retry.
let _bridgeToken = "";

async function _getBridgeToken(force = false) {
  if (_bridgeToken && !force) return _bridgeToken;
  const r = await fetchWithTimeout(`${BASE_URL}/ping`, {}, 4_000);
  const d = await r.json().catch(() => ({}));
  _bridgeToken = d.token || "";
  return _bridgeToken;
}

async function bridgeFetch(pathWithQuery, ms) {
  let r;
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await _getBridgeToken(attempt > 0);
    const sep = pathWithQuery.includes("?") ? "&" : "?";
    r = await fetchWithTimeout(`${BASE_URL}${pathWithQuery}${sep}t=${encodeURIComponent(token)}`, {}, ms);
    if (r.status !== 403) break;
  }
  return r;
}

async function probeVidDown(url, title, onHeartbeat, referer = "") {
  const ep = `/probe?url=${encodeURIComponent(url)}&title=${encodeURIComponent(title)}` +
             (referer ? `&referer=${encodeURIComponent(referer)}` : "");
  // A slow site (lots of formats, a slow connection, etc.) can leave this
  // fetch() pending well past 30s. Chrome's MV3 service worker can be
  // killed mid-request once a fetch() has been outstanding that long,
  // even with a port open — as of Chrome 114, merely HAVING a port open
  // no longer resets the worker's idle timer; only actually sending a
  // message across it does. So this drives a small heartbeat message out
  // to the caller every few seconds for as long as the fetch is still
  // running, purely to keep the service worker alive until the real
  // result is ready — this is what was causing the extension to silently
  // "lose connection" partway through a probe instead of staying
  // connected for the whole request.
  const heartbeat = onHeartbeat
    ? setInterval(() => { try { onHeartbeat(); } catch {} }, 15000)
    : null;
  try {
    const r = await bridgeFetch(ep, REQUEST_TIMEOUT_MS);
    // bridge_server.py's error responses are still valid JSON with a
    // descriptive "error" field (whatever yt-dlp actually failed with) —
    // parse the body even on a non-2xx status instead of discarding that
    // detail and reporting just the bare status code.
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) return { ok: false, error: d.error || `HTTP ${r.status}` };
    return d;
  } catch (e) {
    return { ok: false, error: e.message || "Connection failed" };
  } finally {
    if (heartbeat) clearInterval(heartbeat);
  }
}

async function downloadVidDown(url, qualityIndex, title, referer = "") {
  const ep = `/download?url=${encodeURIComponent(url)}&quality_index=${qualityIndex}&title=${encodeURIComponent(title)}` +
             (referer ? `&referer=${encodeURIComponent(referer)}` : "");
  try {
    const r = await bridgeFetch(ep, 15_000);
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) return { ok: false, error: d.error || `HTTP ${r.status}` };
    // the cached quality list carries "already downloaded" flags that are
    // now out of date — drop it so the next open re-asks VidDown
    _prefetchCache.delete(url);
    return { ok: true, job_id: d.job_id };
  } catch (e) {
    return { ok: false, error: e.message || "Connection failed" };
  }
}

async function fetchJobStatus(jobId) {
  const ep = `/job_status?job_id=${encodeURIComponent(jobId)}`;
  try {
    const r = await bridgeFetch(ep, 8_000);
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.ok) return { ok: false, error: d.error || `HTTP ${r.status}` };
    return d;
  } catch (e) {
    return { ok: false, error: e.message || "Connection failed" };
  }
}

// Hovering across a grid of video cards fires a prefetch per card, and each one
// is a full yt-dlp extraction (using the browser cookies). Cap how many run at
// once and simply drop the rest — clicking a card still fetches on demand.
let _prefetchInFlight = 0;
const _PREFETCH_MAX_PARALLEL = 2;

async function prefetchIfNeeded(url, title) {
  if (_cacheGet(url)) return;               // already cached or loading
  if (_prefetchInFlight >= _PREFETCH_MAX_PARALLEL) return;
  _prefetchInFlight++;
  try {
    _cacheSet(url, "loading");
    const result = await probeVidDown(url, title);
    _cacheSet(url, result.ok ? "done" : "error", result);
  } finally {
    _prefetchInFlight--;
  }
}

// ── Port — used by content.js only for probe_video, the one call that
// can genuinely run long enough (up to 55s on a slow site) to need a
// long-lived connection's keepalive protection. download_video and
// poll_job_status use the plain sendMessage handler above instead. ────────
chrome.runtime.onConnect.addListener((port) => {
  port.onMessage.addListener(async (msg) => {

    if (msg.type === "probe_video") {
      const url     = msg.url;
      const title   = msg.title || "";
      const referer = msg.referer || "";
      let result;

      const sendHeartbeat = () => {
        try { port.postMessage({ type: "probe_heartbeat" }); } catch {}
      };

      const cached = _cacheGet(url);
      if (cached && cached.status === "done") {
        // ✅ instant cache hit
        result = cached.result;
      } else if (cached && cached.status === "loading") {
        // ⏳ prefetch running — wait for it
        result = await _waitForPrefetch(url, 55_000, sendHeartbeat);
      } else {
        // no prefetch — fetch now (also stores in cache)
        _cacheSet(url, "loading");
        result = await probeVidDown(url, title, sendHeartbeat, referer);
        _cacheSet(url, result.ok ? "done" : "error", result);
      }

      try { port.postMessage({ type: "probe_result", ...result }); }
      catch {}   // port may have been closed
    }
  });
});

// ── sendMessage — used by content.js for prefetch/status checks, and for
// download_video + poll_job_status specifically because those are both
// short, bounded calls (15s / 8s internal timeouts) that don't need a
// long-lived port's keepalive protection — unlike probe_video (still on
// the port below), which can run up to 55s on a slow site. Polling job
// status every 500ms-1s for a download's entire runtime through a port
// would mean opening and tearing one down that often for the whole
// download; a plain request/response message is both simpler and cheaper
// for calls this quick. ──────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {

  if (msg.type === "prefetch_video") {
    prefetchIfNeeded(msg.url, msg.title || "");
    sendResponse({ ok: true });
    return false;
  }

  if (msg.type === "get_prefetch_status") {
    const e = _cacheGet(msg.url);
    sendResponse(e ? { status: e.status } : { status: "none" });
    return false;
  }

  if (msg.type === "get_connection_status") {
    // Always a fresh live check, not the possibly-stale _appConnected —
    // this only runs once per content-script injection (page load), so
    // the extra round-trip is cheap and worth it for a correct first
    // answer. Piggybacks the sender's own tab badge onto the same ping
    // rather than firing a second one for it.
    const tabId = _sender && _sender.tab ? _sender.tab.id : null;
    checkConnectionAndBadge(tabId).then((connected) => sendResponse({ connected }));
    return true;  // keep the message channel open for the async response
  }

  if (msg.type === "download_video") {
    downloadVidDown(msg.url, msg.index, msg.title || "", msg.referer || "").then(sendResponse);
    return true;
  }

  if (msg.type === "get_sniffed") {
    // Media links seen in this tab's network traffic (all frames) — see the
    // sniffer section at the bottom of this file.
    const tabId = _sender && _sender.tab ? _sender.tab.id : null;
    getSniffed(tabId, msg.pageUrl || "").then((r) => sendResponse({ ok: true, items: r.items, rejected: r.rejected, derived: r.derived }));
    return true;
  }

  if (msg.type === "poll_job_status") {
    fetchJobStatus(msg.jobId).then(sendResponse);
    return true;
  }
});

// ── Connection check + badge — one ping updates both. Also gates whether
// content.js shows its hover button at all (see _appConnected below). A
// service worker restart wipes any in-memory state back to defaults (MV3
// unloads the whole script after ~30s idle and re-runs it from scratch on
// the next event), which is fine here since every wake that matters (the
// alarm below, a tab activating/finishing load, or a content script's own
// initial query) re-checks live rather than trusting a stale leftover value.
let _appConnected = false;
let _connectionCheckInFlight = null;   // coalesces simultaneous callers into one /ping

async function checkConnectionAndBadge(tabId) {
  // A page can inject content.js into many frames at once (all_frames is
  // true — see manifest.json), and every one of them asks for the
  // connection status on load. Without this, a page with a dozen ad/embed
  // iframes would fire a dozen simultaneous /ping calls and a dozen
  // broadcasts-to-every-tab for the exact same answer. Coalescing means
  // only the first caller in a given window actually pings; everyone else
  // just awaits that same in-flight result.
  if (!_connectionCheckInFlight) {
    _connectionCheckInFlight = (async () => {
      let connected = false;
      try {
        const r = await fetchWithTimeout(`${BASE_URL}/ping`, {}, 3_000);
        const d = await r.json();
        connected = d.status === "ok";
      } catch {}
      _appConnected = connected;
      // Push to every open tab, not just the active one — a background
      // tab the person switches back to should already have the right
      // answer instead of showing a stale button for a moment. Tabs with
      // no content script (chrome:// pages, the Web Store, etc.) just
      // reject the message — harmless, so it's swallowed.
      chrome.tabs.query({}, (tabs) => {
        for (const tab of tabs) {
          if (tab.id == null) continue;
          chrome.tabs.sendMessage(tab.id, { type: "viddown_connection", connected })
            .catch(() => {});
        }
      });
      return connected;
    })();
    _connectionCheckInFlight.finally(() => { _connectionCheckInFlight = null; });
  }

  const connected = await _connectionCheckInFlight;
  if (tabId != null) {
    chrome.action.setBadgeText({ text: connected ? "✓" : "✕", tabId });
    chrome.action.setBadgeBackgroundColor({ color: connected ? "#22c55e" : "#ef4444", tabId });
  }
  return connected;
}

chrome.tabs.onActivated.addListener((info) => checkConnectionAndBadge(info.tabId));
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === "complete") checkConnectionAndBadge(tabId);
});

// Poll every 30 s to react when the app starts/stops
// Create only if missing: re-creating on every service-worker wake-up would
// restart the 30 s countdown each time, so it could never actually fire.
chrome.alarms.get("badge_poll", (existing) => {
  if (!existing) chrome.alarms.create("badge_poll", { periodInMinutes: 0.5 });
});
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name !== "badge_poll") return;
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    checkConnectionAndBadge(tabs[0] ? tabs[0].id : null);
  });
});

// ── Right-click context menu — "Download with VidDown" ─────────────────────
// removeAll() first: onInstalled can fire again on an extension reload
// during development, and create() throws on a duplicate id otherwise.
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "viddown-download",
      title: "⬇ Download with VidDown",
      contexts: ["link", "video", "page"],
    });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== "viddown-download" || !tab || !tab.id) return;

  // Right-clicking a link targets that exact URL (could be a video page,
  // or a direct video file link). Right-clicking a <video> element also
  // gives info.srcUrl — the same thing a browser's own "Copy video URL"
  // uses, sent along as a second candidate; content.js tries it only if
  // the page URL / link URL doesn't work out, since srcUrl is often a
  // blob: URL on sites using MSE for adaptive streaming (most big
  // platforms), which isn't usable outside the page at all.
  const targetUrl = info.linkUrl || tab.url;
  if (!targetUrl) return;

  chrome.tabs.sendMessage(
    tab.id,
    { type: "context_menu_download", url: targetUrl, srcUrl: info.srcUrl || null, title: tab.title || "" },
    { frameId: 0 }  // main frame only — opening the panel inside an iframe would be wrong
  );
});


// ── Network sniffer ─────────────────────────────────────────────────────────
// Watches the tab's own network traffic for media the page loads (HLS/DASH
// manifests and plain video files) and remembers them per tab, so content.js
// can offer them as an extra "way" when yt-dlp can't work out the page URL
// itself — the same idea IDM uses to catch streams on unsupported sites.
//
// Kept in chrome.storage.session (not a plain variable): the MV3 service
// worker is unloaded after ~30s idle and would otherwise forget everything.
// Observation only — nothing is blocked or modified.
const SNIFF_MAX_PER_TAB = 24;
// A player fetches its manifest/file once, when the video loads - the person
// may hover or click minutes later. 2 minutes dropped it in that case, so it
// is kept for 10. Stale feed candidates are still kept out by the same-page
// filter and the "one stream only" rule in selectSniffed().
const SNIFF_TTL_MS      = 10 * 60 * 1000;
const SNIFF_MIN_BYTES   = 300 * 1024;   // smaller "videos" are previews/ads/thumbnails
const SNIFF_RETURN_MAX  = 4;

const _SNIFF_MANIFEST_EXT = /\.(m3u8|mpd|f4m)(?:[?#]|$)/i;
const _SNIFF_FILE_EXT     = /\.(mp4|m4v|webm|mkv|mov|flv)(?:[?#]|$)/i;
const _SNIFF_MANIFEST_CT  = /(mpegurl|dash\+xml|f4m)/i;
// YouTube's raw googlevideo chunks and DASH byte-range pieces are useless on
// their own (yt-dlp already handles those sites from the page URL).
const _SNIFF_SKIP_HOST    = /(^|\.)googlevideo\.com$/i;
const _SNIFF_SKIP_QUERY   = /[?&](bytestart|byteend|range|rbuf)=|\/videoplayback/i;

function _sniffKey(tabId) { return `sniff:${tabId}`; }

// One writer at a time — read-modify-write on storage.session would otherwise
// lose entries when several media requests land at once.
let _sniffChain = Promise.resolve();
function _sniffLocked(fn) {
  _sniffChain = _sniffChain.then(fn, fn).catch(() => {});
  return _sniffChain;
}

function _classifyMedia(details, headers) {
  let u;
  try { u = new URL(details.url); } catch { return null; }
  if (!/^https?:$/.test(u.protocol)) return null;
  if (u.hostname === "127.0.0.1" || u.hostname === "localhost") return null;
  if (details.statusCode && details.statusCode >= 400) return null;

  const h = {};
  for (const x of headers || []) h[x.name.toLowerCase()] = x.value || "";
  const ct = (h["content-type"] || "").toLowerCase();

  const isManifest  = _SNIFF_MANIFEST_EXT.test(u.pathname) || _SNIFF_MANIFEST_CT.test(ct);
  const isSegment   = /\.(ts|m4s)$/i.test(u.pathname) || ct === "video/mp2t" || ct.includes("iso.segment");
  const isVideoCt   = ct.startsWith("video/");
  const hasVideoExt = _SNIFF_FILE_EXT.test(u.pathname);
  if (!isManifest && !isSegment && !isVideoCt && !hasVideoExt) return null;   // not media at all

  // From here on it LOOKS like media — a rejection is reported (as a count,
  // see _noteRejected) so the panel can say why nothing was captured.
  if (isSegment)                                  return null;   // hundreds per video: not worth even counting
  if (_SNIFF_SKIP_HOST.test(u.hostname))          return { reject: "googlevideo (YouTube)" };
  if (_SNIFF_SKIP_QUERY.test(details.url))        return { reject: "known byte-range/chunk URL" };
  if (isManifest)                                 return { kind: "manifest", size: 0 };

  // A 206 is NOT rejected: browsers request <video> files with Range headers,
  // so most progressive mp4s answer 206. The URL itself still names the whole
  // file (VidDown downloads it from the start), and the real size is taken
  // from the Content-Range total below. Chunk-style URLs that really do
  // address a slice are already caught by _SNIFF_SKIP_QUERY above.

  // Real size: Content-Range total for a 206, else Content-Length.
  let size = 0;
  const crMatch = (h["content-range"] || "").match(/\/(\d+)$/);
  if (crMatch) size = parseInt(crMatch[1], 10);
  else if (h["content-length"]) size = parseInt(h["content-length"], 10) || 0;
  if (size && size < SNIFF_MIN_BYTES)             return { reject: "too small (<300KB)" };
  if (!size && !hasVideoExt)                      return { reject: "unknown size" };
  return { kind: "file", size };
}

async function _refererFor(details) {
  // The page that actually issued the request (frame-aware); CDNs usually
  // check Referer/Origin against it.
  if (details.documentUrl && /^https?:/i.test(details.documentUrl)) return details.documentUrl;
  try {
    const tab = await chrome.tabs.get(details.tabId);
    if (tab && tab.url && /^https?:/i.test(tab.url)) return tab.url;
  } catch {}
  return details.initiator && /^https?:/i.test(details.initiator) ? details.initiator + "/" : "";
}

// Do not reconstruct Facebook watch URLs from CDN chunk parameters here.
// Those IDs are not reliably tied to the currently hovered video; a guessed
// URL can silently select a different post. Platform adapters should provide
// a verified permalink instead.

// Counts of media-looking requests that were deliberately ignored, per tab
// (diagnostics only — shown in the panel when nothing could be captured).
// Kept in memory and flushed to storage at most every ~1.5s: a byte-range
// stream can fire dozens of ignored requests per second.
const _rejPending = new Map();   // tabId -> { reason: count }
let _rejFlushTimer = null;

function _noteRejected(tabId, reason) {
  const m = _rejPending.get(tabId) || {};
  m[reason] = (m[reason] || 0) + 1;
  _rejPending.set(tabId, m);
  if (_rejFlushTimer) return;
  _rejFlushTimer = setTimeout(() => {
    _rejFlushTimer = null;
    const batch = new Map(_rejPending);
    _rejPending.clear();
    _sniffLocked(async () => {
      for (const [tid, counts] of batch) {
        const key = `sniffrej:${tid}`;
        const got = await chrome.storage.session.get(key);
        const cur = got[key] || {};
        for (const [r, n] of Object.entries(counts)) cur[r] = (cur[r] || 0) + n;
        await chrome.storage.session.set({ [key]: cur });
      }
    });
  }, 1500);
}

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId == null || details.tabId < 0) return;   // not from a tab
    const info = _classifyMedia(details, details.responseHeaders);
    if (!info) return;
    if (info.reject) { _noteRejected(details.tabId, info.reject); return; }
    _sniffLocked(async () => {
      const referer = await _refererFor(details);
      const key = _sniffKey(details.tabId);
      const got = await chrome.storage.session.get(key);
      const now = Date.now();
      let list = (got[key] || []).filter((x) => now - x.ts < SNIFF_TTL_MS && x.url !== details.url);
      list.push({ url: details.url, kind: info.kind, size: info.size, referer, ts: now });
      if (list.length > SNIFF_MAX_PER_TAB) list = list.slice(-SNIFF_MAX_PER_TAB);
      await chrome.storage.session.set({ [key]: list });
    });
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] },
  ["responseHeaders"]
);

// The directory part of a URL ("https://cdn.x/v/123/master.m3u8" ->
// "https://cdn.x/v/123/"), used to tell whether two playlists belong together.
function _urlDir(u) {
  try {
    const x = new URL(u);
    return x.origin + x.pathname.slice(0, x.pathname.lastIndexOf("/") + 1);
  } catch { return String(u); }
}

// True when two manifest URLs look like parts of ONE stream: an HLS player
// fetches the master playlist and then one or more variant/audio playlists,
// which live in (or below) the master's directory.
function _sameStream(a, b) {
  const da = _urlDir(a), db = _urlDir(b);
  if (da === db) return true;
  const rootOf = (d) => { try { return new URL(d).origin + "/"; } catch { return d; } };
  // a playlist sitting in the site root gives no information about a family
  if (da === rootOf(da) || db === rootOf(db)) return false;
  return da.startsWith(db) || db.startsWith(da);
}

// Decide which sniffed network media (already limited to the current page)
// to offer. Returns { selected, ambiguous }.
//  - manifests: they all belong to one stream (master + its variants) ->
//    offer the EARLIEST one, the master, which yt-dlp expands into every
//    quality. Requiring "exactly one manifest" made HLS fail on nearly every
//    site, because players always request a master plus variant playlists.
//    Playlists of several different streams are ambiguous -> offer nothing.
//  - no manifest: exactly one direct file, as before.
function selectSniffed(list) {
  const manifests = list.filter((x) => x.kind === "manifest").sort((a, b) => a.ts - b.ts);
  if (manifests.length) {
    const head = manifests[0];
    if (manifests.every((m) => _sameStream(m.url, head.url))) return { selected: [head], ambiguous: 0 };
    return { selected: [], ambiguous: manifests.length };
  }
  const files = list.filter((x) => x.kind === "file");
  if (files.length === 1) return { selected: files, ambiguous: 0 };
  return { selected: [], ambiguous: files.length > 1 ? files.length : 0 };
}

async function getSniffed(tabId, pageUrl = "") {
  if (tabId == null) return { items: [], rejected: {}, derived: [] };
  const key = _sniffKey(tabId);
  let list = [], rejected = {};
  try {
    const got = await chrome.storage.session.get([key, `sniffrej:${tabId}`]);
    list = got[key] || [];
    rejected = { ...(got[`sniffrej:${tabId}`] || {}) };
  } catch { return { items: [], rejected: {}, derived: [] }; }
  for (const [r, n] of Object.entries(_rejPending.get(tabId) || {})) rejected[r] = (rejected[r] || 0) + n;

  const now = Date.now();
  list = list.filter((x) => now - x.ts < SNIFF_TTL_MS);

  // Network media is only a fallback. Restrict it to the current page context;
  // never blindly return the last four media URLs from a busy feed. A URL may
  // be shared by multiple videos, so return sniffed candidates only when one
  // unambiguous manifest (preferred) or one unambiguous direct file remains.
  let page = null;
  try { page = new URL(pageUrl); } catch {}
  if (page) {
    list = list.filter((x) => {
      try {
        const ref = new URL(x.referer || "");
        return ref.origin === page.origin && ref.pathname === page.pathname;
      } catch { return false; }
    });
  } else {
    list = [];
  }

  const picked = selectSniffed(list);
  const selected = picked.selected;
  if (picked.ambiguous) {
    rejected["several different streams on this page"] =
      (rejected["several different streams on this page"] || 0) + picked.ambiguous;
  }
  // Ambiguous feed traffic is intentionally not guessed. The user can open
  // the individual video page or use a platform-specific supported extractor.
  const items = selected.map(({ url, kind, size, referer }) => ({ url, kind, size, referer }));

  // Facebook's efg IDs are observed on CDN chunk requests, but cannot reliably
  // be associated with the currently hovered DOM video. Do not manufacture a
  // watch URL from a tab-global "last ID"; that was a source of wrong videos.
  return { items, rejected, derived: [] };
}

// A real page load in the tab means the old page's streams no longer apply.
// Deliberately main_frame requests only — NOT tabs.onUpdated's url change,
// which also fires for in-page (pushState) navigation on SPA sites like
// Facebook/Instagram and was wiping the list while the video was playing.
function _clearSniffed(tabId) {
  _rejPending.delete(tabId);
  return _sniffLocked(() => chrome.storage.session.remove([_sniffKey(tabId), `sniffrej:${tabId}`, `sniffvid:${tabId}`]));
}
chrome.webRequest.onBeforeRequest.addListener(
  (details) => { if (details.tabId >= 0) _clearSniffed(details.tabId); },
  { urls: ["<all_urls>"], types: ["main_frame"] }
);
chrome.tabs.onRemoved.addListener((tabId) => { _clearSniffed(tabId); });
