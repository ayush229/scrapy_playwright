# Scrapy settings for my_scraper_project project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = 'my_scraper_project'

SPIDER_MODULES = ['src.my_scraper_project.spiders']
NEWSPIDER_MODULE = 'src.my_scraper_project.spiders'


# Crawl responsibly by identifying yourself (and your website) on the user-agent
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36' # Recommended for Playwright

# Obey robots.txt rules - Set to False if you need to ignore it for specific sites
ROBOTSTXT_OBEY = False # Often set to False when using Playwright for aggressive scraping

# Configure maximum concurrent requests performed by Scrapy (default: 16)
CONCURRENT_REQUESTS = 8 # Lower for Playwright as it's resource-intensive

# Configure a delay for requests for the same website (default: 0)
# See https://docs.scrapy.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs
DOWNLOAD_DELAY = 2 # Add a delay to be polite and avoid blocks
# The download delay setting will honor only one of:
CONCURRENT_REQUESTS_PER_DOMAIN = 4 # Lower for Playwright
#CONCURRENT_REQUESTS_PER_IP = 16 # Keep commented unless you're rotating IPs heavily

# Disable cookies (enabled by default)
# COOKIES_ENABLED = False # Keep enabled for Playwright to manage sessions by default

# Disable Telnet Console (enabled by default)
TELNETCONSOLE_ENABLED = False # Disable for server environments

# Override the default request headers:
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en',
    'Accept-Encoding': 'gzip, deflate', # Add encoding
}

# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
#SPIDER_MIDDLEWARES = {
#    'src.my_scraper_project.middlewares.MyScraperProjectSpiderMiddleware': 543,
#}

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
DOWNLOADER_MIDDLEWARES = {
#    'src.my_scraper_project.middlewares.MyScraperProjectDownloaderMiddleware': 543,
}

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
EXTENSIONS = {
    'scrapy.extensions.corestats.CoreStats': 500, # Only enable core stats for simplicity
    'scrapy.extensions.telnet.TelnetConsole': None, # Ensure TelnetConsole is disabled
}

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    'scraper.JsonWriterPipeline': 300, # IMPORTANT: Correctly reference your pipeline from 'scraper.py'
                                        # Assuming 'scraper.py' is at the project root or in the same path where Scrapy can find it.
                                        # If 'scraper.py' is in a subfolder like 'src/my_scraper_project',
                                        # you might need 'src.my_scraper_project.scraper.JsonWriterPipeline'
}

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
AUTOTHROTTLE_ENABLED = True
# The initial download delay
AUTOTHROTTLE_START_DELAY = 1 # Start with a shorter delay
# The maximum download delay to be set in case of high latencies
AUTOTHROTTLE_MAX_DELAY = 10 # Adjust as needed
# The average number of requests Scrapy should be sending in parallel to
# each remote server
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
AUTOTHROTTLE_DEBUG = False # Set to True for debugging throttling

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
# HTTPCACHE_ENABLED = True # Keep commented out unless you specifically need caching for development/testing
# HTTPCACHE_EXPIRATION_SECS = 0
# HTTPCACHE_DIR = 'httpcache'
# HTTPCACHE_IGNORE_HTTP_CODES = []
# HTTPCACHE_STORAGE = 'scrapy.extensions.httpcache.FilesystemCacheStorage'

# Set settings for Playwright if you are using it (example)
# UNCOMMENT AND CONFIGURE THESE FOR PLAYWRIGHT
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor" # Essential for Playwright

PLAYWRIGHT_BROWSER_TYPE = "chromium"  # or "firefox", "webkit"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True, # CRUCIAL for server environments and often for performance
    "timeout": 20000, # Playwright launch timeout (milliseconds)
    "args": [
        '--no-sandbox', # Required for Docker environments like Railway
        '--disable-dev-shm-usage', # Recommended for Docker
        '--disable-gpu', # Disable GPU hardware acceleration
        '--disable-web-security', # Sometimes needed for cross-origin issues
        '--disable-features=VizDisplayCompositor' # Can help in headless environments
    ]
}

# Increased Playwright timeouts for longer navigation and command execution
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 120000 # 2 minutes
PLAYWRIGHT_DEFAULT_COMMAND_TIMEOUT = 120000    # 2 minutes

# Add any custom unpicklable objects here by ensuring they are not initialized
# directly, but rather configured to be initialized on demand within spiders/middlewares/pipelines.
# For example, if you had a logging object that caused the TypeError, ensure it's removed from here
# and initialized locally where it's needed (e.g., in a spider's __init__ method).

# Log Level (useful for debugging)
LOG_LEVEL = 'INFO'
