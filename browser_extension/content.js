// content.js
// Runs on every page. Watches for <video> elements and shows a small
// floating "⬇ VidDown" button when hovering over them — but only while
// the VidDown desktop app is actually reachable (see appConnected below);
// otherwise hovering does nothing, since a button that can only ever
// fail on click is worse than no button at all. Clicking it opens
// a quality-picker panel right there on the page (same idea as the
// "Select Quality" popup panel in the VidDown app) — picking a quality
// downloads it directly, no need to switch to the desktop app at all.
//
// Built to work generically across every site yt-dlp supports, not just
// one or two — see the comments in the video-detection and messaging
// sections below for the specific cross-site problems each part solves.

(function () {
  const MIN_SIZE = 100;          // ignore tiny preview/ad videos smaller than this (px)
  const HIDE_DELAY = 350;        // ms grace period before hiding the button on mouseleave
  const PROBE_TIMEOUT_MS = 60000; // some sites (lots of formats) take a while in yt-dlp
  // When a page has several sizeable videos and the hovered one is clearly NOT
  // the page's main video, the page-wide fallbacks (page URL, tab-level sniffed
  // streams) describe the MAIN video, not the hovered one — using them is what
  // made a different video's quality list show up. Set to false to restore the
  // old behaviour of always offering those fallbacks.
  const MULTI_VIDEO_GATE = true;

  let hideTimer = null;
  let activeBtn = null;
  let panelEl = null;
  let panelOpenForVideo = null;  // the <video> element the open panel belongs to
  let _lastPrefetchUrl  = null;  // URL we last triggered prefetch for
  let currentRequestId = 0;      // guards against a stale/late response overwriting a newer one
  let loadingTimerId = null;     // interval driving the "Loading... (Ns)" text

  // Whether the VidDown app is actually reachable right now — the hover
  // button has no reason to exist while it isn't, since clicking it could
  // only ever fail. Starts false (safest default) until background.js's
  // initial check below confirms otherwise, and is kept in sync from then
  // on by background.js pushing "viddown_connection" on its own ~30s poll
  // and whenever this tab activates/finishes loading.
  let appConnected = false;
  chrome.runtime.sendMessage({ type: "get_connection_status" }, (resp) => {
    if (chrome.runtime.lastError) return;  // extension context gone — ignore
    appConnected = !!(resp && resp.connected);
  });

  // ------------------------------------------------------------- hover button
  //
  // Draggable, like IDM's floating download icon — mousedown-and-drag
  // moves it, and where you drop it is remembered (as an offset from the
  // hovered video's own top-left corner) so every video's button after
  // that starts at your preferred spot too, instead of resetting to the
  // same corner every time. Resets on page reload/navigation — this is
  // in-memory only, not persisted to storage.
  let customOffset = null;   // {dx, dy} relative to the hovered video's top-left, or null (default position)
  let dragState = null;      // {startX, startY, moved} while a drag is in progress
  let suppressNextClick = false;

  function makeButton() {
    const btn = document.createElement("div");
    btn.className = "viddown-hover-btn";
    btn.innerHTML = '<span class="viddown-btn-dot"></span><span>⬇ VidDown</span>';
    btn.addEventListener("click", onButtonClick);
    btn.addEventListener("mousedown", onButtonMouseDown);
    return btn;   // caller appends it once
  }

  function onButtonMouseDown(e) {
    if (e.button !== 0) return;  // left button only
    dragState = { startX: e.clientX, startY: e.clientY, moved: false };
    cancelHide();  // don't let the hover-hide timer fire mid-drag
    e.preventDefault();
    document.addEventListener("mousemove", onButtonDragMove, true);
    document.addEventListener("mouseup", onButtonDragEnd, true);
  }

  function onButtonDragMove(e) {
    if (!dragState || !activeBtn) return;
    const dx = e.clientX - dragState.startX;
    const dy = e.clientY - dragState.startY;
    // small threshold before it counts as an actual drag rather than a
    // click with a slightly shaky hand
    if (!dragState.moved && Math.hypot(dx, dy) < 4) return;
    dragState.moved = true;

    const rect = activeBtn.getBoundingClientRect();
    const newLeft = rect.left + dx;
    const newTop  = rect.top + dy;
    activeBtn.style.left = `${Math.max(newLeft, 0)}px`;
    activeBtn.style.top  = `${Math.max(newTop,  0)}px`;
    dragState.startX = e.clientX;
    dragState.startY = e.clientY;

    if (activeBtn._viddownVideo) {
      const videoRect = activeBtn._viddownVideo.getBoundingClientRect();
      customOffset = { dx: newLeft - videoRect.left, dy: newTop - videoRect.top };
    }
  }

  function onButtonDragEnd() {
    document.removeEventListener("mousemove", onButtonDragMove, true);
    document.removeEventListener("mouseup", onButtonDragEnd, true);
    if (dragState && dragState.moved) {
      // the browser fires a click right after mouseup on the same
      // element — swallow just that one so dropping the button doesn't
      // also open the quality panel
      suppressNextClick = true;
      // If mouseup landed OFF the button no click ever reaches it, and the
      // flag would silently swallow the NEXT real click instead — so it
      // only lives for the instant the browser needs to deliver that click.
      setTimeout(() => { suppressNextClick = false; }, 60);
    }
    dragState = null;
  }

  function isPanelOpen() {
    return !!panelEl && panelEl.style.display !== "none";
  }

  function cancelHide() {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function scheduleHide() {
    if (dragState) return;  // never hide while actively being dragged
    cancelHide();
    hideTimer = setTimeout(() => {
      // don't hide the button while its panel is open — closing the
      // panel already takes care of restoring/hiding things
      if (activeBtn && !isPanelOpen()) activeBtn.style.display = "none";
    }, HIDE_DELAY);
  }

  // offset: optional {dx, dy} relative to target's top-left corner,
  // overriding the default margin-based placement below it — only the
  // draggable hover button ever passes one; the quality panel always
  // uses the default position.
  function positionNear(el, target, offset) {
    if (!target) return;
    // target is normally an element; a plain {left, top, ...} rect is also
    // accepted (used when the element is gone and only a snapshot is left)
    const rect = typeof target.getBoundingClientRect === "function"
      ? target.getBoundingClientRect() : target;
    el.style.display = "flex";  // force layout so reqWidth is known
    const panelW = el.offsetWidth || 300;
    const panelH = el.offsetHeight || 200;
    const margin = 8;
    let top, left;
    if (offset) {
      left = rect.left + offset.dx;
      top  = rect.top  + offset.dy;
    } else {
      top  = rect.top  + margin;
      left = rect.left + margin;
    }
    // keep inside viewport
    if (left + panelW > window.innerWidth  - margin) left = window.innerWidth  - panelW - margin;
    if (top  + panelH > window.innerHeight - margin) top  = window.innerHeight - panelH - margin;
    el.style.top  = `${Math.max(top,  0)}px`;
    el.style.left = `${Math.max(left, 0)}px`;
  }

  // On a single-video page (e.g. a YouTube watch page), document.location.href
  // IS the video's URL. But on a listing page (YouTube homepage, search
  // results, channel pages) many different videos share that one page URL —
  // hovering a specific card needs THAT video's own URL instead.
  //
  // The naive approach — video.closest("a[href]") — only finds a link that
  // directly WRAPS the video element. That works when a site nests the
  // video inside its permalink <a>, but plenty of component-based sites
  // (YouTube's hover-preview <video> on the homepage/search results is a
  // common example) place the video as a SIBLING or COUSIN of the
  // permalink link instead, both sitting inside a shared "card" wrapper —
  // closest() alone never finds that.
  //
  // So: walk up through ancestors, and at each level search that whole
  // ancestor's subtree (not just direct children) for a link — whichever
  // one is found first. The safety rule that keeps this from grabbing the
  // WRONG video's link on a listing page: stop widening the instant an
  // ancestor contains more than one <video> — that means we've walked out
  // of this specific video's own card into a container shared by several
  // different videos (a whole grid/row), where any link found could
  // belong to a neighboring video instead of the one actually being
  // hovered. Silently failing (no URL found, falls back to the page's own
  // URL) is far better than silently grabbing the wrong one.
  //
  // Deliberately simple — no guessing at which links "look like" a video
  // URL and which don't. That kind of site-specific filtering is brittle
  // in exactly the way that's hard to get right without testing directly
  // against a real page, and a wrong guess does more harm (silently
  // rejecting a link that would have worked, or grabbing a plausible-
  // looking wrong one) than just taking the first link found and letting
  // the app's own fetch either succeed or fail honestly.
  //
  // One narrow, specific exception: a profile's own "tab" link (Facebook's
  // profile.php?...&sk=reels_tab, or a bare .../reels with nothing after
  // it) is that PERSON's whole list of reels/videos, never the one
  // specific video being hovered — confirmed (via screenshot) to be the
  // single most common wrong answer this ends up grabbing on Facebook's
  // Reels viewer specifically, since the creator's byline is often the
  // only real <a href> anywhere near the video at all. This skips that
  // one confirmed, recurring wrong answer and keeps looking at the next
  // ancestor level up — it's not a general "does this look like a video
  // URL" guess the way the classification system that caused problems
  // before was.
  const _PROFILE_TAB_RE = /[?&]sk=(reels|videos|photos)_tab(&|$)/i;
  const _BARE_TAB_PATH_RE = /\/(reels?|videos?)\/?$/i;
  function _isProfileTabUrl(urlString) {
    if (_PROFILE_TAB_RE.test(urlString)) return true;
    try {
      return _BARE_TAB_PATH_RE.test(new URL(urlString).pathname);
    } catch {
      return false;
    }
  }

  // Do not treat an arbitrary nearby anchor (author/profile/caption/ad) as
  // the video's permalink. Only accept URLs whose shape is recognizably a
  // single-video page or a known post permalink. A false negative is safer
  // than downloading a different video without telling the user.
  function _isPlausibleVideoPermalink(urlString) {
    try {
      const u = new URL(urlString, document.baseURI);
      if (!/^https?:$/.test(u.protocol) || _isProfileTabUrl(u.href)) return false;
      if (_urlLooksLikeSingleVideo(u.href)) return true;
      return _POST_PERMALINK_RES.some((re) => re.test(u.pathname + u.search));
    } catch {
      return false;
    }
  }

  function _stripHash(href) {
    const i = href.indexOf("#");
    return i < 0 ? href : href.slice(0, i);
  }

  // Among several permalinks, the one that actually sits UNDER the video is
  // the video's own (a card's thumbnail link, a player's wrapper link). Used
  // only to choose between DIFFERENT links; returns null if none overlaps.
  function _linkOverVideo(links, video) {
    if (!video || !video.isConnected) return null;
    const vr = video.getBoundingClientRect();
    if (!isBigEnough(vr)) return null;
    const cx = vr.left + vr.width / 2;
    const cy = vr.top + vr.height / 2;
    for (const link of links) {
      const r = link.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      if (cx >= r.left && cx <= r.right && cy >= r.top && cy <= r.bottom) return link;
    }
    return null;
  }

  function _firstUsableLink(container, video) {
    if (!container || !container.querySelectorAll) return null;
    const usable = [];
    // Prefer an explicit video permalink over any generic navigation link.
    for (const link of container.querySelectorAll("a[href]")) {
      if (!link.href || /^javascript:/i.test(link.href)) continue;
      if (_isPlausibleVideoPermalink(link.href)) usable.push(link);
    }
    if (usable.length === 0) return null;
    // Several DIFFERENT permalinks in one container means it spans more than
    // one video (a grid/row sharing a single hover-preview <video>, which the
    // ">1 <video>" rule in _resolveVideoInfo can't see). "First in document
    // order" would then be the first card's link — i.e. the previous video —
    // so pick the one lying under this video. No overlap found → exactly the
    // old behaviour (first link), so nothing that worked before changes.
    if (video && usable.length > 1) {
      const first = _stripHash(usable[0].href);
      if (usable.some((l) => _stripHash(l.href) !== first)) {
        const over = _linkOverVideo(usable, video);
        if (over) return over;
      }
    }
    return usable[0];
  }

  function _resolveVideoInfo(video) {
    if (!video) return { url: null, title: null };

    let container = video;
    for (let depth = 0; depth < 8 && container; depth++, container = container.parentElement) {
      if (!container.querySelectorAll) continue;
      if (depth > 0 && container.querySelectorAll("video").length > 1) break;
      const link = _firstUsableLink(container, video);
      if (link) {
        const resolved = new URL(link.href, document.baseURI).href;
        const title = link.getAttribute("aria-label") || link.getAttribute("title") || null;
        return { url: resolved, title };
      }
    }
    return { url: null, title: null };
  }

  // ---------------------------------------------------- multi-system fallback
  //
  // System 1 (above: _resolveVideoInfo / _firstUsableLink) finds a page
  // permalink near the video and hands it to the app, which uses yt-dlp's
  // site-specific extractor — this is what already works well for
  // YouTube. When that fails (wrong/no link found — the recurring
  // Facebook problem), instead of giving up, a few other ways of
  // identifying the SAME video are tried in turn, each only invoked if
  // the one before it actually failed:
  //
  //   System 2 — the <video>'s own currentSrc/src (or a right-clicked
  //     element's info.srcUrl — same idea, from Chrome's own context
  //     menu API instead of reading the DOM directly). This is exactly
  //     what a browser's native "Copy video URL" does — and hits the
  //     exact same wall that feature does: on sites using MSE for
  //     adaptive streaming (most big platforms), this is a blob: URL,
  //     not a real fetchable one, so it's skipped when detected as such
  //     rather than sent anywhere.
  //   System 3 — a nearby <iframe src>, for an embedded player (a
  //     YouTube/Vimeo/Facebook video embedded on a third-party page) —
  //     the iframe's own src is usually a directly-usable video URL.
  //   System 4 — document.location.href itself, but only offered as a
  //     candidate when the page's own og:video/JSON-LD VideoObject
  //     metadata suggests it genuinely IS a single-video page — a real
  //     check instead of just always assuming so, which is what caused
  //     Facebook's feed/hashtag/profile-tab URLs to get sent before.
  //
  //   System 5 — media links background.js sniffed from the tab's own
  //     network traffic (HLS/DASH manifests, plain video files), for sites
  //     yt-dlp has no extractor for. Added by _mergeSniffed() below.
  //
  // Candidates are probed concurrently, but only candidates with a plausible
  // video-page shape or a directly observed media source are admitted. A
  // successful probe alone cannot prove identity; avoid generic nearby links.
  function _isRealMediaUrl(src) {
    if (!src) return false;
    return /^https?:\/\//i.test(src);   // excludes blob:, data:, mediasource:, empty string
  }

  function _videoSrcCandidate(video) {
    if (!video) return null;
    if (_isRealMediaUrl(video.currentSrc)) return video.currentSrc;
    if (_isRealMediaUrl(video.src)) return video.src;
    const source = video.querySelector("source[src]");
    if (source && _isRealMediaUrl(source.src)) return source.src;
    return null;
  }

  function _iframeCandidate(video) {
    if (!video) return null;
    let container = video;
    for (let depth = 0; depth < 8 && container; depth++, container = container.parentElement) {
      if (!container.querySelectorAll) continue;
      if (depth > 0 && container.querySelectorAll("video").length > 1) break;
      const iframe = container.querySelector("iframe[src]");
      if (iframe && _isRealMediaUrl(iframe.src)) return iframe.src;
    }
    return null;
  }

  function _pageLooksLikeVideoPage() {
    if (document.querySelector(
      'meta[property="og:video"], meta[property="og:video:url"], meta[property="og:video:secure_url"]'
    )) return true;
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(script.textContent);
        const items = Array.isArray(data) ? data : [data];
        for (const item of items) {
          const type = item && item["@type"];
          if (type === "VideoObject" || (Array.isArray(type) && type.includes("VideoObject"))) return true;
        }
      } catch { /* not valid JSON, or not the shape expected — skip it */ }
    }
    return false;
  }

  // Single-page apps (Facebook/Instagram Reels viewers, TikTok, X, YouTube
  // Shorts...) often don't put og:video / VideoObject in the live DOM, yet
  // the address bar IS that one video's URL. Recognising those URL shapes
  // lets System 4 offer the page URL there too. Only a gate for System 4 —
  // a wrong guess costs one extra, lowest-priority probe, not a wrong answer.
  const _SINGLE_VIDEO_PATH_RES = [
    /^\/reels?\/[^/]+/i,                    // facebook.com/reel/ID, instagram.com/reel/CODE
    /^\/(?:p|tv)\/[^/]+/i,                  // instagram.com/p/CODE, /tv/CODE
    /^\/share\/(?:v|r)\/[^/]+/i,           // facebook.com/share/v/ID, /share/r/ID
    /^\/(?:[^/]+\/)?videos\/[^/]+/i,       // facebook.com/USER/videos/ID, /videos/ID
    /^\/@[^/]+\/video\/\d+/i,              // tiktok.com/@user/video/ID
    /^\/[^/]+\/status\/\d+/i,              // x.com/user/status/ID
    /^\/shorts\/[^/]+/i,                    // youtube.com/shorts/ID
    /^\/video\/[^/]+/i,                     // dailymotion.com/video/ID, etc.
  ];
  function _urlLooksLikeSingleVideo(href) {
    try {
      const u = new URL(href);
      if (/^\/watch\/?$/.test(u.pathname) && u.searchParams.has("v")) return true;   // YouTube / Facebook watch
      return _SINGLE_VIDEO_PATH_RES.some((re) => re.test(u.pathname));
    } catch {
      return false;
    }
  }

  // System 1b — a feed post (Facebook/TikTok/Instagram/X) wraps its video,
  // author, timestamp and caption in one <article>. System 1 stops widening
  // as soon as an ancestor holds a second <video> (or after 8 levels), which
  // on these deep feed layouts happens before it ever reaches the post's own
  // permalink. Here the search is bounded by the post's own <article>
  // instead, and only accepts links that look like a post/video permalink.
  const _POST_PERMALINK_RES = [/\/posts\/[^/]+/i, /\/permalink\.php/i, /\/story\.php/i, /\/permalink\/[^/]+/i];
  function _articleLinkCandidate(video) {
    if (!video || !video.closest) return null;
    const art = video.closest('[role="article"], article');
    if (!art || art.querySelectorAll("video").length > 1) return null;
    for (const a of art.querySelectorAll("a[href]")) {
      let u;
      try { u = new URL(a.href, document.baseURI); } catch { continue; }
      if (!/^https?:$/.test(u.protocol) || _isProfileTabUrl(u.href)) continue;
      if (_urlLooksLikeSingleVideo(u.href) || _POST_PERMALINK_RES.some((re) => re.test(u.pathname + u.search))) {
        return u.href;
      }
    }
    return null;
  }

  // Builds the ordered, de-duplicated candidate list described above.
  // overrideSrcUrl is Chrome's own info.srcUrl from a right-click (only
  // present for that flow — see the context_menu_download listener near
  // the bottom of this file); video is null in that same flow, so
  // _videoSrcCandidate/_iframeCandidate (which need a real element)
  // naturally contribute nothing there, and overrideSrcUrl covers System
  // 2 instead.
  function _buildCandidates(video, overrideUrl, overrideTitle, overrideSrcUrl, allowPageLevel = true) {
    const seen = new Set();
    const candidates = [];
    const add = (url, title, referer) => {
      if (!url) return;
      let resolved;
      try { resolved = new URL(url, document.baseURI).href; } catch { return; }
      if (!/^https?:\/\//i.test(resolved) || seen.has(resolved)) return;
      seen.add(resolved);
      candidates.push({ url: resolved, title: title || null, referer: referer || "" });
    };

    // A direct media URL (a <video>'s own src, an iframe) is a CDN file link:
    // fetched bare, TikTok-style CDNs answer 403. Sending the page as Referer
    // (and Origin) is what the browser itself would have done.
    const pageRef = document.location.href;

    add(overrideUrl, overrideTitle);                        // System 1
    add(_articleLinkCandidate(video), overrideTitle);        // System 1b
    add(overrideSrcUrl, overrideTitle, pageRef);             // System 2 (right-click)
    add(_videoSrcCandidate(video), overrideTitle, pageRef);  // System 2 (hover)
    add(_iframeCandidate(video), overrideTitle, pageRef);    // System 3
    // System 4 describes the PAGE's video — skipped when the hovered video is
    // clearly a different one (see _isPrimaryVideo / MULTI_VIDEO_GATE).
    if (allowPageLevel && (_pageLooksLikeVideoPage() || _urlLooksLikeSingleVideo(document.location.href))) {
      add(document.location.href, document.title);            // System 4
    }
    return candidates;
  }

  // The one shared button must not carry the PREVIOUS video's ready/loading
  // dot over to a different video.
  function _clearBtnStatus() {
    if (!activeBtn) return;
    activeBtn.classList.remove("viddown-btn-loading", "viddown-btn-ready");
  }

  function _triggerPrefetch(url, title) {
    if (!url) {            // nothing trustworthy to prefetch for this video
      _lastPrefetchUrl = null;
      _clearBtnStatus();
      return;
    }
    if (url === _lastPrefetchUrl) return;
    _lastPrefetchUrl = url;
    _clearBtnStatus();
    chrome.runtime.sendMessage({ type: "prefetch_video", url, title: title || document.title });
    // poll until done, then update button indicator
    _pollButtonStatus(url, 0);
  }

  function _pollButtonStatus(url, attempts) {
    if (attempts > 120) return;   // 60 s timeout
    setTimeout(() => {
      if (!activeBtn) return;
      chrome.runtime.sendMessage({ type: "get_prefetch_status", url }, (resp) => {
        if (!activeBtn) return;
        if (url !== _lastPrefetchUrl) return;   // the button moved on to another video
        const status = resp && resp.status;
        if (status === "done") {
          activeBtn.classList.remove("viddown-btn-loading");
          activeBtn.classList.add("viddown-btn-ready");
        } else if (status === "error") {
          activeBtn.classList.remove("viddown-btn-loading");
        } else {
          // still loading — keep polling
          _pollButtonStatus(url, attempts + 1);
        }
      });
    }, 500);
  }

  function showButtonFor(video) {
    if (!activeBtn) {
      activeBtn = makeButton();
      document.documentElement.appendChild(activeBtn);
    }
    // While actively dragging, handlePointer's own positionNear() calls
    // (triggered by mousemove passing over other tracked videos) would
    // otherwise fight with the drag's own positioning every frame.
    if (!dragState) positionNear(activeBtn, video, customOffset);
    activeBtn.style.display = "flex";
    cancelHide();
  }

  // ------------------------------------------------------------ quality panel
  function makePanel() {
    const panel = document.createElement("div");
    panel.className = "viddown-panel";

    const header = document.createElement("div");
    header.className = "viddown-panel-header";
    const title = document.createElement("span");
    title.className = "viddown-panel-title";
    title.innerText = "🎚 Select Quality";
    const close = document.createElement("span");
    close.className = "viddown-panel-close";
    close.innerText = "✕";
    close.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closePanel();
    });
    header.appendChild(title);
    header.appendChild(close);

    const body = document.createElement("div");
    body.className = "viddown-panel-body";

    panel.appendChild(header);
    panel.appendChild(body);
    document.documentElement.appendChild(panel);
    return panel;
  }

  function setPanelBody(html) {
    panelEl.querySelector(".viddown-panel-body").innerHTML = html;
  }

  function stopLoadingTimer() {
    if (loadingTimerId) {
      clearInterval(loadingTimerId);
      loadingTimerId = null;
    }
  }

  // Shows "⏳ Loading qualities... (Ns)" and bumps the seconds every tick —
  // so on a genuinely slow site, it's visibly still working instead of
  // looking identical to "frozen".
  function startLoadingTimer(baseText) {
    stopLoadingTimer();
    let seconds = 0;
    const render = () => setPanelBody(
      `<div class="viddown-panel-msg">⏳ ${baseText}${seconds > 0 ? ` (${seconds}s)` : ""}</div>`
    );
    render();
    loadingTimerId = setInterval(() => {
      seconds += 1;
      render();
    }, 1000);
  }

  // ---------------------------------------------------- background.js bridge
  //
  // Uses a long-lived port (chrome.runtime.connect), NOT one-shot
  // sendMessage. Some sites (YouTube especially) take much longer for
  // yt-dlp to extract than others (Facebook, etc.) — a one-shot message's
  // response can simply never arrive if the MV3 service worker gets
  // recycled mid-request on a slow site, with no error at all. A port
  // keeps the service worker alive for as long as it's open, AND we still
  // add our own timeout here as a second safety net regardless.
  //
  // Only ever used for "probe_video" — that's the only message type
  // background.js's chrome.runtime.onConnect listener actually handles.
  // Everything else goes through callViaMessage() below instead. (These
  // two previously drifted out of sync — download_video and
  // poll_job_status were being sent through here, but nothing on the
  // background.js side was listening for them on the port, so neither
  // ever got a real response.) Adding a new port-based call here means
  // adding a matching case in background.js's onConnect listener too.
  function callViaPort(message, onResult) {
    let settled = false;
    let port;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { port && port.disconnect(); } catch (e) { /* already gone */ }
      onResult(result);
    };

    let timer = setTimeout(() => {
      finish({ ok: false, error: `⏱ Timeout (${PROBE_TIMEOUT_MS / 1000}s) — no response received` });
    }, PROBE_TIMEOUT_MS);

    try {
      port = chrome.runtime.connect({ name: "viddown-panel" });
    } catch (err) {
      finish({ ok: false, error: "Extension connection failed — try reloading the page" });
      return;
    }

    port.onMessage.addListener((result) => {
      // A heartbeat just means background.js is still waiting on a slow
      // site — not the real answer yet. Push our own timeout back out so
      // a genuinely slow (but still working) probe isn't mistaken for a
      // dead connection.
      if (result && result.type === "probe_heartbeat") {
        clearTimeout(timer);
        timer = setTimeout(() => {
          finish({ ok: false, error: `⏱ Timeout (${PROBE_TIMEOUT_MS / 1000}s) — no response received` });
        }, PROBE_TIMEOUT_MS);
        return;
      }
      finish(result);
    });
    port.onDisconnect.addListener(() => {
      finish({ ok: false, error: "VidDown is not running (connection failed)" });
    });
    try {
      port.postMessage(message);
    } catch (err) {
      finish({ ok: false, error: "VidDown is not running (connection failed)" });
    }
  }

  // Plain request/response messaging — for download_video and
  // poll_job_status specifically, which are both short, bounded calls
  // (15s / 8s internal timeouts on the background.js side) that don't
  // need callViaPort's long-lived-connection keepalive; that's reserved
  // for probe_video, which can genuinely run up to 55s on a slow site.
  // Polling job status every 500ms-1s for a download's entire runtime
  // through a port would mean opening and tearing one down that often
  // for the whole download — a plain message is simpler and cheaper.
  function callViaMessage(message, onResult, timeoutMs = 20000) {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      onResult(result);
    };

    const timer = setTimeout(() => {
      finish({ ok: false, error: `⏱ Timeout (${timeoutMs / 1000}s) — no response received` });
    }, timeoutMs);

    try {
      chrome.runtime.sendMessage(message, (result) => {
        if (chrome.runtime.lastError) {
          finish({ ok: false, error: "VidDown is not running (connection failed)" });
          return;
        }
        finish(result);
      });
    } catch (err) {
      finish({ ok: false, error: "VidDown is not running (connection failed)" });
    }
  }

  function openPanelFor(video, overrideUrl, overrideTitle, overrideSrcUrl, snap) {
    if (!panelEl) panelEl = makePanel();
    panelOpenForVideo = video;
    // snap = what was true when the button was last shown/clicked (see
    // onButtonClick). No snap (right-click flow) → behave exactly as before.
    const primary = !snap || snap.primary !== false;

    // hide the hover button while the panel is open — they'd overlap
    if (activeBtn) activeBtn.style.display = "none";
    cancelHide();

    // guard: video may be null if the button was clicked in an edge case
    // (or the panel was opened from the right-click menu, which has no
    // <video> element to anchor to at all)
    if (video && _hasLiveRect(video)) {
      positionNear(panelEl, video);
    } else if (snap && snap.anchorRect) {
      // The <video> is gone/hidden/off-screen by now (hover-preview players
      // get removed the moment the mouse moves onto the button) and its rect
      // would read as all zeros → the panel landed in the browser's corner.
      // Use where the button the person just clicked was instead.
      positionNear(panelEl, snap.anchorRect, { dx: 0, dy: 0 });
    } else {
      // fallback: top-left of viewport
      panelEl.style.top = "20px";
      panelEl.style.left = "20px";
    }
    panelEl.style.display = "flex";

    const requestId = ++currentRequestId;
    const baseCandidates = _buildCandidates(video, overrideUrl, overrideTitle, overrideSrcUrl, primary);

    // if already prefetched, this resolves almost instantly (no spinner needed)
    const isReady = activeBtn && activeBtn.classList.contains("viddown-btn-ready");
    startLoadingTimer(isReady ? "Loading qualities..." : "Fetching video info (this may take a few seconds)...");

    // System 5 — media links background.js sniffed from this tab's network
    // traffic (HLS/DASH manifests, plain video files). Appended after the
    // four page-based systems, so they only win when those can't.
    const go = (sniffed, rejected, derived) => {
      if (requestId !== currentRequestId || !isPanelOpen()) return;
      // tab-level sniffed streams belong to the page's main video (System 5)
      const candidates = _mergeSniffed(baseCandidates, primary ? sniffed : [], primary ? derived : []);

      if (candidates.length === 0) {
        // Nothing at all found by any system — this can only ever fail, and
        // yt-dlp's "Unsupported URL" for a bare homepage is a confusing thing
        // to see when what actually happened is "couldn't find this video's
        // own link on the page".
        stopLoadingTimer();
        setPanelBody(
          `<div class="viddown-panel-msg viddown-panel-error">✕ Couldn't find this video's direct link on this page.</div>` +
          `<div class="viddown-panel-url">Try playing the video for a moment, then hover it again (or open it in its own tab).</div>` +
          _diagHtml(video, overrideUrl, overrideSrcUrl, sniffed, rejected, derived, primary)
        );
        return;
      }
      _runCandidates(candidates, requestId);
    };
    try {
      chrome.runtime.sendMessage({ type: "get_sniffed", pageUrl: document.location.href }, (resp) => {
        if (chrome.runtime.lastError) { go([]); return; }
        go(resp && resp.items ? resp.items : [], resp && resp.rejected, resp && resp.derived);
      });
    } catch (e) {
      go([]);
    }
  }

  // What each system found (or why not) — shown when there are zero
  // candidates, so a failure says exactly which layer came up empty instead
  // of just "couldn't find it".
  function _short(x, n = 90) {
    x = String(x || "");
    return x.length > n ? x.slice(0, n) + "…" : x;
  }
  function _diagHtml(video, overrideUrl, overrideSrcUrl, sniffed, rejected, derived, primary = true) {
    const info = video ? _resolveVideoInfo(video) : { url: null };
    const rawSrc = overrideSrcUrl || (video && (video.currentSrc || video.src)) || "";
    const lines = [
      `1 permalink: ${_short(overrideUrl || info.url) || "none"}`,
      `1b post link: ${_short(_articleLinkCandidate(video)) || "none"}`,
      `2 video src: ${rawSrc ? (_isRealMediaUrl(rawSrc) ? _short(rawSrc) : "blob/MSE (not downloadable)") : "none"}`,
      `3 iframe: ${_short(_iframeCandidate(video)) || "none"}`,
      `4 page URL: ${!primary ? "skipped (not the page's main video)" : ((_pageLooksLikeVideoPage() || _urlLooksLikeSingleVideo(document.location.href)) ? "ok" : "not recognised as a video page")}`,
      `5 sniffed links: ${!primary ? "skipped (not the page's main video)" : (sniffed || []).length}`,
      `5b facebook id from stream: ${(derived || []).length ? derived.map((d) => d.id).join(", ") : "none"}`,
    ];
    const rej = Object.entries(rejected || {});
    if (rej.length) lines.push("ignored: " + rej.map(([r, n]) => `${r} ×${n}`).join(", "));
    return `<div class="viddown-panel-url">${lines.map(escapeHtml).join("<br>")}</div>`;
  }

  // Adds the sniffed links (System 5) to the page-based candidates: de-duped
  // by URL (a sniffed link identical to an existing candidate just lends it
  // its Referer), capped so a busy page can't launch a dozen probes at once.
  const MAX_CANDIDATES = 6;
  function _mergeSniffed(base, sniffed, derived) {
    const out = base.map((c) => ({ ...c, referer: c.referer || "" }));
    const byUrl = new Map(out.map((c) => [c.url, c]));
    for (const item of sniffed || []) {
      if (!item || !item.url) continue;
      const existing = byUrl.get(item.url);
      if (existing) {
        if (!existing.referer) existing.referer = item.referer || "";
        continue;
      }
      if (out.length >= MAX_CANDIDATES) break;
      const c = { url: item.url, title: null, referer: item.referer || "" };
      out.push(c);
      byUrl.set(c.url, c);
    }
    // System 5b — page URLs rebuilt from stream URLs (Facebook video IDs)
    for (const item of derived || []) {
      if (!item || !item.url || byUrl.has(item.url) || out.length >= MAX_CANDIDATES) continue;
      const c = { url: item.url, title: null, referer: "" };
      out.push(c);
      byUrl.set(c.url, c);
    }
    return out;
  }

  // Probes EVERY candidate at the same time (the app's bridge server handles
  // each request on its own thread) and then picks by priority:
  //   - a candidate only counts as a success if it returned at least one
  //     quality (yt-dlp "succeeding" with an empty list used to be taken as
  //     the final answer, which left the panel stuck on "No qualities");
  //   - the highest-priority success wins as soon as every candidate ranked
  //     above it has settled;
  //   - if a lower-priority one succeeds while a higher one is still running,
  //     the higher one gets GRACE_MS more to finish before we move on —
  //     so one slow/hanging candidate can never block the others (before,
  //     they ran one after another, up to ~1 minute each).
  const GRACE_MS = 6000;
  function _runCandidates(candidates, requestId) {
    const n = candidates.length;
    const results = new Array(n).fill(null);   // null = still running
    let finished = false;
    let graceTimer = null;

    const stop = () => { finished = true; clearTimeout(graceTimer); };

    const winWith = (i) => {
      stop();
      stopLoadingTimer();
      const c = candidates[i];
      renderQualityList(results[i].qualities, c.url, c.title || document.title, c.referer || "", results[i].title);
    };

    const evaluate = () => {
      if (finished) return;
      if (requestId !== currentRequestId || !isPanelOpen()) { stop(); return; }

      for (let i = 0; i < n; i++) {
        if (results[i] === null) break;          // a higher-priority one is still running
        if (results[i].ok) { winWith(i); return; }
      }

      if (results.every((r) => r !== null)) {    // all settled, none usable
        stop();
        stopLoadingTimer();
        const tried = candidates.map((c) => escapeHtml(c.url)).join("<br>");
        setPanelBody(
          `<div class="viddown-panel-msg viddown-panel-error">✕ Couldn't fetch this video (tried ${n} way${n > 1 ? "s" : ""}).</div>` +
          `<div class="viddown-panel-url">${tried}</div>`
        );
        return;
      }

      if (!graceTimer && results.some((r) => r && r.ok)) {
        graceTimer = setTimeout(() => {
          if (finished) return;
          if (requestId !== currentRequestId || !isPanelOpen()) { stop(); return; }
          const i = results.findIndex((r) => r && r.ok);
          if (i >= 0) winWith(i);
        }, GRACE_MS);
      }
    };

    candidates.forEach((c, i) => {
      callViaPort(
        { type: "probe_video", url: c.url, title: c.title || document.title, referer: c.referer || "" },
        (res) => {
          const usable = !!(res && res.ok && res.qualities && res.qualities.length);
          results[i] = usable ? res : { ok: false, error: (res && res.error) || "no qualities" };
          evaluate();
        }
      );
    });
  }

  function renderQualityList(qualities, url, title, referer, foundTitle) {
    if (!qualities || !qualities.length) {
      setPanelBody(`<div class="viddown-panel-msg">No qualities available.</div>`);
      return;
    }
    const body = panelEl.querySelector(".viddown-panel-body");
    body.innerHTML = "";
    if (foundTitle) {
      // which video the app actually found — guards against a wrong guess
      const vt = document.createElement("div");
      vt.className = "viddown-panel-vtitle";
      vt.innerText = "🎬 " + _short(foundTitle, 70);
      body.appendChild(vt);
    }
    qualities.forEach((q) => {
      const row = document.createElement("div");
      row.className = "viddown-quality-row" + (q.is_duplicate ? " viddown-quality-duplicate" : "");
      const dot = document.createElement("span");
      dot.className = "viddown-quality-dot";
      dot.innerText = "○";
      const label = document.createElement("span");
      label.className = "viddown-quality-label";
      label.innerText = q.label;
      const size = document.createElement("span");
      size.className = "viddown-quality-size";
      size.innerText = q.size_label || "";
      row.appendChild(dot);
      row.appendChild(label);
      if (q.is_duplicate) {
        const badge = document.createElement("span");
        badge.className = "viddown-quality-badge";
        badge.innerText = "✓ downloaded";
        row.appendChild(badge);
      }
      row.appendChild(size);
      row.addEventListener("click", () => onQualityPicked(q, url, title, referer));
      body.appendChild(row);
    });
    // the panel was positioned while still showing a short loading message;
    // now that the list made it taller, pull it back inside the viewport
    _clampPanelIntoViewport();
  }

  function onQualityPicked(quality, url, title, referer) {
    if (quality.is_duplicate) {
      const proceed = confirm(
        `"${title}" at ${quality.label} looks like it's already downloaded or queued.\n\nDownload it again anyway?`
      );
      if (!proceed) return;
    }
    const requestId = ++currentRequestId;
    startLoadingTimer("Sending...");
    callViaMessage(
      { type: "download_video", url, title, index: quality.index, referer: referer || "" },
      (result) => {
        if (requestId !== currentRequestId || !isPanelOpen()) return;
        stopLoadingTimer();
        if (!result || !result.ok) {
          const err = (result && result.error) || "Download failed";
          setPanelBody(`<div class="viddown-panel-msg viddown-panel-error">✕ ${escapeHtml(err)}</div>`);
          return;
        }
        closePanel();
        createProgressCard(result.job_id, title || url);
      },
      20000
    );
  }

  // ── IDM-style floating download progress popup ──────────────────────────
  let progressStack = null;

  function getProgressStack() {
    if (progressStack && document.documentElement.contains(progressStack)) {
      return progressStack;
    }
    progressStack = document.createElement("div");
    progressStack.className = "viddown-progress-stack";
    document.documentElement.appendChild(progressStack);
    return progressStack;
  }

  function createProgressCard(jobId, title) {
    const stack = getProgressStack();

    const card = document.createElement("div");
    card.className = "viddown-progress-card";
    card.innerHTML = `
      <div class="viddown-progress-head">
        <span class="viddown-progress-title">🎬 ${escapeHtml(title)}</span>
        <span class="viddown-progress-close" title="Dismiss">✕</span>
      </div>
      <div class="viddown-progress-track">
        <div class="viddown-progress-fill" style="width:0%"></div>
        <span class="viddown-progress-pct">0%</span>
      </div>
      <div class="viddown-progress-meta">Starting…</div>
    `;
    stack.appendChild(card);

    const closeBtn = card.querySelector(".viddown-progress-close");
    let pollTimer = null;

    const stopPolling = () => {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
    };

    closeBtn.addEventListener("click", () => {
      stopPolling();
      card.remove();
    });

    const fillEl  = card.querySelector(".viddown-progress-fill");
    const pctEl   = card.querySelector(".viddown-progress-pct");
    const metaEl  = card.querySelector(".viddown-progress-meta");
    const titleEl = card.querySelector(".viddown-progress-title");

    let missCount = 0;      // tolerate a few early "unknown job_id" 404s
    let reconnecting = false;

    function pollViaMessage() {
      callViaMessage({ type: "poll_job_status", jobId }, (result) => {
        if (!document.documentElement.contains(card)) return;  // card was dismissed

        if (!result || !result.ok) {
          missCount++;
          if (missCount <= 6) {
            // brief blip (a couple of seconds) — keep polling at the normal pace
            pollTimer = setTimeout(pollViaMessage, 500);
            return;
          }
          // Past that, the app itself may just be restarting (an update,
          // a crash recovery, briefly closed) rather than gone for good —
          // the download job very likely still exists and is still
          // running server-side regardless of whether THIS tab can reach
          // it right now. Giving up permanently here would strand the
          // card showing a dead end even once the app comes back, so
          // instead back off to a slower, bounded retry (~5 more minutes)
          // before finally admitting defeat.
          reconnecting = true;
          const totalMisses = missCount - 6;
          if (totalMisses > 75) {  // 75 × 4s ≈ 5 minutes
            metaEl.textContent = "✕ Lost connection to VidDown — reopen the app and check the Queue page";
            card.classList.remove("viddown-progress-reconnecting");
            card.classList.add("viddown-progress-error");
            stopPolling();
            return;
          }
          metaEl.textContent = "⚠ Reconnecting to VidDown…";
          card.classList.add("viddown-progress-reconnecting");
          pollTimer = setTimeout(pollViaMessage, 4000);
          return;
        }
        missCount = 0;
        if (reconnecting) {
          reconnecting = false;
          card.classList.remove("viddown-progress-reconnecting");
        }

        // sync the header with the server's title once it's known —
        // this now includes the quality tag (e.g. "Some Video [1080p]"),
        // matching what the app's own Queue page shows
        if (result.title && titleEl.textContent !== `🎬 ${result.title}`) {
          titleEl.textContent = `🎬 ${result.title}`;
        }

        const status = result.status;
        const pct    = Math.max(0, Math.min(100, result.progress || 0));

        fillEl.style.width = `${pct}%`;
        pctEl.textContent  = `${pct.toFixed(0)}%`;

        if (status === "downloading") {
          const speed = result.speed || "";
          const eta   = result.eta   || "";
          metaEl.textContent = [speed, eta ? `ETA ${eta}` : ""].filter(Boolean).join("  ·  ") || "Downloading…";
          pollTimer = setTimeout(pollViaMessage, 500);

        } else if (status === "queued") {
          metaEl.textContent = "Waiting in queue…";
          pollTimer = setTimeout(pollViaMessage, 800);

        } else if (status === "completed") {
          fillEl.style.width = "100%";
          pctEl.textContent  = "100%";
          metaEl.textContent = "✓ Download complete";
          card.classList.add("viddown-progress-done");
          stopPolling();
          setTimeout(() => card.remove(), 4000);

        } else if (status === "error") {
          metaEl.textContent = `✕ ${result.error || "Download failed"}`;
          card.classList.add("viddown-progress-error");
          stopPolling();

        } else if (status === "cancelled") {
          metaEl.textContent = "Cancelled";
          card.classList.add("viddown-progress-error");
          stopPolling();

        } else if (status === "paused") {
          metaEl.textContent = "⏸ Paused";
          pollTimer = setTimeout(pollViaMessage, 1000);

        } else {
          pollTimer = setTimeout(pollViaMessage, 800);
        }
      });
    }

    pollViaMessage();
  }

  function closePanel() {
    stopLoadingTimer();
    currentRequestId++; // invalidate any in-flight request
    if (panelEl) panelEl.style.display = "none";
    panelOpenForVideo = null;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.innerText = str;
    return div.innerHTML;
  }

  // close the panel if the person clicks anywhere outside it
  document.addEventListener(
    "click",
    (e) => {
      if (panelEl && panelEl.style.display !== "none" && !panelEl.contains(e.target) && e.target !== activeBtn) {
        closePanel();
      }
    },
    true
  );
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePanel();
  });

  function onButtonClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (suppressNextClick) {
      suppressNextClick = false;
      return;
    }
    const btn   = e.currentTarget;
    const video = btn._viddownVideo;
    const url   = btn._viddownUrl;
    const title = btn._viddownTitle;
    // Snapshot taken NOW, before openPanelFor hides the button: where it is
    // on screen (fallback anchor) and whether its video is the page's main one.
    // (No more "|| panelOpenForVideo" — that was the PREVIOUS panel's video.)
    const br = btn.getBoundingClientRect();
    openPanelFor(video, url, title, undefined, {
      anchorRect: { left: br.left, top: br.top, width: br.width, height: br.height },
      primary: btn._viddownPrimary !== false,
    });
  }

  // -------------------------------------------------------- video detection
  //
  // IMPORTANT: we deliberately do NOT attach mouseenter/mouseleave directly
  // to each <video> element. Many sites (Facebook, and others) place an
  // invisible overlay div ON TOP of the actual <video> tag — for a custom
  // play button, ad layer, or click-to-expand UI. The mouse cursor visually
  // sits over the video, but every mouse event actually lands on that
  // overlay div instead, so a listener on the <video> element itself would
  // never fire. Instead, we track raw cursor position on the whole
  // document and geometrically check it against every known video's
  // bounding box — this works no matter what's stacked on top of it.

  let trackedVideos = [];
  let rafPending = false;

  function isBigEnough(rect) {
    return rect.width >= MIN_SIZE && rect.height >= MIN_SIZE;
  }

  // When multiple videos' boxes overlap the cursor (a small recommended-
  // video thumbnail sitting inside/near the main player, a picture-in-
  // picture mini view, etc.), prefer the LARGEST one — that's reliably
  // the "main" video the person actually means on every site we've seen.
  function findHoveredVideo(x, y) {
    let best = null;
    let bestArea = -1;
    for (const video of trackedVideos) {
      if (!video.isConnected) continue;
      const rect = video.getBoundingClientRect();
      if (!isBigEnough(rect)) continue;
      if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) continue;
      const area = rect.width * rect.height;
      if (area > bestArea) {
        best = video;
        bestArea = area;
      }
    }
    return best;
  }

  // ---- helpers for the "which video is this button really for" logic ------

  function _hasLiveRect(video) {
    if (!video || !video.isConnected) return false;
    const r = video.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    // wholly outside the viewport also can't be anchored to
    return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
  }

  function _visibleArea(video) {
    const r = video.getBoundingClientRect();
    const w = Math.min(r.right, window.innerWidth) - Math.max(r.left, 0);
    const h = Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0);
    return w > 0 && h > 0 ? w * h : 0;
  }

  // True unless another sizeable video on screen is clearly bigger (1.5x+)
  // than this one — then this is a side/preview/thumbnail video and the
  // page-wide fallbacks (page URL, sniffed streams) would describe the
  // other, main one. Ties and near-equal sizes count as "primary" so feed
  // viewers (Reels/TikTok, several same-size videos mounted) are unaffected.
  function _isPrimaryVideo(video) {
    if (!MULTI_VIDEO_GATE || !video) return true;
    const mine = _visibleArea(video);
    for (const v of trackedVideos) {
      if (v === video || !v.isConnected) continue;
      if (!isBigEnough(v.getBoundingClientRect())) continue;
      if (_visibleArea(v) > mine * 1.5) return false;
    }
    return true;
  }

  // handlePointer runs on every animation frame while the mouse moves; link
  // resolution walks up to 8 ancestor subtrees, so reuse the answer while
  // the same video sits at the same place on the same page.
  let _infoCache = null;
  function _hoverInfoFor(video) {
    const r = video.getBoundingClientRect();
    const key = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height), document.location.href].join("|");
    const now = Date.now();
    if (_infoCache && _infoCache.video === video && _infoCache.key === key && now - _infoCache.ts < 1500) {
      return _infoCache;
    }
    _infoCache = { video, key, ts: now, info: _resolveVideoInfo(video), primary: _isPrimaryVideo(video) };
    return _infoCache;
  }

  function _clampPanelIntoViewport() {
    if (!panelEl || !isPanelOpen()) return;
    const margin = 8;
    const w = panelEl.offsetWidth, h = panelEl.offsetHeight;
    let top  = parseFloat(panelEl.style.top)  || 0;
    let left = parseFloat(panelEl.style.left) || 0;
    if (top  + h > window.innerHeight - margin) top  = window.innerHeight - h - margin;
    if (left + w > window.innerWidth  - margin) left = window.innerWidth  - w - margin;
    panelEl.style.top  = `${Math.max(top,  0)}px`;
    panelEl.style.left = `${Math.max(left, 0)}px`;
  }

  function handlePointer(x, y) {
    // While dragging the button itself, the cursor is being used to
    // reposition it, not to hover content — whatever video happens to be
    // under it at any given moment mid-drag is irrelevant, and must NOT
    // reassign which video this button is tied to.
    if (dragState) return;
    // ignore moves while the cursor is over our own button/panel
    if (panelEl && panelEl.style.display !== "none" && panelEl.contains(document.elementFromPoint(x, y))) {
      return;
    }
    const video = findHoveredVideo(x, y);
    if (video && appConnected) {
      showButtonFor(video);
      if (activeBtn) {
        const hi   = _hoverInfoFor(video);
        const info = hi.info;
        activeBtn._viddownVideo   = video;
        activeBtn._viddownUrl     = info.url;    // null on a single-video page — falls back to document.location.href
        activeBtn._viddownTitle   = info.title;  // null if not found — falls back to document.title
        activeBtn._viddownPrimary = hi.primary;
        // The page URL is only a sensible guess for the page's main video.
        _triggerPrefetch(info.url || (hi.primary ? document.location.href : null), info.title || document.title);
      }
      cancelHide();
    } else {
      scheduleHide();
    }
  }

  document.addEventListener(
    "mousemove",
    (e) => {
      if (rafPending) return;
      rafPending = true;
      const x = e.clientX, y = e.clientY;
      window.requestAnimationFrame(() => {
        rafPending = false;
        handlePointer(x, y);
      });
    },
    true // capture phase — fires even if something deeper calls stopPropagation()
  );

  // Some modern sites render their player inside an open shadow root.
  // querySelectorAll on the main document alone won't see those — walk
  // into every open shadow root we can find too.
  function collectVideos(root, out) {
    root.querySelectorAll("video").forEach((v) => out.push(v));
    root.querySelectorAll("*").forEach((el) => {
      if (el.shadowRoot) collectVideos(el.shadowRoot, out);
    });
  }

  function scan() {
    const found = [];
    collectVideos(document, found);
    trackedVideos = found;
  }

  scan();

  // SPA sites (YouTube, Facebook, etc.) load <video> tags dynamically as
  // you navigate/scroll — keep watching the DOM for newly added ones.
  // scheduleScan (not scan directly) coalesces a whole burst of mutations
  // into a single scan per animation frame — on a busy listing page (many
  // videos, constant lazy-loading/hover-preview insert-and-remove churn),
  // a full document scan on every single mutation record can't keep up,
  // leaving trackedVideos stale right when a newly-inserted hover-preview
  // video most needs to be found.
  let scanPending = false;
  function scheduleScan() {
    if (scanPending) return;
    scanPending = true;
    requestAnimationFrame(() => {
      scanPending = false;
      scan();
    });
  }
  const observer = new MutationObserver(() => scheduleScan());
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // hide the floating button on scroll so it doesn't drift away from the
  // video it was pointing at; it reappears on the next mousemove. Leave
  // the panel open though — closing it on every scroll would be annoying.
  window.addEventListener(
    "scroll",
    () => {
      if (activeBtn && activeBtn.style.display !== "none" && !isPanelOpen()) {
        activeBtn.style.display = "none";
      }
    },
    true
  );

  // "Download with VidDown" right-click menu (background.js) lands here —
  // no <video> element to anchor to, so the panel falls back to its
  // top-left position, and the target URL comes from the context menu
  // (a right-clicked link, or the page itself) rather than document.location.
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === "context_menu_download") {
      openPanelFor(null, msg.url, msg.title, msg.srcUrl);
    }
    if (msg.type === "viddown_connection") {
      appConnected = !!msg.connected;
      // Don't wait for the next mousemove to react to a disconnect —
      // a button already on screen when the app closes should disappear
      // right away rather than sit there promising a click that can only fail.
      if (!appConnected && activeBtn && !isPanelOpen()) {
        activeBtn.style.display = "none";
      }
    }
  });
})();
