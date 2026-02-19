import json
import html
import scrapy
from scrapy_playwright.page import PageMethod

class RedditMediaItem(scrapy.Item):
    subreddit       = scrapy.Field()
    post_id         = scrapy.Field()
    title           = scrapy.Field()
    permalink       = scrapy.Field()
    type            = scrapy.Field() # image | video | external
    url             = scrapy.Field() # remote url (for FilesPipeline or direct upload)
    media_urls      = scrapy.Field() # for FilesPipeline
    files           = scrapy.Field() # filled by FilesPipeline
    downloaded_path = scrapy.Field()

class RedditMediaSpider(scrapy.Spider):
    name = "reddit_media"
    allowed_domains = ["reddit.com"]

    def __init__(self, subreddit="funny", limit=25, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subreddit = subreddit
        self.limit = int(limit)
        self.video_count = 0
        self.image_count = 0
        self.seen_ids = set()     # avoid duplicate post processing
        self.last_after = None    # guard against repeating pagination

    def start_requests(self):
        url = f"https://www.reddit.com/r/{self.subreddit}/.json?limit={self.limit}&raw_json=1"
        yield scrapy.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                "Accept": "application/json",
                "Referer": "https://www.reddit.com/",
            },
            meta={
                "playwright": True,
                "playwright_include_page": True,
                # wait for networkidle so JS-driven things settle
                "playwright_page_methods": [PageMethod("wait_for_load_state", "networkidle")],
            },
            callback=self.parse
        )

    async def parse(self, response):
        page = response.meta.get("playwright_page")
        data = None

        # Try direct JSON parsing first (sometimes it works)
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            # If not JSON, extract rendered page content
            try:
                text = await page.evaluate(
                    """() => {
                        const pre = document.querySelector('pre');
                        if (pre) return pre.innerText;
                        return document.documentElement.innerText;
                    }"""
                )
                data = json.loads(text)
            except Exception as exc:
                self.logger.error("Couldn't extract JSON from Playwright-rendered page: %s", exc)
                snippet = await page.content()
                self.logger.debug("Page snippet: %s", snippet[:1000])
                await page.close()
                return

        await page.close()

        posts = data.get("data", {}).get("children", [])

        # global stop (use >= to be robust)
        if self.video_count >= 2 and self.image_count >= 3:
            self.logger.info("Reached target counts: videos=%s images=%s; stopping.", self.video_count, self.image_count)
            return

        for post in posts:
            # double-check inside loop
            if self.video_count >= 2 and self.image_count >= 3:
                self.logger.info("Reached target counts inside loop; stopping.")
                return

            p = post.get("data", {})
            post_id = p.get("id")
            if not post_id:
                continue

            # skip duplicates
            if post_id in self.seen_ids:
                continue
            self.seen_ids.add(post_id)

            item = RedditMediaItem()
            item["subreddit"] = self.subreddit
            item["post_id"] = post_id
            item["title"] = p.get("title")
            item["permalink"] = "https://www.reddit.com" + p.get("permalink", "")
            item["type"] = None
            item["url"] = None
            item["media_urls"] = []

            # Reddit-hosted video
            if p.get("is_video"):
                # check limit before incrementing
                if self.video_count >= 2:
                    # we've already got enough videos; skip this
                    continue
                media = p.get("media") or {}
                reddit_video = media.get("reddit_video", {})
                fallback = reddit_video.get("fallback_url")
                if fallback:
                    url = html.unescape(fallback).replace("&amp;", "&")
                    item["type"] = "video"
                    item["url"] = url
                    item["media_urls"] = [url]
                    self.video_count += 1
                    yield item

            # Single image via preview
            elif p.get("preview"):
                # check limit before incrementing
                if self.image_count >= 3:
                    continue
                images = p["preview"].get("images", [])
                if images:
                    src = images[0]["source"]["url"]
                    url = html.unescape(src).replace("&amp;", "&")
                    item["type"] = "image"
                    item["url"] = url
                    item["media_urls"] = [url]
                    self.image_count += 1
                    yield item

        # Pagination guard: stop if we've reached targets
        if self.video_count >= 2 and self.image_count >= 3:
            self.logger.info("Reached target counts after processing posts; stopping pagination.")
            return

        after = data.get("data", {}).get("after")
        if not after:
            self.logger.info("No more pages (after is null); stopping.")
            return

        # If 'after' didn't change from the last request, stop to avoid infinite loops
        if after == self.last_after:
            self.logger.info("Pagination 'after' unchanged (%s). Stopping to avoid loop.", after)
            return
        self.last_after = after

        next_url = f"https://www.reddit.com/r/{self.subreddit}/.json?after={after}&limit={self.limit}&raw_json=1"
        yield scrapy.Request(
            next_url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
                "Referer": "https://www.reddit.com/",
            },
            meta={
                "playwright": True,
                "playwright_include_page": True,
                "playwright_page_methods": [PageMethod("wait_for_load_state", "networkidle")],
            },
            callback=self.parse
        )