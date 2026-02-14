import os
import sqlite3
import time
import random
import requests
from scrapy.exceptions import DropItem


# =========================
# Config via environment
# =========================
PAGE_ID = os.environ.get("FB_PAGE_ID", "208327419022837")
PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN","EAASipm90TW8BQqVZCguKgUMFi6len98PwwfbjEvndWNeTcaxwXj7iJQju9X32jl55OAdiZCnIf5T6Lj3dViz8nV3lyKTWdU0lMTZBhCjGIRmDWo66BcShdZBHqnGAYSFWsvVYjraju1w3zhGWSBpb4ZC4Aa6JLwZCHGR611UsN6wN06cNMDGGNpwbGPDvTZAsAd5ZB07hjFARujcYxGvLxjxdCGiMKYPYtOWlPXUrpkWcZCysM0Qga7VvLwDk7b4ZD")
GRAPH_API_VERSION = os.environ.get("FB_GRAPH_API_VERSION", "v24.0")

DB_PATH = os.environ.get("REDDIT_TO_FB_DB", "posted.db")
UPLOAD_MIN_DELAY = float(os.environ.get("UPLOAD_MIN_DELAY", "2.0"))
UPLOAD_MAX_DELAY = float(os.environ.get("UPLOAD_MAX_DELAY", "6.0"))


class DedupeDownloadUploadPipeline:
    """
    Pipeline that:
    - Prevents duplicate Reddit posts
    - Stores all metadata locally (SQLite)
    - Stores media files locally
    - Uploads media to Facebook Page
    - Tracks success / failure
    """

    # =========================
    # Spider lifecycle
    # =========================
    def open_spider(self, spider):
        self.conn = sqlite3.connect(DB_PATH)
        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS posted (
            post_id TEXT PRIMARY KEY,
            reddit_url TEXT,
            title TEXT,
            subreddit TEXT,
            author TEXT,
            post_type TEXT,
            media_url TEXT,
            local_path TEXT,
            fb_post_id TEXT,
            upload_status TEXT,
            error_message TEXT,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        self.conn.commit()

    def close_spider(self, spider):
        self.conn.close()

    # =========================
    # Main pipeline
    # =========================
    def process_item(self, item, spider):
        post_id = item.get("post_id")

        # Skip unsupported Reddit posts
        if item.get("type") not in ("image", "video"):
            raise DropItem("Unsupported Reddit post type")

        if self._is_posted(post_id):
            raise DropItem(f"Already processed: {post_id}")

        # Try file downloaded by FilesPipeline
        local_path = self._get_local_file(item, spider)

        # Fallback: direct download
        if not local_path:
            try:
                local_path = self._download_direct(item["url"], spider)
            except Exception as e:
                self._record_posted(item, None, None, "failed", str(e))
                raise DropItem(f"Media download failed: {e}")

        # Upload to Facebook
        try:
            fb_post_id = self._upload_to_facebook(local_path, item)
            self._record_posted(item, local_path, fb_post_id, "success")
        except Exception as e:
            spider.logger.error(f"Facebook upload failed: {e}")
            self._record_posted(item, local_path, None, "failed", str(e))
            raise DropItem("FB upload failed")

        # Rate-limit safety
        delay = random.uniform(UPLOAD_MIN_DELAY, UPLOAD_MAX_DELAY)
        spider.logger.info(f"Sleeping {delay:.1f}s to avoid rate limits")
        time.sleep(delay)

        return item

    # =========================
    # Helpers
    # =========================
    def _is_posted(self, post_id):
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM posted WHERE post_id = ?", (post_id,))
        return cur.fetchone() is not None

    def _record_posted(self, item, local_path, fb_post_id, status, error=None):
        cur = self.conn.cursor()
        cur.execute("""
        INSERT OR REPLACE INTO posted (
            post_id,
            reddit_url,
            title,
            subreddit,
            author,
            post_type,
            media_url,
            local_path,
            fb_post_id,
            upload_status,
            error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.get("post_id"),
            item.get("permalink"),
            item.get("title"),
            item.get("subreddit"),
            item.get("author"),
            item.get("type"),
            item.get("url"),
            local_path,
            fb_post_id,
            status,
            error
        ))
        self.conn.commit()

    def _get_local_file(self, item, spider):
        files = item.get("files") or []
        if not files:
            return None

        store = spider.settings.get("FILES_STORE", "media")
        path = os.path.join(store, files[0].get("path"))
        return path if os.path.exists(path) else None

    def _download_direct(self, url, spider):
        headers = {"User-Agent": "reddit-media-scraper"}
        resp = requests.get(url, stream=True, timeout=60, headers=headers)
        resp.raise_for_status()

        ext = os.path.splitext(url.split("?")[0])[1] or ".bin"
        filename = f"reddit_{int(time.time() * 1000)}{ext}"

        folder = spider.settings.get("FILES_STORE", "media")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)

        with open(path, "wb") as f:
            for chunk in resp.iter_content(1024 * 64):
                if chunk:
                    f.write(chunk)

        return path

    # =========================
    # Facebook upload
    # =========================
    def _upload_to_facebook(self, local_path, item):
        if not PAGE_ID or not PAGE_ACCESS_TOKEN:
            raise Exception("Facebook credentials not set")

        ext = os.path.splitext(local_path)[1].lower()
        is_video = item.get("type") == "video" or ext in (".mp4", ".mov", ".webm", ".mkv")

        caption = item.get("title") or ""

        if is_video:
            url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PAGE_ID}/videos"
            data = {
                "description": caption,
                "access_token": PAGE_ACCESS_TOKEN,
            }

            with open(local_path, "rb") as f:
                files = {"file": f}
                resp = requests.post(url, files=files, data=data, timeout=300)

        else:
            url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PAGE_ID}/photos"
            data = {
                "caption": caption,
                "access_token": PAGE_ACCESS_TOKEN,
                "published": "true",
            }

            with open(local_path, "rb") as f:
                files = {"source": f}
                resp = requests.post(url, files=files, data=data, timeout=120)

        if not resp.ok:
            raise Exception(f"Facebook API error {resp.status_code}: {resp.text}")

        j = resp.json()
        return j.get("post_id") or j.get("id")

