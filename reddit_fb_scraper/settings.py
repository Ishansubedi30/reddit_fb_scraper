BOT_NAME = "reddit_fb_scraper"

SPIDER_MODULES = ["reddit_fb_scraper.spiders"]
NEWSPIDER_MODULE = "reddit_fb_scraper.spiders"

ITEM_PIPELINES = {
    "scrapy.pipelines.files.FilesPipeline": 1,
    "reddit_fb_scraper.pipelines.DedupeDownloadUploadPipeline": 200,
}

FILES_STORE = "media"
FILES_EXPIRES = 30

DOWNLOAD_DELAY = 2.0
AUTOTHROTTLE_ENABLED = True
CONCURRENT_REQUESTS = 1