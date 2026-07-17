# RILA (Reel Intent Location Assistant)

## Instagram Authentication (Cookies)

RILA uses `yt-dlp` to download reels. While YouTube Shorts can be downloaded anonymously, Instagram actively blocks anonymous downloads. To allow RILA to download Instagram reels, you need to provide an authenticated cookie file.

**Important:** Do not automate Instagram login or use your primary account's credentials in a script, as this violates Instagram's Terms of Service and can get your account banned. Instead, use a manual export method.

### How to set up Instagram Cookies

1. Install a browser extension like **"Get cookies.txt LOCALLY"** (available for Chrome and Firefox).
2. Open a new tab and log into [instagram.com](https://instagram.com) normally.
3. Once logged in, click the extension icon and select **Export** (ensure you are exporting cookies specifically for `instagram.com`).
4. Save the exported file as `instagram_cookies.txt`.
5. Place this file inside the `cookies/` directory in the root of the RILA repository. (e.g., `cookies/instagram_cookies.txt`). This directory is gitignored and will never be committed.
6. Make sure `INSTAGRAM_COOKIES_PATH=/app/cookies/instagram_cookies.txt` is set in your `.env` file.

### Known Limitations
*   **Cookie Expiry:** Instagram cookies expire periodically (typically every few weeks to months). When RILA starts failing to download Instagram reels with a "Private video" or similar access error, you will need to re-export the cookies and replace the file. This is expected maintenance for a personal tool.
