import asyncio
import random
import re
from html import unescape
from urllib.parse import urljoin

import scrapy
from camoufox.async_api import AsyncCamoufox


class RedditMediaItem(scrapy.Item):
    subreddit       = scrapy.Field()
    post_id         = scrapy.Field()
    title           = scrapy.Field()
    permalink       = scrapy.Field()
    type            = scrapy.Field()
    url             = scrapy.Field()
    media_urls      = scrapy.Field()
    files           = scrapy.Field()
    downloaded_path = scrapy.Field()


POST_RE = re.compile(r"<shreddit-post\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)(?:="([^"]*)")?')
NEXT_PARTIAL_RE = re.compile(
    r'<faceplate-partial[^>]*slot="load-after"[^>]*src="([^"]+)"',
    re.IGNORECASE,
)
VIDEO_MP4_RE = re.compile(r"packaged-media\.redd\.it/[^\"'\s]+/pb/m2-res_\d+p\.mp4[^\"'\s]*")


def parse_post_attrs(tag_html):
    attrs = {}
    inner = tag_html[len("<shreddit-post"):-1].strip()
    for m in ATTR_RE.finditer(inner):
        key, val = m.group(1), m.group(2)
        if key.lower() == "class":
            continue
        attrs[key] = unescape(val) if val is not None else True
    return attrs


class RedditMediaSpider(scrapy.Spider):
    name = "reddit_media"
    custom_settings = {
        "CONCURRENT_REQUESTS": 1,
        "DOWNLOAD_DELAY": 0,
    }

    def __init__(self, subreddit_list=["instant_regret", "funny"], limit=25,
                 min_page_delay=4.0, max_page_delay=9.0,
                 min_post_delay=2.0, max_post_delay=5.0,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(subreddit_list, str):
            subreddit_list = [s.strip() for s in subreddit_list.split(",") if s.strip()]
        self.subreddit_list = subreddit_list
        self.limit = int(limit)
        self.seen_ids = set()

        self.targets = {
            "instant_regret": {"video": 3, "image": 0},
            "funny":          {"video": 0, "image": 6},
        }
        self.counts = {s: {"video": 0, "image": 0} for s in subreddit_list}

        self.min_page_delay = float(min_page_delay)
        self.max_page_delay = float(max_page_delay)
        self.min_post_delay = float(min_post_delay)
        self.max_post_delay = float(max_post_delay)

    def _reached_target(self, subreddit):
        c = self.counts[subreddit]
        t = self.targets.get(subreddit, {"video": 0, "image": 0})
        return c["video"] >= t["video"] and c["image"] >= t["image"]

    async def start(self):
        async with AsyncCamoufox(headless=True, humanize=True, geoip=True) as browser:
            for subreddit in self.subreddit_list:
                if self._reached_target(subreddit):
                    continue
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    async for item in self._crawl_subreddit(page, subreddit):
                        yield item
                finally:
                    await context.close()
                await asyncio.sleep(random.uniform(self.min_page_delay, self.max_page_delay))

    async def start_requests(self):
        return
        yield

    async def _crawl_subreddit(self, page, subreddit):
        url = f"https://www.reddit.com/r/{subreddit}/"
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await self._wait_out_challenge(page)

        html = await page.content()
        pages_fetched = 0
        max_pages = max(4, (self.limit // 20) + 4)

        while True:
            posts = self._extract_posts(html)
            for post in posts:
                if self._reached_target(subreddit):
                    break
                item = await self._build_item(page, post, subreddit)
                if item is not None:
                    yield item
                    await asyncio.sleep(random.uniform(self.min_post_delay, self.max_post_delay))

            if self._reached_target(subreddit):
                self.logger.info("[%s] Targets reached.", subreddit)
                return

            pages_fetched += 1
            if pages_fetched >= max_pages:
                self.logger.info("[%s] Reached max_pages guard (%s).", subreddit, max_pages)
                return

            next_path = self._next_partial_src(html)
            if not next_path:
                self.logger.info("[%s] No more pagination partial found, stopping.", subreddit)
                return

            next_url = urljoin("https://www.reddit.com", unescape(next_path))
            await asyncio.sleep(random.uniform(self.min_page_delay, self.max_page_delay))

            try:
                resp = await page.request.get(
                    next_url,
                    headers={
                        "Accept": "text/vnd.reddit.partial+html, text/html;q=0.9",
                        "x-original-referer": page.url,
                        "Referer": page.url,
                    },
                )
                if resp.status != 200:
                    self.logger.warning("[%s] Pagination fetch failed with %s", subreddit, resp.status)
                    return
                html = await resp.text()
            except Exception as exc:
                self.logger.warning("[%s] Pagination fetch error: %s", subreddit, exc)
                return

    async def _wait_out_challenge(self, page):
        for _ in range(15):
            content = await page.content()
            if "js_challenge" not in page.url and "shreddit-post" in content:
                return
            await asyncio.sleep(1.0)

    def _extract_posts(self, html):
        posts = []
        for tag in POST_RE.findall(html):
            attrs = parse_post_attrs(tag)
            post_id = attrs.get("id")
            if not post_id or post_id in self.seen_ids:
                continue
            posts.append(attrs)
        return posts

    def _next_partial_src(self, html):
        m = NEXT_PARTIAL_RE.search(html)
        return m.group(1) if m else None

    async def _build_item(self, page, attrs, subreddit):
        post_id = attrs["id"]
        post_type = attrs.get("post-type")
        content_href = attrs.get("content-href")
        permalink = attrs.get("permalink")
        title = attrs.get("post-title")

        self.seen_ids.add(post_id)

        if post_type not in ("video", "image") or not content_href:
            return None

        c = self.counts[subreddit]
        t = self.targets.get(subreddit, {"video": 0, "image": 0})

        if post_type == "video" and c["video"] >= t["video"]:
            return None
        if post_type == "image" and c["image"] >= t["image"]:
            return None

        item = RedditMediaItem()
        item["subreddit"] = subreddit
        item["post_id"]   = post_id
        item["title"]     = title
        item["permalink"] = urljoin("https://www.reddit.com", permalink) if permalink else None
        item["type"]      = post_type
        item["media_urls"] = []

        if post_type == "video":
            media_url = await self._resolve_video_url(page, content_href)
            if not media_url:
                self.logger.warning("[%s] Could not resolve video source for %s", subreddit, post_id)
                return None
            item["url"] = media_url
            c["video"] += 1
        else:
            item["url"] = content_href
            item["media_urls"] = [content_href]
            c["image"] += 1

        return item

    async def _resolve_video_url(self, page, post_url):
        found = {}

        def on_response(response):
            if "url" not in found and VIDEO_MP4_RE.search(response.url):
                found["url"] = response.url

        context = page.context
        video_page = await context.new_page()
        video_page.on("response", on_response)
        try:
            await video_page.goto(post_url, wait_until="networkidle", timeout=45000)
            for _ in range(12):
                if "url" in found:
                    break
                await asyncio.sleep(1.0)
        except Exception as exc:
            self.logger.debug("Video resolve error for %s: %s", post_url, exc)
        finally:
            video_page.remove_listener("response", on_response)
            await video_page.close()

        return found.get("url")