import json
import html
import scrapy
from scrapy_playwright.page import PageMethod

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

class RedditMediaSpider(scrapy.Spider):
    name = "reddit_media"
    allowed_domains = [
        "reddit.com", "www.reddit.com",
        "v.redd.it", "i.redd.it", "preview.redd.it"
    ]

    def __init__(self, subreddit_list=["instant_regret", "funny"], limit=25, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit = int(limit)
        self.seen_ids = set()

        # Per-subreddit targets — no more shared scalars
        self.targets = {
            "instant_regret": {"video": 3, "image": 0},
            "funny":          {"video": 0, "image": 6},
        }
        # Per-subreddit counters and pagination guard
        self.counts     = {s: {"video": 0, "image": 0} for s in subreddit_list}
        self.last_after = {s: None for s in subreddit_list}

        self.subreddit_list = subreddit_list

    def _reached_target(self, subreddit):
        c = self.counts[subreddit]
        t = self.targets.get(subreddit, {"video": 0, "image": 0})
        return c["video"] >= t["video"] and c["image"] >= t["image"]

    def start_requests(self):
        for subreddit in self.subreddit_list:
            url = f"https://www.reddit.com/r/{subreddit}/.json?limit={self.limit}&raw_json=1"
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
                    "playwright_page_methods": [PageMethod("wait_for_load_state", "networkidle")],
                    "subreddit": subreddit,   # ← carry identity through the request
                },
                callback=self.parse,
            )

    async def parse(self, response):
        subreddit = response.meta["subreddit"]   # ← read from meta, not self
        page = response.meta.get("playwright_page")
        data = None

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
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
                self.logger.error("Couldn't extract JSON: %s", exc)
                snippet = await page.content()
                self.logger.debug("Page snippet: %s", snippet[:1000])
                await page.close()
                return

        await page.close()

        if self._reached_target(subreddit):
            self.logger.info("[%s] Already at target; skipping page.", subreddit)
            return

        posts = data.get("data", {}).get("children", [])
        t = self.targets.get(subreddit, {"video": 0, "image": 0})
        c = self.counts[subreddit]

        for post in posts:
            if self._reached_target(subreddit):
                self.logger.info("[%s] Reached targets inside loop; stopping.", subreddit)
                return

            p = post.get("data", {})
            post_id = p.get("id")
            if not post_id or post_id in self.seen_ids:
                continue
            self.seen_ids.add(post_id)

            item = RedditMediaItem()
            item["subreddit"]  = subreddit
            item["post_id"]    = post_id
            item["title"]      = p.get("title")
            item["permalink"]  = "https://www.reddit.com" + p.get("permalink", "")
            item["type"]       = None
            item["url"]        = None
            item["media_urls"] = []

            if p.get("is_video"):
                if c["video"] >= t["video"]:
                    continue
                reddit_video = (p.get("media") or {}).get("reddit_video", {})
                fallback = reddit_video.get("fallback_url")
                if fallback:
                    url = html.unescape(fallback).replace("&amp;", "&")
                    item["type"]       = "video"
                    item["url"]        = url
                    item["media_urls"] = []
                    c["video"] += 1
                    yield item

            elif p.get("preview"):
                if c["image"] >= t["image"]:
                    continue
                images = p["preview"].get("images", [])
                if images:
                    src = images[0]["source"]["url"]
                    url = html.unescape(src).replace("&amp;", "&")
                    item["type"]       = "image"
                    item["url"]        = url
                    item["media_urls"] = [url]
                    c["image"] += 1
                    yield item

        if self._reached_target(subreddit):
            self.logger.info("[%s] Reached targets after processing; stopping pagination.", subreddit)
            return

        after = data.get("data", {}).get("after")
        if not after:
            self.logger.info("[%s] No more pages.", subreddit)
            return

        if after == self.last_after[subreddit]:
            self.logger.info("[%s] 'after' unchanged (%s); stopping.", subreddit, after)
            return
        self.last_after[subreddit] = after

        next_url = f"https://www.reddit.com/r/{subreddit}/.json?after={after}&limit={self.limit}&raw_json=1"
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
                "subreddit": subreddit,   # ← always forward it
            },
            callback=self.parse,
        )