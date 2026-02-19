BOT_NAME = "reddit_fb_scraper"

SPIDER_MODULES = ["reddit_fb_scraper.spiders"]
NEWSPIDER_MODULE = "reddit_fb_scraper.spiders"
FILES_URLS_FIELD = "media_urls"
FILES_RESULT_FIELD = "files"

ITEM_PIPELINES = {
    "scrapy.pipelines.files.FilesPipeline": 1,
    "reddit_fb_scraper.pipelines.DedupeDownloadUploadPipeline": 200,
}

FILES_STORE = "media"
FILES_EXPIRES = 30

DOWNLOAD_DELAY = 2.0
AUTOTHROTTLE_ENABLED = True
CONCURRENT_REQUESTS = 1

ROBOTSTXT_OBEY = False


DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {"headless": False}