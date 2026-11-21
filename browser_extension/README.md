# VidDown Companion — browser extension

Adds a **VidDown** button to videos in Chrome, Brave and Edge. Click it, pick a quality, and the download starts in the VidDown app.

## Install

The extension is not on the Web Store, so it is added by hand:

1. Open `chrome://extensions` (`brave://extensions`, `edge://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and select this `browser_extension` folder. You can also open it from VidDown: **Settings → Browser extension → Open folder**.
4. Pin the VidDown icon to the toolbar.

After updating VidDown, reload the extension on the same page and refresh open tabs.

## Use

1. Start the VidDown app. The extension only talks to it on `127.0.0.1:47892` (your own computer).
2. Hover over a video and click **VidDown**. A panel lists the available qualities.
3. Click a quality. The download starts in VidDown's queue.

The toolbar icon shows a green check when VidDown is running and a red cross when it is not. If no button appears on a page, click the toolbar icon to send the current tab's address to VidDown's Download page.

## Notes

- It works with the sites yt-dlp supports. Some sites are slow; the panel shows a timeout message after 60 seconds.
- It needs access to all sites so it can find videos on any page and see their media requests.
- Firefox is not supported yet.
