BOT_NAME = "reddit_fb_scraper"

SPIDER_MODULES = ["reddit_fb_scraper.spiders"]
NEWSPIDER_MODULE = "reddit_fb_scraper.spiders"

ITEM_PIPELINES = {
    "reddit_fb_scraper.pipelines.DedupeDownloadUploadPipeline": 200,
}

FILES_STORE = "media"
FILES_EXPIRES = 30

DOWNLOAD_DELAY = 3.0
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 3.0
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
CONCURRENT_REQUESTS = 1

RETRY_ENABLED = True
RETRY_TIMES = 3

ROBOTSTXT_OBEY = False

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"