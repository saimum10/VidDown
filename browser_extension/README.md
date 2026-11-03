# VidDown Companion — Browser Extension

Chrome / Brave / Edge-এ ভিডিও পেজে **hover করলে একটা "⬇ VidDown" বাটন ভেসে ওঠে**
(IDM-এর মতো) — ক্লিক করলে সেই পেজেই একটা **quality-picker panel** খোলে, আর
কোয়ালিটি বাছলেই সরাসরি VidDown-এর Queue-তে ডাউনলোড শুরু হয়ে যায় — app-এ গিয়ে
আলাদা করে কিছু করতে হয় না।

## কীভাবে ইন্সটল করবেন (Developer mode — unpacked extension)

এটা এখনো Chrome Web Store-এ পাবলিশ করা নেই, তাই "Load unpacked" দিয়ে ইন্সটল করতে হবে:

1. Chrome/Brave/Edge-এ গিয়ে ঠিকানা বারে লিখুন: `chrome://extensions` (Brave: `brave://extensions`, Edge: `edge://extensions`)
2. উপরে-ডানে **Developer mode** টগল অন করুন
3. **Load unpacked** বাটনে ক্লিক করুন
4. এই `browser_extension/` ফোল্ডারটা সিলেক্ট করুন
5. টুলবারে VidDown আইকন দেখা যাবে — pin করে রাখুন যাতে সবসময় দেখা যায়

## কীভাবে কাজ করে

- **VidDown desktop app খোলা থাকতে হবে** — এক্সটেনশন শুধু `127.0.0.1:47892`-এ (নিজের
  কম্পিউটারেই) কথা বলে, ইন্টারনেটে কোথাও যায় না।
- টুলবার আইকনের পাশে সবুজ **✓** মানে VidDown চালু ও সংযুক্ত আছে, লাল **✕** মানে
  VidDown app খোলা নেই — আগে app চালু করুন।
- কোনো ভিডিওর উপর মাউস নিয়ে গেলে কোণে নীল **"⬇ VidDown"** বাটন ভাসবে। ক্লিক করলে:
  1. ঠিক সেখানেই একটা প্যানেল খোলে, প্রথমে "⏳ Loading qualities..." দেখায়
  2. VidDown app yt-dlp দিয়ে video info fetch করে কোয়ালিটি লিস্ট (144p–4K +
     Audio Only, সাইজ সহ) পাঠায়, প্যানেলে দেখা যায়
  3. একটা কোয়ালিটিতে ক্লিক করলে সাথে সাথে VidDown-এর Queue-তে ডাউনলোড শুরু হয়ে
     যায় (app-টা সামনে চলে আসবে যাতে progress দেখা যায়), আর প্যানেল বন্ধ হয়ে যায়
  4. প্যানেলের বাইরে ক্লিক করলে, ✕ বাটনে ক্লিক করলে, বা Escape চাপলে — প্যানেল
     কিছু না বেছেই বন্ধ হয়ে যাবে
- ভিডিও খুঁজে না পেলে বা বাটন না দেখালে, টুলবারের VidDown আইকনে সরাসরি ক্লিক করলে
  **current tab**-এর URL সরাসরি VidDown-এর Download পেজে পাঠিয়ে দেবে (manual
  fallback — এক্ষেত্রে quality panel খোলে না, app-এ গিয়ে নিজে quality বাছতে হবে)।

## জানা সীমাবদ্ধতা

- yt-dlp যত সাইট সাপোর্ট করে (১৮০০+), নীতিগতভাবে সবগুলোতেই হোভার বাটন+কোয়ালিটি+ডাউনলোড কাজ করার কথা। তবে কিছু সাইটে video info fetch করতে বেশি সময় লাগে (অনেক format থাকলে) — প্যানেলে "Loading... (Ns)" দেখিয়ে সেটা বোঝানো হয়, আর ৬০ সেকেন্ডের মধ্যে রেসপন্স না এলে স্পষ্ট timeout error দেখাবে (চুপচাপ আটকে থাকবে না)।
- Embed/iframe-এ থাকা ভিডিওর ক্ষেত্রে কখনো কখনো iframe-এর URL পাঠানো হয় (পেজের
  মূল URL না) — অনেক সাইটে (যেমন YouTube embed) এটা ঠিকঠাক কাজ করে, তবে সব
  সাইটে guaranteed না।
- Firefox-এর জন্য এখনো বানানো হয়নি (পরে চাইলে যোগ করা যাবে — Manifest পাল্টাতে হবে)।

## Cross-site robustness (যেভাবে সব সাইটে কাজ করানো হয়েছে)

- **Overlay-proof hover detection**: অনেক সাইট (Facebook, ইত্যাদি) ভিডিওর উপর invisible overlay div রাখে (play বাটন/ad লেয়ার) — তাই `<video>` element-এ সরাসরি listener বসানোর বদলে, পুরো পেজে mouse position track করে video-র bounding box-এর সাথে geometrically মিলিয়ে দেখা হয়।
- **Largest-video-wins**: একই জায়গায় একাধিক video element overlap করলে (যেমন PIP/thumbnail preview প্রধান প্লেয়ারের ভেতরে), সবচেয়ে বড়টা বাছা হয়।
- **Shadow DOM সাপোর্ট**: কিছু আধুনিক সাইট player শ্যাডো DOM-এর ভেতরে রাখে — সেখানেও খোঁজা হয়।
- **Long-lived port + timeout**: ধীর সাইটে (অনেক format, ধীর extraction) one-shot message ব্যবহার করলে Chrome-এর service worker মাঝপথে বন্ধ হয়ে যেতে পারে কোনো error ছাড়াই। তাই `chrome.runtime.connect()` দিয়ে দীর্ঘ-স্থায়ী সংযোগ ব্যবহার করা হয়, প্লাস ৬০ সেকেন্ড ক্লায়েন্ট-সাইড timeout — যাতে কখনো নিঃশব্দে চিরকাল আটকে না থাকে।


## v2.9.1 — network sniffing + parallel candidates

- **System 5 (network sniffing):** `background.js` now watches each tab's
  network traffic (`webRequest`, observe-only) and remembers HLS/DASH
  manifests (`.m3u8`, `.mpd`) and large video files (`.mp4`, `.webm`, ...).
  When the page-based systems 1-4 can't produce a quality list, these links
  are tried too, with the page's Referer/Origin forwarded to yt-dlp.
  Ignored on purpose: segments (`.ts`/`.m4s`), files under 300 KB, YouTube's
  `googlevideo.com` chunks, byte-range pieces.
- **Candidates run in parallel** (was one after another, ~1 min each). The
  highest-priority candidate that returns at least one quality wins; a hung
  one gets a 6 s grace period once a lower-priority one has succeeded.
- **Empty quality list = failure** (was treated as success, which stopped the
  search and left the panel on "No qualities available").
- Direct video files with no height metadata now get an
  "Original (best available)" option.
- New permissions: `webRequest`, `storage`, and host access to all sites
  (needed to observe other sites' requests). After updating, reload the
  extension at chrome://extensions and accept the new permissions.

## v2.9.2

- **Facebook without opening the player:** Facebook streams a video as
  hundreds of byte-range chunks (ignored on purpose). Each chunk URL carries
  an `efg` parameter that includes the video's ID; the extension now rebuilds
  `https://www.facebook.com/watch/?v=<id>` from it (system 5b) and lets
  yt-dlp fetch the full video + audio. The panel shows the found video's
  title so a wrong guess is visible before downloading.
- **System 1b:** post-bounded permalink search inside the video's own
  `<article>` (feed layouts that are too deep for system 1).
- **Referer on direct media URLs** (a `<video>`'s own src, iframes): sent as
  Referer/Origin so CDNs like TikTok's don't answer 403.


## v2.9.3 — safer source selection

- Nearby anchors are no longer accepted as video links unless their URL looks like a known single-video page or post permalink. This reduces accidental selection of profile, caption, ad, or unrelated navigation links.
- Network-sniffed candidates are limited to the current page context and a shorter two-minute window. They are only offered automatically when there is one unambiguous manifest, or one direct-file candidate and no manifest.
- Removed the tab-global Facebook CDN-ID-to-watch-URL guess because the most recently observed ID cannot reliably be associated with the video under the pointer.
- Removed the broad `rn` query-parameter exclusion, which could discard legitimate URLs.
- Partial HTTP 206 Content-Range responses are no longer presented as complete video files.
- Validation performed: JavaScript syntax checks and Python compilation checks. Live tests on third-party platforms have not been performed; platform availability and site restrictions still apply.


## v2.9.4
- The desktop app Cookies page now includes a User-Agent selector for desktop, phone, and tablet browser profiles, plus Automatic and Random modes.
- Random mode chooses one curated profile per queued download. User-Agent changes do not bypass login requirements, DRM, or site restrictions; matching the browser that supplied the cookies is often more reliable.
