# settings.py

# Scrapy settings for my_scraper_project project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = 'my_scraper_project'

# Corrected SPIDER_MODULES and NEWSPIDER_MODULE to remove 'src.'
# Assuming my_scraper_project is directly under your application root.
SPIDER_MODULES = ['my_scraper_project.spiders']
NEWSPIDER_MODULE = 'my_scraper_project.spiders'


# Crawl responsibly by identifying yourself (and your website) on the user-agent
# Recommended User-Agent for Playwright-based scraping to mimic a real browser
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Obey robots.txt rules
# Set to False often for dynamic scraping to ensure all content is accessible,
# but use with caution and respect website policies.
ROBOTSTXT_OBEY = False

# Configure maximum concurrent requests performed by Scrapy (default: 16)
# Lower for Playwright as it's more resource-intensive (each request launches a browser).
CONCURRENT_REQUESTS = 8

# Configure a delay for requests for the same website (default: 0)
# See https://docs.scrapy.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs
DOWNLOAD_DELAY = 2 # A polite delay to avoid overwhelming the server
# The download delay setting will honor only one of:
CONCURRENT_REQUESTS_PER_DOMAIN = 4 # Lower concurrency per domain for Playwright
#CONCURRENT_REQUESTS_PER_IP = 16

# Disable cookies (enabled by default)
# Keep cookies enabled for Playwright to manage sessions by default, as a real browser would.
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
TELNETCONSOLE_ENABLED = False

# Override the default request headers:
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en',
    'Accept-Encoding': 'gzip, deflate', # Standard encoding for browser requests
}

# Enable or disable spider middlewares
#SPIDER_MIDDLEWARES = {
#    'src.my_scraper_project.middlewares.MyScraperProjectSpiderMiddleware': 543,
#}

# Enable or disable downloader middlewares
#DOWNLOADER_MIDDLEWARES = {
#    'src.my_scraper_project.middlewares.MyScraperProjectDownloaderMiddleware': 543,
#}

# Enable or disable extensions
EXTENSIONS = {
    'scrapy.extensions.corestats.CoreStats': 500, # Provides basic scraping stats
    'scrapy.extensions.telnet.TelnetConsole': None, # Ensure TelnetConsole is disabled
}

# Configure item pipelines
# CRITICAL: This enables your JsonWriterPipeline to process scraped items.
ITEM_PIPELINES = {
    'scraper.JsonWriterPipeline': 300,
    # IMPORTANT: Double-check this path based on your project structure.
    # 'scraper.py' is at the root level, so 'scraper.JsonWriterPipeline' is correct.
}

# Enable and configure the AutoThrottle extension (disabled by default)
AUTOTHROTTLE_ENABLED = True
# The initial download delay
AUTOTHROTTLE_START_DELAY = 1
# The maximum download delay to be set in case of high latencies
AUTOTHROTTLE_MAX_DELAY = 10
# The average number of requests Scrapy should be sending in parallel to
# each remote server
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# Keep commented out unless you specifically need caching for development/testing
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0
#HTTPCACHE_DIR = 'httpcache'
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = 'scrapy.extensions.httpcache.FilesystemCacheStorage'

# =============================================================================
# Playwright Specific Settings
# CRITICAL: These settings enable Scrapy to use Playwright for rendering pages.
# =============================================================================

# Tell Scrapy to use the Playwright download handler for HTTP/HTTPS requests
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

# Essential: Use the asyncio reactor for Twisted, required by scrapy-playwright
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# Configure the browser type Playwright should use
PLAYWRIGHT_BROWSER_TYPE = "chromium"  # Options: "chromium", "firefox", "webkit"

# Configure launch options for the Playwright browser
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True, # Set to False for debugging (browser UI will show)
    "timeout": 20000, # Launch timeout in milliseconds (20 seconds)
    "args": [
        '--no-sandbox',             # Required for Docker environments (e.g., Railway)
        '--disable-dev-shm-usage',  # Recommended for Docker to prevent /dev/shm issues
        '--disable-gpu',            # Disable GPU hardware acceleration
        '--disable-web-security',   # Sometimes needed for cross-origin issues
        '--disable-features=VizDisplayCompositor' # Can help stability in headless environments
    ]
}

# Increase default Playwright timeouts for page navigation and command execution.
# Dynamic pages with many elements or slow loading times benefit from longer timeouts.
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 180000 # 3 minutes (for page load events) - Increased for consistency with scraper.py
PLAYWRIGHT_DEFAULT_COMMAND_TIMEOUT = 180000    # 3 minutes (for individual Playwright actions like click, wait_for_selector) - Increased for consistency with scraper.py

# =============================================================================
# Logging
# =============================================================================
LOG_LEVEL = 'INFO' # Set to 'DEBUG' for more verbose output during development
