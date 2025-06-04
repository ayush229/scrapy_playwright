# Scrapy settings for my_scraper_project project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = 'my_scraper_project'

SPIDER_MODULES = ['my_scraper_project.my_scraper_project.spiders']
NEWSPIDER_MODULE = 'my_scraper_project.my_scraper_project.spiders'


# Crawl responsibly by identifying yourself (and your website) on the user-agent
#USER_AGENT = 'my_scraper_project (+http://www.yourdomain.com)'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


# Obey robots.txt rules
ROBOTSTXT_OBEY = False # Set to False as Playwright is often used to bypass basic robot.txt

# Configure maximum concurrent requests performed by Scrapy (default: 16)
CONCURRENT_REQUESTS = 16

# Configure a delay for requests for the same website (default: 0)
# See https://docs.scrapy.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs
DOWNLOAD_DELAY = 1 # Be polite, 1 second delay between requests to same domain
# The download delay setting will honor only one of:
CONCURRENT_REQUESTS_PER_DOMAIN = 8
CONCURRENT_REQUESTS_PER_IP = 0

# Disable cookies (enabled by default)
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
#TELNETCONSOLE_ENABLED = False

# Override the default request headers:
#DEFAULT_REQUEST_HEADERS = {
#    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
#    'Accept-Language': 'en',
#}

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    'my_scraper_project.my_scraper_project.pipelines.JsonWriterPipeline': 300,
}

# Configure Playwright settings
DOWNLOAD_HANDLERS = {
    'http': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
    'https': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
}
TWISTED_REACTOR = 'twisted.internet.asyncioreactor.AsyncioSelectorReactor'

PLAYWRIGHT_LAUNCH_OPTIONS = {
    'headless': True,
    'timeout': 20000, # 20 seconds
    # 'args': ['--disable-web-security'] # Example for CORS or other security bypass if needed
}

PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30000 # 30 seconds
PLAYWRIGHT_DEFAULT_COMMAND_TIMEOUT = 30000 # 30 seconds

# Example for Playwright proxy. Uncomment and configure if needed.
# PLAYWRIGHT_PROXY = {
#     'server': 'http://your_proxy_server:port',
#     'username': 'proxy_user',
#     'password': 'proxy_password',
#     'no_proxy': ['localhost', '127.0.0.1'] # Do not proxy these domains
# }

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
DOWNLOADER_MIDDLEWARES = {
    'my_scraper_project.my_scraper_project.middlewares.ProxyMiddleware': 543,
    'my_scraper_project.my_scraper_project.middlewares.CaptchaSolverMiddleware': 544,
    'scrapy_playwright.middleware.ScrapyPlaywrightMiddleware': 725, # Required for Playwright
    # 'scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware': 750, # Standard Scrapy proxy
}

# Set log level
LOG_LEVEL = 'INFO'
# LOG_FILE = 'scrapy.log' # Uncomment to write logs to a file

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
AUTOTHROTTLE_ENABLED = True
# The initial download delay
AUTOTHROTTLE_START_DELAY = 5
# The maximum download delay to be set in case of high latencies
AUTOTHROTTLE_MAX_DELAY = 60
# The average number of requests Scrapy should be sending in parallel to
# each remote server
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
#AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0 # 0 means never expire
#HTTPCACHE_DIR = 'httpcache'
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = 'scrapy.extensions.httpcache.FilesystemCacheStorage'
