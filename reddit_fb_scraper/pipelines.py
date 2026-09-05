import os
import sqlite3
import time
import random
import requests
import mimetypes
from scrapy.exceptions import DropItem

DB_PATH = os.environ.get("REDDIT_TO_FB_DB", "posted.db")
UPLOAD_MIN_DELAY = float(os.environ.get("UPLOAD_MIN_DELAY", "3.0"))
UPLOAD_MAX_DELAY = float(os.environ.get("UPLOAD_MAX_DELAY", "8.0"))

USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:155.0) Gecko/20100101 Firefox/155.0"


class DedupeDownloadUploadPipeline:
    """
    Pipeline that:
    - Prevents duplicate Reddit posts
    - Stores all metadata locally (SQLite)
    - Downloads media directly (no FilesPipeline dependency)
    - Uploads media via Make.com webhook
    - Tracks success / failure
    """

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

    def process_item(self, item, spider):
        post_id = item.get("post_id")

        if item.get("type") not in ("image", "video"):
            raise DropItem("Unsupported Reddit post type")

        if self._is_posted(post_id):
            raise DropItem(f"Already processed: {post_id}")

        try:
            local_path = self._download_direct(item["url"], spider, item=item)
        except Exception as e:
            self._record_posted(item, None, None, "failed", str(e))
            raise DropItem(f"Media download failed: {e}")

        if not local_path or not os.path.exists(local_path):
            self._record_posted(item, None, None, "failed", "local file missing after download")
            raise DropItem("Local file missing after download")

        try:
            fb_post_id = self._upload_to_facebook(local_path, item)
            self._record_posted(item, local_path, fb_post_id, "success")
        except Exception as e:
            spider.logger.error(f"Facebook upload failed: {e}")
            self._record_posted(item, local_path, None, "failed", str(e))
            raise DropItem("FB upload failed")

        delay = random.uniform(UPLOAD_MIN_DELAY, UPLOAD_MAX_DELAY)
        spider.logger.info(f"Sleeping {delay:.1f}s to avoid rate limits")
        time.sleep(delay)

        return item

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

    def _referer_for(self, item):
        if item.get("type") == "video":
            return "https://v.redd.it/"
        return "https://www.reddit.com/"

    def _download_direct(self, url, spider, item=None, max_retries=4, timeout=60):
        if not url:
            raise ValueError("No URL provided for direct download")

        headers = {
            "User-Agent": USER_AGENT,
            "Referer": self._referer_for(item) if item else "https://www.reddit.com/",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }

        attempt = 0
        last_exc = None
        while attempt < max_retries:
            attempt += 1
            try:
                with requests.get(url, stream=True, timeout=timeout, headers=headers, allow_redirects=True) as resp:
                    resp.raise_for_status()

                    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
                    ext = None
                    if content_type:
                        ext = mimetypes.guess_extension(content_type)
                        if ext == ".jpe":
                            ext = ".jpg"

                    url_path = url.split("?")[0]
                    basename = os.path.basename(url_path)
                    url_ext = os.path.splitext(basename)[1]
                    if not ext and url_ext:
                        ext = url_ext

                    if not ext:
                        ext = ".mp4" if (item and item.get("type") == "video") else (url_ext or ".bin")

                    folder = spider.settings.get("FILES_STORE", "media")
                    os.makedirs(folder, exist_ok=True)
                    filename = f"reddit_{int(time.time() * 1000)}{ext}"
                    path = os.path.join(folder, filename)

                    chunk_size = 1024 * 64
                    with open(path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=chunk_size):
                            if chunk:
                                f.write(chunk)

                    if os.path.getsize(path) < 1024:
                        raise Exception(f"Downloaded file too small ({os.path.getsize(path)} bytes)")

                    spider.logger.info(f"Downloaded direct: {url} -> {path} (size={os.path.getsize(path)})")
                    return path

            except Exception as exc:
                last_exc = exc
                spider.logger.warning(f"Direct download attempt {attempt} failed for {url}: {exc}")
                time.sleep((2 ** attempt) + random.uniform(0, 1.5))

        raise Exception(f"Failed to download after {max_retries} attempts: {last_exc}")

    def _upload_to_facebook(self, local_path, item):
        caption = item.get("title") or ""

        ext = os.path.splitext(local_path)[1].lower()
        is_video = item.get("type") == "video" or ext == ".mp4"
        is_photo = item.get("type") == "image" or ext in (".jpeg", ".jpg", ".png")

        if not (is_video or is_photo):
            raise Exception("Only video and image uploads are supported")

        if is_video:
            MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/111"
        else:
            MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/222"

        payload = {
            "caption": f"{caption}\nPlease like and follow \nhttps://www.youtube.com/@am_ish \nhttps://discord.gg/Qnp2eF5MaU \nall links on: https://linktr.ee/am_ish",
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