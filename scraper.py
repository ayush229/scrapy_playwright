# scraper.py
import requests
from bs4 import BeautifulSoup
import logging
from urllib.parse import urlparse, urljoin
import re
import queue
import threading
from scrapy.crawler import CrawlerRunner
from scrapy.utils.project import get_project_settings
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import CrawlSpider, Rule
from scrapy.item import Item, Field
from scrapy import Request
from twisted.internet import reactor, defer
from twisted.internet.defer import inlineCallbacks
import asyncio
# from playwright.sync_api import sync_playwright # Not directly used in _run_scrapy_spider call

logger = logging.getLogger(__name__)

# This queue will hold results from Scrapy and be read by scraper.py
_scrapy_results_queue = queue.Queue()

class ScrapedItem(Item):
    url = Field()
    content = Field()
    raw_data = Field()
    error = Field()

class GenericSpider(CrawlSpider):
    name = 'generic_spider'
    custom_settings = {
        'ROBOTSTXT_OBEY': False,
        'DOWNLOAD_TIMEOUT': 60, # Increased timeout for Playwright
        'FEEDS': {}, # No direct feed output, use pipeline
        'ITEM_PIPELINES': {
            'my_scraper_project.my_scraper_project.pipelines.JsonWriterPipeline': 300,
        },
        'DOWNLOAD_HANDLERS': {
            'http': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
            'https': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
        },
        'TWISTED_REACTOR': 'twisted.internet.asyncioreactor.AsyncioSelectorReactor', # Scrapy's way of integrating with asyncio
        'PLAYWRIGHT_LAUNCH_OPTIONS': {
            'headless': True, # Run Playwright in headless mode
            'timeout': 20000, # 20 seconds
        },
        'PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT': 30000, # 30 seconds
        'PLAYWRIGHT_DEFAULT_COMMAND_TIMEOUT': 30000, # 30 seconds
        'PLAYWRIGHT_BROWSER_TYPE': 'chromium', # or 'firefox', 'webkit'
        # 'PLAYWRIGHT_PROXY': { # Example proxy configuration for Playwright directly
        #     'server': 'http://your_proxy_server:port',
        #     'username': 'proxy_user',
        #     'password': 'proxy_password',
        #     'no_proxy': ['localhost', '127.0.0.1']
        # },
        # Add a custom setting to pass the queue
        'SCRAPY_RESULTS_QUEUE': None, # This will be updated dynamically by _run_scrapy_spider
    }

    rules = (
        Rule(LinkExtractor(deny_domains=['google.com', 'facebook.com', 'twitter.com']), callback='parse_item', follow=True),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scrape_mode = kwargs.get('scrape_mode', 'beautify')
        self.user_query = kwargs.get('user_query', '') # For crawl_ai, if spider needs to know query
        self.domain = kwargs.get('domain', '')
        self.proxy_enabled = kwargs.get('proxy_enabled', False)
        self.captcha_solver_enabled = kwargs.get('captcha_solver_enabled', False)
        
        # Adjust allowed_domains dynamically based on start_urls
        if self.start_urls:
            parsed_start_url = urlparse(self.start_urls[0])
            self.domain = parsed_start_url.netloc
            self.allowed_domains = [self.domain] if self.domain else []
            # Update rules with dynamic allowed_domains for LinkExtractor if needed
            self.rules = (
                Rule(LinkExtractor(allow_domains=self.allowed_domains, deny_domains=['google.com', 'facebook.com', 'twitter.com', 'linkedin.com']), callback='parse_item', follow=True),
            )

        # Enable proxy middleware if configured
        if self.proxy_enabled:
            # Note: For Playwright's actual proxying, the 'PLAYWRIGHT_PROXY' setting is key.
            # This middleware is more for standard HTTP requests if you were using requests.get directly or
            # if you had a custom proxy rotation logic not tied to Playwright's built-in proxy.
            # For simplicity, we'll indicate if it needs to be uncommented in settings.py.
            if 'DOWNLOADER_MIDDLEWARES' not in self.custom_settings:
                self.custom_settings['DOWNLOADER_MIDDLEWARES'] = {}
            self.custom_settings['DOWNLOADER_MIDDLEWARES']['my_scraper_project.my_scraper_project.middlewares.ProxyMiddleware'] = 543
            logger.info("Proxy enabled. Remember to configure PLAYWRIGHT_PROXY in settings.py for Playwright requests.")

        if self.captcha_solver_enabled:
            if 'DOWNLOADER_MIDDLEWARES' not in self.custom_settings:
                self.custom_settings['DOWNLOADER_MIDDLEWARES'] = {}
            self.custom_settings['DOWNLOADER_MIDDLEWARES']['my_scraper_project.my_scraper_project.middlewares.CaptchaSolverMiddleware'] = 544


    def start_requests(self):
        # Requests will be handled by Playwright by default due to settings
        for url in self.start_urls:
            yield Request(url, meta={'playwright': True})

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
            
            if not sections and soup.body: # Fallback if specific sections are not found, use whole body
                sections = [soup.body] 
            elif not sections: # If no body either, just use the soup object directly
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

                paragraphs = sec.find_all(['p', 'li', 'span', 'div']) # Broaden paragraph search
                for p in paragraphs:
                    text = p.get_text(strip=True)
                    if text and len(text) > 5: # Filter out very short or empty strings
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

            # Final fallback if no structured content was found but general text exists
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
            logger.error(f"Error parsing HTML for {response.url}: {e}")
            item['error'] = str(e)
            yield item


@inlineCallbacks
def _run_scrapy_spider_async(start_urls, scrape_mode, user_query, proxy_enabled, captcha_solver_enabled):
    """Helper to run Scrapy spider asynchronously."""
    try:
        # Clear the queue before a new run
        while not _scrapy_results_queue.empty():
            _scrapy_results_queue.get_nowait()

        settings = get_project_settings()
        settings.update({
            'SCRAPY_RESULTS_QUEUE': _scrapy_results_queue,
            'USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        })

        domain = urlparse(start_urls[0]).netloc if start_urls else ''
        settings['ALLOWED_DOMAINS'] = [domain] if domain else []
        
        # Configure Playwright specific settings for the scrape_mode if needed
        if scrape_mode == 'beautify':
            settings['PLAYWRIGHT_PROCESS_REQUEST_HEADERS'] = None
            settings['PLAYWRIGHT_PROCESS_RESPONSE_HEADERS'] = None

        if proxy_enabled:
            logger.info("Proxy enabled. Ensure 'PLAYWRIGHT_PROXY' is configured in my_scraper_project/settings.py.")

        runner = CrawlerRunner(settings)
        spider_kwargs = {
            'start_urls': start_urls,
            'scrape_mode': scrape_mode,
            'user_query': user_query,
            'domain': domain,
            'proxy_enabled': proxy_enabled,
            'captcha_solver_enabled': captcha_solver_enabled
        }
        
        yield runner.crawl(GenericSpider, **spider_kwargs)
        
    except Exception as e:
        logger.error(f"Error running Scrapy spider: {e}", exc_info=True)


def _run_scrapy_spider_in_thread(start_urls, scrape_mode, user_query, proxy_enabled, captcha_solver_enabled):
    """Helper to run Scrapy process in a separate thread using reactor."""
    try:
        # Check if reactor is already running
        if reactor.running:
            # If reactor is running, we need to use it differently
            d = _run_scrapy_spider_async(start_urls, scrape_mode, user_query, proxy_enabled, captcha_solver_enabled)
            return d
        else:
            # Start the reactor in this thread
            def run_spider():
                d = _run_scrapy_spider_async(start_urls, scrape_mode, user_query, proxy_enabled, captcha_solver_enabled)
                d.addBoth(lambda _: reactor.stop())
                return d
            
            reactor.callWhenRunning(run_spider)
            reactor.run(installSignalHandlers=False)
            
    except Exception as e:
        logger.error(f"Error running Scrapy spider in thread: {e}", exc_info=True)


def _run_scrapy_spider(start_urls, scrape_mode="beautify", user_query="", proxy_enabled=False, captcha_solver_enabled=False):
    """
    Synchronous wrapper to run the Scrapy spider and collect results.
    Returns:
        list: A list of dictionaries, each representing a scraped item.
    """
    try:
        # Method 1: Try running without threading first (simpler approach)
        if not reactor.running:
            def run_and_stop():
                d = _run_scrapy_spider_async(start_urls, scrape_mode, user_query, proxy_enabled, captcha_solver_enabled)
                d.addBoth(lambda _: reactor.stop())
                return d
            
            # Clear the queue before starting
            while not _scrapy_results_queue.empty():
                _scrapy_results_queue.get_nowait()
                
            reactor.callWhenRunning(run_and_stop)
            reactor.run(installSignalHandlers=False)
        else:
            # If reactor is already running, we need a different approach
            logger.warning("Reactor already running. Using alternative approach.")
            # You might need to implement a different strategy here
            # For now, return empty results with an error
            return []
        
        # Collect results
        results = []
        while not _scrapy_results_queue.empty():
            results.append(_scrapy_results_queue.get_nowait())
        return results
        
    except Exception as e:
        logger.error(f"Error in _run_scrapy_spider: {e}", exc_info=True)
        return []


def scrape_website(url, type="beautify", proxy_enabled=False, captcha_solver_enabled=False):
    """
    Scrapes a single website using Scrapy/Playwright.
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
                      If type is a beautify, this is a dictionary with 'sections'.
            - "error": (Only present if status is "error") A string describing the error.
    """
    logger.info(f"Synchronous scrape_website call for {url} (type: {type})")
    scraped_items = _run_scrapy_spider(
        start_urls=[url],
        scrape_mode=type,
        proxy_enabled=proxy_enabled,
        captcha_solver_enabled=captcha_solver_enabled
    )

    if scraped_items:
        first_item = scraped_items[0] # Assuming only one item for single URL scrape
        if first_item.get('error'):
            return {"status": "error", "url": url, "type": type, "error": first_item['error']}
        elif type == 'raw':
            return {"status": "success", "url": url, "type": type, "data": first_item.get('raw_data')}
        else: # beautify
            return {"status": "success", "url": url, "type": type, "data": first_item.get('content')}
    else:
        return {"status": "error", "url": url, "type": type, "error": "No data scraped or unknown error."}


def crawl_website(base_url, type="beautify", user_query="", proxy_enabled=False, captcha_solver_enabled=False):
    """
    Crawls a website using Scrapy/Playwright.
    Args:
        base_url (str): The starting URL for the crawl.
        type (str): 'raw' for raw HTML, 'beautify' for structured content.
        user_query (str): User query for 'crawl_ai' type.
        proxy_enabled (bool): Whether to use proxies.
        captcha_solver_enabled (bool): Whether to enable captcha solving.
    Returns:
        list: A list of dictionaries, each representing a crawled page
              (containing 'url', 'content'/'raw_data', or 'error').
    """
    logger.info(f"Synchronous crawl_website call for {base_url} (type: {type})")
    
    # Scrapy will handle the crawling and link extraction based on GenericSpider's rules.
    scraped_items = _run_scrapy_spider(
        start_urls=[base_url],
        scrape_mode=type,
        user_query=user_query,
        proxy_enabled=proxy_enabled,
        captcha_solver_enabled=captcha_solver_enabled
    )

    all_data = []
    for item in scraped_items:
        page_data = {"url": item.get('url')}
        if item.get('error'):
            page_data["error"] = item['error']
        elif type == 'raw':
            page_data["raw_data"] = item.get('raw_data')
        else: # beautify
            page_data["content"] = item.get('content', {}).get('sections', [])
        all_data.append(page_data)

    return all_data
