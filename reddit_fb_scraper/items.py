import scrapy

class RedditMediaItem(scrapy.Item):
    subreddit = scrapy.Field()
    post_id = scrapy.Field()
    title = scrapy.Field()
    permalink = scrapy.Field()
    type = scrapy.Field()      # image | video | external
    url = scrapy.Field()       # remote url (for FilesPipeline or direct upload)
    media_urls = scrapy.Field()# for FilesPipeline
    files = scrapy.Field()     # filled by FilesPipeline
    downloaded_path = scrapy.Field()  # local path after download