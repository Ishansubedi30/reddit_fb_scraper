import scrapy
import html
from ..items import RedditMediaItem

class RedditMediaSpider(scrapy.Spider):
    name = "reddit_media"
    allowed_domains = ["reddit.com"]

    def __init__(self, subreddit="funny", limit=25, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subreddit = subreddit
        self.limit = int(limit)

    def start_requests(self):
        url = f"https://www.reddit.com/r/{self.subreddit}/.json?limit={self.limit}&raw_json=1"
        yield scrapy.Request(
            url,
            headers={"User-Agent": "scrapy:reddit-media-scraper:v1.0 (by u/yourusername)"},
            callback=self.parse
        )

    def parse(self, response):
        data = response.json()
        posts = data.get("data", {}).get("children", [])

        for post in posts:
            p = post.get("data", {})
            item = RedditMediaItem()
            item["subreddit"] = self.subreddit
            item["post_id"] = p.get("id")
            item["title"] = p.get("title")
            item["permalink"] = "https://www.reddit.com" + p.get("permalink", "")
            item["type"] = None
            item["url"] = None
            item["media_urls"] = []

            # gallery
            if p.get("is_gallery"):
                media_meta = p.get("media_metadata", {})
                gallery_items = p.get("gallery_data", {}).get("items", [])
                for gi in gallery_items:
                    mid = gi.get("media_id")
                    meta = media_meta.get(mid, {})
                    src = meta.get("s", {}).get("u")
                    if src:
                        url = html.unescape(src).replace("&amp;", "&")
                        it = dict(item)
                        it["type"] = "image"
                        it["url"] = url
                        it["media_urls"] = [url]
                        yield it

            # reddit hosted video
            elif p.get("is_video"):
                media = p.get("media") or {}
                reddit_video = media.get("reddit_video", {})
                fallback = reddit_video.get("fallback_url")
                if fallback:
                    url = html.unescape(fallback).replace("&amp;", "&")
                    item["type"] = "video"
                    item["url"] = url
                    item["media_urls"] = [url]
                    yield item

            # single image via preview
            elif p.get("preview"):
                images = p["preview"].get("images", [])
                if images:
                    src = images[0]["source"]["url"]
                    url = html.unescape(src).replace("&amp;", "&")
                    item["type"] = "image"
                    item["url"] = url
                    item["media_urls"] = [url]
                    yield item

            # external links
            else:
                url = p.get("url_overridden_by_dest") or p.get("url")
                if url:
                    url = html.unescape(url)
                    item["type"] = "external"
                    item["url"] = url
                    item["media_urls"] = [url]
                    yield item

        # paginate
        after = data.get("data", {}).get("after")
        if after:
            next_url = f"https://www.reddit.com/r/{self.subreddit}/.json?after={after}&limit={self.limit}&raw_json=1"
            yield scrapy.Request(next_url,
                                 headers={"User-Agent": "scrapy:reddit-media-scraper:v1.0 (by u/yourusername)"},
                                 callback=self.parse)