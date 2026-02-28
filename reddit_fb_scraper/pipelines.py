import os
import sqlite3
import time
import random
import requests
import mimetypes
from scrapy.exceptions import DropItem

# =========================
# Config via environment
# =========================
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

        local_path = None

        # If it's a video -> download directly (avoid FilesPipeline oddities)
        if item.get("type") == "video":
            try:
                local_path = self._download_direct(item["url"], spider, item=item)
            except Exception as e:
                self._record_posted(item, None, None, "failed", str(e))
                raise DropItem(f"Media download failed: {e}")

        else:
            # Try file downloaded by FilesPipeline (images)
            local_path = self._get_local_file(item, spider)

            # Fallback: direct download if FilesPipeline didn't store the file
            if not local_path:
                try:
                    local_path = self._download_direct(item["url"], spider, item=item)
                except Exception as e:
                    self._record_posted(item, None, None, "failed", str(e))
                    raise DropItem(f"Media download failed: {e}")

        # At this point local_path should exist
        if not local_path or not os.path.exists(local_path):
            self._record_posted(item, None, None, "failed", "local file missing after download")
            raise DropItem("Local file missing after download")

        # Upload to Facebook (commented out in original — kept as-is)
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

        # Record success (fb_post_id left None since upload is skipped)
        self._record_posted(item, local_path, None, "success")
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
        """
        Check if FilesPipeline already downloaded the file (images).
        """
        files = item.get("files") or []
        if not files:
            return None

        store = spider.settings.get("FILES_STORE", "media")
        path = os.path.join(store, files[0].get("path"))
        return path if os.path.exists(path) else None

    def _download_direct(self, url, spider, item=None, max_retries=3, timeout=60):
        """
        Robust direct download using requests (streamed).
        Returns local path to the downloaded file.
        """
        if not url:
            raise ValueError("No URL provided for direct download")

        headers = {
            # Use a realistic browser UA to avoid odd blocking
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            # Use permalink as referer if available (helps some hosts)
            "Referer": (item.get("permalink") if item else "https://www.reddit.com/"),
            "Accept": "*/*",
            "Accept-Encoding": "identity",  # avoid gzip/decompression that may break streaming guesses
        }

        attempt = 0
        last_exc = None
        while attempt < max_retries:
            attempt += 1
            try:
                with requests.get(url, stream=True, timeout=timeout, headers=headers, allow_redirects=True) as resp:
                    resp.raise_for_status()

                    # Try to detect extension from Content-Type header
                    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
                    ext = None
                    if content_type:
                        ext = mimetypes.guess_extension(content_type)
                        # handle some common cases
                        if ext == ".jpe":
                            ext = ".jpg"

                    # Fall back to extension from URL path
                    url_path = url.split("?")[0]
                    basename = os.path.basename(url_path)
                    url_ext = os.path.splitext(basename)[1]
                    if not ext and url_ext:
                        ext = url_ext

                    # If still no ext and item indicates video, default to .mp4
                    if not ext:
                        if item and item.get("type") == "video":
                            ext = ".mp4"
                        else:
                            ext = url_ext or ".bin"

                    # Create filename
                    folder = spider.settings.get("FILES_STORE", "media")
                    os.makedirs(folder, exist_ok=True)
                    filename = f"reddit_{int(time.time() * 1000)}{ext}"
                    path = os.path.join(folder, filename)

                    # Stream write
                    chunk_size = 1024 * 64
                    with open(path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=chunk_size):
                            if chunk:
                                f.write(chunk)

                    # Basic sanity check: file size > 1 KB (adjust if needed)
                    if os.path.getsize(path) < 1024:
                        raise Exception(f"Downloaded file too small ({os.path.getsize(path)} bytes)")

                    spider.logger.info(f"Downloaded direct: {url} -> {path} (size={os.path.getsize(path)})")
                    return path

            except Exception as exc:
                last_exc = exc
                spider.logger.warning(f"Direct download attempt {attempt} failed for {url}: {exc}")
                time.sleep(1 + attempt)  # small backoff and retry

        raise Exception(f"Failed to download after {max_retries} attempts: {last_exc}")


    # =========================
    # Make.com upload (videos only)
    # =========================
    def _upload_to_facebook(self, local_path, item):

        caption = item.get("title") or ""

        # Ensure it's a video
        ext = os.path.splitext(local_path)[1].lower()
        is_video = item.get("type") == "video" or ext in (".mp4")
        is_photo = item.get("type") == "preview" or ext in (".jpeg", ".jpg", ".png")

        if not (is_video or is_photo):
            raise Exception("Only video and image uploads are supported")
        
        if is_video:
            MAKE_WEBHOOK_URL = ""

        if is_photo:
            MAKE_WEBHOOK_URL = ""


        payload = {
            "caption": f"{caption}\nPlease like and follow \n https://www.youtube.com/@am_ish \nhttps://discord.gg/Qnp2eF5MaU \nall links on: https://linktr.ee/am_ish",
        }

        with open(local_path, "rb") as f:
            files = {
                "file": (os.path.basename(local_path), f, "application/octet-stream")
            }

            resp = requests.post(
                MAKE_WEBHOOK_URL,
                data=payload,
                files=files,
                timeout=300,
            )

        if not resp.ok:
            raise Exception(f"Make webhook error {resp.status_code}: {resp.text}")
        
        time.sleep(3)

        return resp.text