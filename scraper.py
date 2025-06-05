# Import install_reactor as the very first Twisted-related import
from scrapy.utils.reactor import install_reactor

# IMPORTANT: Install the reactor immediately when the module is loaded.
# This must happen before any other Twisted components try to auto-select a reactor.
try:
    install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")
except Exception as e:
    # This might happen if another part of the application or environment
    # has already installed a reactor. Log a warning but proceed.
    # On Railway, it's possible some underlying system initializes Twisted.
    # However, this placement is the most robust for manual control.
    print(f"WARNING: Could not install AsyncioSelectorReactor at module load: {e}. "
          "It might already be installed or another reactor is running.")

import requests
from bs4 import BeautifulSoup
import logging
from urllib.parse import urlparse, urljoin
import re
import queue
import threading
from scrapy.crawler import CrawlerRunner
from scrapy.settings import Settings
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import CrawlSpider, Rule
from scrapy.item import Item, Field
from scrapy import Request
from twisted.internet import reactor, defer, threads
from twisted.internet.defer import inlineCallbacks, Deferred


logger = logging.getLogger(__name__)

# This queue will hold results from Scrapy and be read by scraper.py
# It remains global but won't be passed directly into Scrapy settings for pickling.
_scrapy_results_queue = queue.Queue()

# --- Global state for managing the reactor ---
# We'll use a lock to ensure only one thread manages the reactor state
_reactor_lock = threading.Lock()
_reactor_thread = None
_reactor_deferred = None # To hold the deferred that completes when the reactor stops

# --- Pipeline to put items into the queue ---
# Define this pipeline here as it's used directly in this file
class JsonWriterPipeline:
    def process_item(self, item, spider):
        # Access the queue directly from the spider instance
        if hasattr(spider, 'results_queue') and spider.results_queue:
            spider.results_queue.put(dict(item)) # Convert Item to dict
            spider.logger.info(f"Item processed and added to queue: {item.get('url', 'N/A')}")
        else:
            spider.logger.warning(f"Item processed but no queue found on spider to return results: {item.get('url', 'N/A')}")
        return item

# -----------------------------------------------

class ScrapedItem(Item):
    url = Field()
    content = Field()
    raw_data = Field()
    error = Field()

class GenericSpider(CrawlSpider):
    name = 'generic_spider'
    # custom_settings will be overridden by the Settings object passed to CrawlerRunner
    # but it's good practice to keep common settings here for readability if needed
    # (though in our setup, settings are built dynamically in _run_scrapy_spider_async)

    rules = (
        # LinkExtractor will be updated dynamically in __init__
        Rule(LinkExtractor(deny_domains=['google.com', 'facebook.com', 'twitter.com']), callback='parse_item', follow=True),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scrape_mode = kwargs.get('scrape_mode', 'beautify')
        self.user_query = kwargs.get('user_query', '')
        self.domain = kwargs.get('domain', '')
        self.proxy_enabled = kwargs.get('proxy_enabled', False)
        self.captcha_solver_enabled = kwargs.get('captcha_solver_enabled', False)
        
        # The results queue will be passed directly to the spider instance
        # It's important that this is not part of the 'settings' object itself.
        self.results_queue = kwargs.get('results_queue', None)

        # Adjust allowed_domains dynamically based on start_urls
        if self.start_urls:
            parsed_start_url = urlparse(self.start_urls[0])
            self.domain = parsed_start_url.netloc
            self.allowed_domains = [self.domain] if self.domain else []
            self.rules = (
                Rule(LinkExtractor(allow_domains=self.allowed_domains, deny_domains=['google.com', 'facebook.com', 'twitter.com', 'linkedin.com']), callback='parse_item', follow=True),
            )

        # These settings are now largely handled by the settings object passed to CrawlerRunner
        # but you can use them here for spider-specific overrides if necessary.
        # Ensure your custom_settings are merged/applied correctly.
        # The key is that `SCRAPY_RESULTS_QUEUE` is set in the `Settings` object passed to `CrawlerRunner'.
    def start_requests(self):
        for url in self.start_urls:
            yield Request(url, meta={'playwright': True}) # Request will be handled by Playwright

    def parse_item(self, response):
        item = ScrapedItem()
        item['url'] = response.url
        item['error'] = None

        try:
            if self.scrape_mode == 'raw':
                item['raw_data'] = response.text
                yield item
                return

            # Beautify mode
            soup = BeautifulSoup(response.text, 'html.parser')
            content_sections = []

            sections = soup.find_all(['section', 'div', 'article', 'main', 'body'])
            if not sections and soup.body:
                sections = [soup.body]
            elif not sections:
                sections = [soup]

            for sec in sections:
                section_data = {
                    "heading": None,
                    "paragraphs": [],
                    "images": [],
                    "links": []
                }

                heading_tags = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']
                found_heading = None
                for tag_name in heading_tags:
                    heading = sec.find(tag_name)
                    if heading and heading.get_text(strip=True):
                        found_heading = { "tag": heading.name, "text": heading.get_text(strip=True) }
                        break
                section_data["heading"] = found_heading

                paragraphs = sec.find_all(['p', 'li', 'span', 'div'])
                for p in paragraphs:
                    text = p.get_text(strip=True)
                    if text and len(text) > 5:
                        section_data["paragraphs"].append(text)

                for img in sec.find_all("img"):
                    src = img.get("src")
                    if src:
                        abs_url = urljoin(response.url, src)
                        section_data["images"].append(abs_url)

                for a in sec.find_all("a"):
                    href = a.get("href")
                    if href:
                        abs_href = urljoin(response.url, href)
                        section_data["links"].append(abs_href)

                if section_data["heading"] or section_data["paragraphs"] or section_data["images"] or section_data["links"]:
                    content_sections.append(section_data)

            # Final fallback
            if not content_sections and soup.get_text(strip=True):
                content_sections.append({
                    "heading": None,
                    "paragraphs": [soup.get_text(separator=' ', strip=True)],
                    "images": [],
                    "links": []
                })

            item['content'] = { "sections": content_sections }
            yield item

        except Exception as e:
            logger.error(f"Error parsing HTML for {response.url}: {e}", exc_info=True)
            item['error'] = str(e)
            yield item

# --- Async Scrapy Runner within Twisted's reactor ---
@inlineCallbacks
def _execute_scrapy_crawl(start_urls, scrape_mode, user_query, proxy_enabled, captcha_solver_enabled):
    """
    Executes a single Scrapy crawl within the Twisted reactor.
    This function should be called within the reactor's thread.
    """
    logger.info(f"Executing Scrapy crawl for {start_urls} in reactor thread.")
    try:
        # Create a new Settings object for each run
        settings = Settings()

        # Base settings
        base_settings = {
            'BOT_NAME': 'travel_scraper',
            'ROBOTSTXT_OBEY': False,
            'USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'DOWNLOAD_DELAY': 2,
            'RANDOMIZE_DOWNLOAD_DELAY': True,
            'CONCURRENT_REQUESTS': 8,
            'CONCURRENT_REQUESTS_PER_DOMAIN': 4,
            'AUTOTHROTTLE_ENABLED': True,
            'AUTOTHROTTLE_START_DELAY': 1,
            'AUTOTHROTTLE_MAX_DELAY': 10,
            'AUTOTHROTTLE_TARGET_CONCURRENCY': 1.0,
            'DOWNLOAD_TIMEOUT': 60,
            'RETRY_TIMES': 2,
            'LOG_LEVEL': 'INFO',

            # Disable problematic components for Railway/headless
            'TELNETCONSOLE_ENABLED': False,
            'STATS_CLASS': 'scrapy.statscollectors.MemoryStatsCollector',

            # Playwright settings
            'DOWNLOAD_HANDLERS': {
                'http': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
                'https': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
            },
            # This TWISTED_REACTOR setting should now match the one installed at module level
            'TWISTED_REACTOR': 'twisted.internet.asyncioreactor.AsyncioSelectorReactor',
            'PLAYWRIGHT_LAUNCH_OPTIONS': {
                'headless': True, # CRUCIAL for server environments
                'timeout': 20000,
                'args': [
                    '--no-sandbox', # Required for Docker environments
                    '--disable-dev-shm-usage', # Recommended for Docker
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor'
                ]
            },
            'PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT': 30000,
            'PLAYWRIGHT_DEFAULT_COMMAND_TIMEOUT': 30000,
            'PLAYWRIGHT_BROWSER_TYPE': 'chromium', # or 'firefox', 'webkit'

            # Pipeline settings:
            # We'll refer to the JsonWriterPipeline defined in THIS file ('scraper.py')
            'ITEM_PIPELINES': {
                'scraper.JsonWriterPipeline': 300,
            },

            'FEEDS': {},

            # Headers for better compatibility
            'DEFAULT_REQUEST_HEADERS': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en',
                'Accept-Encoding': 'gzip, deflate',
            },

            # Simple extensions only
            'EXTENSIONS': {
                'scrapy.extensions.corestats.CoreStats': 500,
            },
        }

        domain = urlparse(start_urls[0]).netloc if start_urls else ''
        if domain:
            base_settings['ALLOWED_DOMAINS'] = [domain]

        if scrape_mode == 'beautify':
            base_settings['PLAYWRIGHT_PROCESS_REQUEST_HEADERS'] = None
            base_settings['PLAYWRIGHT_PROCESS_RESPONSE_HEADERS'] = None

        if proxy_enabled:
            logger.info("Proxy enabled. Ensure 'PLAYWRIGHT_PROXY' is configured in Scrapy settings.")
            # Example proxy config if needed:
            # base_settings['PLAYWRIGHT_PROXY'] = {
            #    'server': 'http://your_proxy_server:port',
            #    'username': 'proxy_user',
            #    'password': 'proxy_password',
            # }

        settings.setdict(base_settings)
        # Configure logging for Scrapy specifically
        from scrapy.utils.log import configure_logging
        configure_logging(settings)


        # Instantiate CrawlerRunner with the dynamically created settings
        runner = CrawlerRunner(settings)
        spider_kwargs = {
            'start_urls': start_urls,
            'scrape_mode': scrape_mode,
            'user_query': user_query,
            'domain': domain,
            'proxy_enabled': proxy_enabled,
            'captcha_solver_enabled': captcha_solver_enabled,
            'results_queue': _scrapy_results_queue # Pass the queue directly to the spider
        }

        # Clear the queue before a new run
        while not _scrapy_results_queue.empty():
            _scrapy_results_queue.get_nowait()

        # Yield the Deferred from runner.crawl
        yield runner.crawl(GenericSpider, **spider_kwargs)
        logger.info(f"Scrapy crawl for {start_urls} finished successfully.")

    except Exception as e:
        logger.error(f"Error during Scrapy crawl execution: {e}", exc_info=True)
        # Propagate the error so the calling Deferred can handle it
        raise # Re-raise the exception after logging

# --- Reactor Management Thread ---
def _start_reactor_thread():
    """Starts the Twisted reactor in a dedicated thread."""
    global _reactor_thread, _reactor_deferred

    with _reactor_lock:
        if _reactor_thread is None or not _reactor_thread.is_alive():
            # The install_reactor call is now at the module level, so no need to call it here.
            # Create a Deferred that will fire when the reactor stops
            _reactor_deferred = Deferred()
            _reactor_thread = threading.Thread(target=_reactor_loop, daemon=True)
            _reactor_thread.start()
            logger.info("Twisted reactor started in a separate thread.")
        else:
            logger.info("Twisted reactor thread already running.")

def _reactor_loop():
    """The main loop for the Twisted reactor, to be run in a separate thread."""
    global _reactor_deferred
    try:
        reactor.run(installSignalHandlers=False) # Do not install signal handlers in a sub-thread
    except Exception as e:
        logger.error(f"Error in reactor thread: {e}", exc_info=True)
    finally:
        # Fire the deferred when the reactor stops (either cleanly or due to error)
        if _reactor_deferred:
            # Check if deferred is not already fired to avoid a RuntimeError
            if not _reactor_deferred.called:
                _reactor_deferred.callback(None) # Signal completion

# --- Public API for scraping ---
def scrape_website(url, type="beautify", proxy_enabled=False, captcha_solver_enabled=False):
    """
    Scrapes a single website using Scrapy/Playwright in a non-blocking manner.
    Args:
        url (str): The URL to scrape.
        type (str): 'raw' for raw HTML, 'beautify' for structured content.
        proxy_enabled (bool): Whether to use proxies.
        captcha_solver_enabled (bool): Whether to enable captcha solving.
    Returns:
        dict: A dictionary containing:
            - "status": "success" or "error".
            - "url": The URL that was scraped.
            - "type": The type of scrape performed.
            - "data": (If status is "success") The scraped content.
            - "error": (Only present if status is "error") A string describing the error.
    """
    logger.info(f"Initiating scrape_website call for {url} (type: {type})")
    _start_reactor_thread() # Ensure reactor thread is running

    # Create a Deferred that will represent the completion of this specific crawl.
    d = Deferred()
    
    # Schedule _execute_scrapy_crawl to run in the reactor's thread.
    # _execute_scrapy_crawl returns a Deferred (because it's an @inlineCallbacks function).
    # We chain that Deferred's outcome to our 'd' Deferred.
    reactor.callFromThread(lambda: _execute_scrapy_crawl(
        start_urls=[url],
        scrape_mode=type,
        user_query="", # Not applicable for single scrape_website
        proxy_enabled=proxy_enabled,
        captcha_solver_enabled=captcha_solver_enabled
    ).chainDeferred(d)) # CRUCIAL FIX: Chain the inner Deferred to 'd'

    # Wait for the Deferred to complete.
    completion_event = threading.Event()
    error_container = [None] # Use a list to allow modification in inner scope

    def _on_crawl_complete(result):
        completion_event.set()
        return result

    def _on_crawl_error(failure):
        error_container[0] = failure.getErrorMessage()
        logger.error(f"Scrapy crawl failed: {failure.getErrorMessage()}", exc_info=True)
        completion_event.set()
        return failure # Re-raise for further handling if needed

    d.addCallback(_on_crawl_complete)
    d.addErrback(_on_crawl_error)

    try:
        # Wait for the crawl to complete
        completion_event.wait(timeout=120) # Max 120 seconds to wait for scrape

        if not completion_event.is_set():
            logger.error(f"Scrapy crawl for {url} timed out after 120 seconds.")
            return {"status": "error", "url": url, "type": type, "error": "Scraping operation timed out."}

        if error_container[0]:
            return {"status": "error", "url": url, "type": type, "error": error_container[0]}

    except Exception as e:
        logger.error(f"Unhandled error during scrape_website execution: {e}", exc_info=True)
        return {"status": "error", "url": url, "type": type, "error": str(e)}

    # Collect results after the crawl is confirmed complete
    results = []
    while not _scrapy_results_queue.empty():
        try:
            results.append(_scrapy_results_queue.get_nowait())
        except queue.Empty:
            break # Should not happen if completion_event is set after results are put

    if results:
        first_item = results[0]
        if first_item.get('error'):
            return {"status": "error", "url": url, "type": type, "error": first_item['error']}
        elif type == 'raw':
            return {"status": "success", "url": url, "type": type, "data": first_item.get('raw_data')}
        else: # beautify
            return {"status": "success", "url": url, "type": type, "data": first_item.get('content')}
    else:
        return {"status": "error", "url": url, "type": type, "error": "No data scraped or unknown error. Check Scrapy logs for details."}


def crawl_website(base_url, type="beautify", user_query="", proxy_enabled=False, captcha_solver_enabled=False):
    """
    Crawl multiple pages from a website using Scrapy/Playwright.
    This function has similar blocking characteristics to scrape_website.
    """
    logger.info(f"Initiating crawl_website call for {base_url} (type: {type})")
    _start_reactor_thread() # Ensure reactor thread is running

    d = Deferred()
    reactor.callFromThread(lambda: _execute_scrapy_crawl(
        start_urls=[base_url],
        scrape_mode=type,
        user_query=user_query,
        proxy_enabled=proxy_enabled,
        captcha_solver_enabled=captcha_solver_enabled
    ).chainDeferred(d)) # CRUCIAL FIX: Chain the inner Deferred to 'd'

    completion_event = threading.Event()
    error_container = [None]

    def _on_crawl_complete(result):
        completion_event.set()
        return result

    def _on_crawl_error(failure):
        error_container[0] = failure.getErrorMessage()
        logger.error(f"Scrapy crawl failed: {failure.getErrorMessage()}", exc_info=True)
        completion_event.set()
        return failure

    d.addCallback(_on_crawl_complete)
    d.addErrback(_on_crawl_error)

    try:
        completion_event.wait(timeout=300) # Max 300 seconds for a crawl

        if not completion_event.is_set():
            logger.error(f"Scrapy crawl for {base_url} timed out after 300 seconds.")
            return {"status": "error", "url": base_url, "type": type, "error": "Crawling operation timed out."}

        if error_container[0]:
            return {"status": "error", "url": base_url, "type": type, "error": error_container[0]}

    except Exception as e:
        logger.error(f"Unhandled error during crawl_website execution: {e}", exc_info=True)
        return {"status": "error", "url": base_url, "type": type, "error": str(e)}

    all_data = []
    while not _scrapy_results_queue.empty():
        try:
            item = _scrapy_results_queue.get_nowait()
            page_data = {"url": item.get('url')}
            if item.get('error'):
                page_data["error"] = item['error']
            elif type == 'raw':
                page_data["raw_data"] = item.get('raw_data')
            else: # beautify
                page_data["content"] = item.get('content', {}).get('sections', [])
            all_data.append(page_data)
        except queue.Empty:
            break

    return all_data
