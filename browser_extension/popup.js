// These two go first, before anything else — if some later line
// throws and halts the rest of the script (which is exactly what
// "stuck on Checking connection…" looks like), this is what makes
// that visible directly in the popup instead of failing completely
// silently with no way to tell what happened without DevTools open.
window.addEventListener("error", (e) => {
  const sub = document.getElementById("statusSub");
  if (sub) sub.textContent = "Script error: " + (e.message || "unknown");
});
window.addEventListener("unhandledrejection", (e) => {
  const sub = document.getElementById("statusSub");
  const reason = e.reason && e.reason.message ? e.reason.message : e.reason;
  if (sub) sub.textContent = "Promise error: " + reason;
});

const PORT = 47892;
const BASE = `http://127.0.0.1:${PORT}`;

async function ping() {
  try {
    const r = await fetch(`${BASE}/ping`, {signal: AbortSignal.timeout(3000)});
    const d = await r.json();
    return d.status === "ok" ? d.version : null;
  } catch { return null; }
}

async function init() {
  try {
    const version = await ping();
    const dot    = document.getElementById("dot");
    const text   = document.getElementById("statusText");
    const sub    = document.getElementById("statusSub");
    const btn    = document.getElementById("sendBtn");

    if (version) {
      dot.className  = "dot ok";
      text.textContent = "✅ Connected";
      sub.textContent  = `v${version} · Port ${PORT}`;
      btn.disabled = false;
    } else {
      dot.className  = "dot error";
      text.textContent = "Not Connected";
      sub.textContent  = "Open the VidDown desktop app first.";
    }
  } catch (err) {
    // Whatever this is, at least it's visible now instead of the
    // status just silently never updating.
    const sub = document.getElementById("statusSub");
    if (sub) sub.textContent = "init() error: " + (err && err.message ? err.message : err);
  }
}

try {
  document.getElementById("sendBtn").addEventListener("click", async () => {
    const feedback = document.getElementById("sendFeedback");
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab || !tab.url) {
      feedback.className = "cookie-feedback error";
      feedback.textContent = "Couldn't read this tab's URL — try reopening the popup on the video's page.";
      return;
    }
    // /add needs the per-run token that /ping hands out (see background.js)
    const pong = await (await fetch(`${BASE}/ping`, {signal: AbortSignal.timeout(3000)})).json();
    const ep = `${BASE}/add?url=${encodeURIComponent(tab.url)}&title=${encodeURIComponent(tab.title||"")}&t=${encodeURIComponent(pong.token||"")}`;
    try {
      await fetch(ep, {signal: AbortSignal.timeout(10000)});
    } catch {}
    window.close();
  });

  init();
  // The popup can stay open for a bit (person reads the tip, thinks
  // about it) — if VidDown gets opened/closed during that window, the
  // one-shot check above would otherwise leave a stale status on
  // screen until the popup is closed and reopened. Cheap local ping,
  // so a few seconds' cadence is fine.
  setInterval(init, 3000);
} catch (err) {
  // Setting up a listener itself failed (e.g. a button id mismatch) —
  // without this, that would silently stop init() from ever being
  // called at all, which looks exactly like "stuck on Checking
  // connection… forever, buttons do nothing."
  const sub = document.getElementById("statusSub");
  if (sub) sub.textContent = "Setup error: " + (err && err.message ? err.message : err);
}
  
