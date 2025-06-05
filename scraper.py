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
from scrapy import Spider # Changed from CrawlSpider
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

class GenericSpider(Spider): # Changed from CrawlSpider
    name = 'generic_spider'
    # custom_settings will be overridden by the Settings object passed to CrawlerRunner
    # but it's good practice to keep common settings here for readability if needed
    # (though in our setup, settings are built dynamically in _run_scrapy_spider_async)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scrape_mode = kwargs.get('scrape_mode', 'beautify')
        self.user_query = kwargs.get('user_query', '')
        self.domain = kwargs.get('domain', '')
        self.proxy_enabled = kwargs.get('proxy_enabled', False)
        self.captcha_solver_enabled = kwargs.get('captcha_solver_enabled', False)
        
        self.results_queue = kwargs.get('results_queue', None)

        if self.start_urls:
            parsed_start_url = urlparse(self.start_urls[0])
            self.domain = parsed_start_url.netloc
            self.allowed_domains = [self.domain] if self.domain else []

    def start_requests(self):
        for url in self.start_urls:
            # Added 'wait_until': 'networkidle' to meta for Playwright
            yield Request(url, meta={'playwright': {'wait_until': 'networkidle'}}, callback=self.parse_item)

    async def parse_item(self, response):
        item = ScrapedItem()
        item['url'] = response.url
        item['error'] = None

        if self.scrape_mode == 'raw':
            item['raw_data'] = response.text
            yield item
            return

        try:
            # Access the Playwright page object
            page = response.playwright_page
            
            # Use Playwright to get visible text content, ensuring it's loaded
            
            # Try to get the "LIVE with Code GODARSHAN" section
            live_with_text = None
            try:
                # Assuming this is in a specific div or span near "LIVE with" text
                live_with_locator = page.locator('div:has-text("LIVE with") span:has-text("Code")') # More specific locator
                await live_with_locator.wait_for(state='visible', timeout=5000)
                live_with_text = await live_with_locator.inner_text()
            except Exception as e:
                self.logger.warning(f"Could not find 'LIVE with Code' element: {e}")

            # Try to get "Pick your next destination from these sacred sites"
            next_destination_text = None
            try:
                next_destination_locator = page.locator('h2:has-text("Pick your NEXT DESTINATION from these sacred sites")')
                await next_destination_locator.wait_for(state='visible', timeout=5000)
                next_destination_text = await next_destination_locator.inner_text()
            except Exception as e:
                self.logger.warning(f"Could not find 'Pick your NEXT DESTINATION' element: {e}")


            # Existing BeautifulSoup parsing for main content
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

            # Final fallback for general content
            if not content_sections and soup.get_text(strip=True):
                content_sections.append({
                    "heading": None,
                    "paragraphs": [soup.get_text(separator=' ', strip=True)],
                    "images": [],
                    "links": []
                })
            
            # --- Handle Dynamic Tab Content ---
            dynamic_sections_data = []
            tab_selectors = {
                "North": 'button:has-text("North")',
                "South": 'button:has-text("South")',
                "West": 'button:has-text("West")',
                "East": 'button:has-text("East")',
                "Central": 'button:has-text("Central")',
            }

            for tab_name, selector in tab_selectors.items():
                try:
                    tab_button = page.locator(selector)
                    await tab_button.wait_for(state='visible', timeout=5000)
                    self.logger.info(f"Clicking tab: {tab_name}")
                    await tab_button.click()
                    # Wait for the content to change after clicking the tab
                    await page.wait_for_selector('div.your_content_container_class_after_tab_click', state='visible', timeout=10000) # IMPORTANT: Replace with actual content container class/selector
                    await page.wait_for_load_state('networkidle') # Wait for new content to load

                    # Get the HTML of the new content section
                    # You'll need to identify the specific container that holds the content that changes when tabs are clicked
                    content_container_selector = 'div.your_content_container_class_after_tab_click' # <<< REPLACE THIS SELECTOR
                    content_container_html = await page.locator(content_container_selector).inner_html()
                    
                    tab_soup = BeautifulSoup(content_container_html, 'html.parser')
                    tab_paragraphs = [p.get_text(strip=True) for p in tab_soup.find_all(['p', 'li']) if p.get_text(strip=True)]
                    
                    dynamic_sections_data.append({
                        "tab_name": tab_name,
                        "content": tab_paragraphs
                    })
                except Exception as e:
                    self.logger.warning(f"Could not scrape content for tab '{tab_name}': {e}")
            
            item['content'] = { 
                "live_with_text": live_with_text,
                "next_destination_prompt": next_destination_text,
                "static_sections": content_sections,
                "dynamic_sections": dynamic_sections_data # Add the dynamic content here
            }
            yield item

        except Exception as e:
            self.logger.error(f"Error parsing HTML for {response.url}: {e}", exc_info=True)
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
        settings = Settings()

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
            'DOWNLOAD_TIMEOUT': 600, # Increased from 60 to 600 seconds (10 minutes)
            'RETRY_TIMES': 2,
            'LOG_LEVEL': 'INFO',

            'TELNETCONSOLE_ENABLED': False,
            'STATS_CLASS': 'scrapy.statscollectors.MemoryStatsCollector',

            'DOWNLOAD_HANDLERS': {
                'http': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
                'https': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
            },
            'TWISTED_REACTOR': 'twisted.internet.asyncioreactor.AsyncioSelectorReactor',
            'PLAYWRIGHT_LAUNCH_OPTIONS': {
                'headless': True, # CRUCIAL for server environments
                'timeout': 30000, # Increased from 20000 ms to 30000 ms for launch
                'args': [
                    '--no-sandbox', # Required for Docker environments
                    '--disable-dev-shm-usage', # Recommended for Docker
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor'
                ]
            },
            # Increased Playwright timeouts for longer navigation and command execution
            'PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT': 180000, # Increased to 3 minutes
            'PLAYWRIGHT_DEFAULT_COMMAND_TIMEOUT': 180000,    # Increased to 3 minutes
            'PLAYWRIGHT_BROWSER_TYPE': 'chromium', # or 'firefox', 'webkit'

            'ITEM_PIPELINES': {
                'scraper.JsonWriterPipeline': 300,
            },

            'FEEDS': {},

            'DEFAULT_REQUEST_HEADERS': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en',
                'Accept-Encoding': 'gzip, deflate',
            },

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

        settings.setdict(base_settings)
        from scrapy.utils.log import configure_logging
        configure_logging(settings)


        runner = CrawlerRunner(settings)
        spider_kwargs = {
            'start_urls': start_urls,
            'scrape_mode': scrape_mode,
            'user_query': user_query,
            'domain': domain,
            'proxy_enabled': proxy_enabled,
            'captcha_solver_enabled': captcha_solver_enabled,
            'results_queue': _scrapy_results_queue
        }

        while not _scrapy_results_queue.empty():
            _scrapy_results_queue.get_nowait()

        yield runner.crawl(GenericSpider, **spider_kwargs)
        logger.info(f"Scrapy crawl for {start_urls} finished successfully.")

    except Exception as e:
        logger.error(f"Error during Scrapy crawl execution: {e}", exc_info=True)
        raise

# --- Reactor Management Thread ---
def _start_reactor_thread():
    """Starts the Twisted reactor in a dedicated thread."""
    global _reactor_thread, _reactor_deferred

    with _reactor_lock:
        if _reactor_thread is None or not _reactor_thread.is_alive():
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
        reactor.run(installSignalHandlers=False)
    except Exception as e:
        logger.error(f"Error in reactor thread: {e}", exc_info=True)
    finally:
        if _reactor_deferred:
            if not _reactor_deferred.called:
                _reactor_deferred.callback(None)

# --- Public API for scraping ---
def scrape_website(url, type="beautify", proxy_enabled=False, captcha_solver_enabled=False):
    logger.info(f"Initiating scrape_website call for {url} (type: {type})")
    _start_reactor_thread()

    d = Deferred()
    reactor.callFromThread(lambda: _execute_scrapy_crawl(
        start_urls=[url],
        scrape_mode=type,
        user_query="",
        proxy_enabled=proxy_enabled,
        captcha_solver_enabled=captcha_solver_enabled
    ).chainDeferred(d))

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
        completion_event.wait(timeout=300)

        if not completion_event.is_set():
            logger.error(f"Scrapy crawl for {url} timed out after 300 seconds.")
            return {"status": "error", "url": url, "type": type, "error": "Scraping operation timed out."}

        if error_container[0]:
            return {"status": "error", "url": url, "type": type, "error": error_container[0]}

    except Exception as e:
        logger.error(f"Unhandled error during scrape_website execution: {e}", exc_info=True)
        return {"status": "error", "url": url, "type": type, "error": str(e)}

    results = []
    while not _scrapy_results_queue.empty():
        try:
            results.append(_scrapy_results_queue.get_nowait())
        except queue.Empty:
            break

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
    logger.info(f"Initiating crawl_website call for {base_url} (type: {type})")
    _start_reactor_thread()

    d = Deferred()
    reactor.callFromThread(lambda: _execute_scrapy_crawl(
        start_urls=[base_url],
        scrape_mode=type,
        user_query=user_query,
        proxy_enabled=proxy_enabled,
        captcha_solver_enabled=captcha_solver_enabled
    ).chainDeferred(d))

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
        completion_event.wait(timeout=600)

        if not completion_event.is_set():
            logger.error(f"Scrapy crawl for {base_url} timed out after 600 seconds.")
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
                # Ensure the structure matches the new item['content']
                page_data["content"] = {
                    "live_with_text": item.get('content', {}).get('live_with_text'),
                    "next_destination_prompt": item.get('content', {}).get('next_destination_prompt'),
                    "static_sections": item.get('content', {}).get('static_sections', []),
                    "dynamic_sections": item.get('content', {}).get('dynamic_sections', []),
                }
            all_data.append(page_data)
        except queue.Empty:
            break

    return all_data
