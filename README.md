# Reddit -> Facebook Scraper (Scrapy)

This Scrapy project fetches media (images/videos) from a subreddit JSON feed,
downloads the media, uploads them to a Facebook Page (keeping the post title as caption),
and records posted Reddit IDs in a local SQLite database to avoid duplicates.

## Setup

1. Install dependencies (recommended in a venv):
   ```
   pip install -r requirements.txt
   ```

2. Set environment variables:
   - `FB_PAGE_ID` - your Facebook Page ID
   - `FB_PAGE_ACCESS_TOKEN` - a **Page access token** with publishing permissions
   - (optional) `REDDIT_TO_FB_DB` - path to SQLite DB (default: posted.db)
   - (optional) `FILES_STORE` - where media files are stored (default: media)

   Example (Linux/macOS):
   ```
   export FB_PAGE_ID=1234567890
   export FB_PAGE_ACCESS_TOKEN="EAAX..."
   export FILES_STORE=media
   ```

3. Run the spider:
   ```
   scrapy crawl reddit_media -a subreddit=funny -a limit=25 -o out.json
   ```

## Notes & Caveats
- You must obtain proper Facebook permissions and a Page access token (see Facebook Graph API docs).
- Large videos may require the Graph API resumable upload flow; the included pipeline uses a simple multipart upload suitable for small/medium files.
- Respect Reddit and Facebook rate limits. The project uses DOWNLOAD_DELAY and AutoThrottle settings — tweak as needed.
- Do not commit your FB access token to source control.

## Project structure
- scrapy.cfg
- reddit_fb_scraper/
  - __init__.py
  - items.py
  - pipelines.py
  - settings.py
  - spiders/reddit_media.py