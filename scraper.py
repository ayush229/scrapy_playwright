# scraper.py

# Import install_reactor as the very first Twisted-related import
from scrapy.utils.reactor import install_reactor

# IMPORTANT: Install the reactor immediately when the module is loaded.
# This must happen before any other Twisted components try to auto-select a reactor.
try:
    install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")
except Exception as e:
    # This might happen if another part of the application or environment
    # has already installed a reactor.
    # Log a warning but proceed.
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
    content = Field() # This will now be a rich dictionary
    raw_data = Field()
    error = Field()

# Re-import GenericSpider to ensure it's from the correct path
from my_scraper_project.spiders.generic_spider import GenericSpider


# --- Twisted Reactor Management ---

def _start_reactor_thread():
    """Starts the Twisted reactor in a new thread if it's not already running."""
    global _reactor_thread, _reactor_deferred
    with _reactor_lock:
        if _reactor_thread is None or not _reactor_thread.is_alive():
            logger.info("Starting Twisted reactor in a new thread.")
            _reactor_deferred = Deferred() # Create a new deferred for this run
            _reactor_thread = threading.Thread(target=_run_reactor_blocking, args=(_reactor_deferred,), daemon=True)
            _reactor_thread.start()
        else:
            logger.info("Twisted reactor thread is already running.")

def _run_reactor_blocking(d):
    """Function to run the reactor in a blocking manner in a separate thread."""
    try:
        reactor.run(installSignalHandlers=False) # Don't install signal handlers in a sub-thread
    except Exception as e:
        logger.error(f"Error running reactor: {e}")
    finally:
        # This will fire the deferred when reactor stops, signaling completion
        if not d.called:
            d.callback(None)
        logger.info("Twisted reactor thread stopped.")

@inlineCallbacks
def _run_scrapy_spider_async(start_urls, scrape_mode='beautify', user_query='', proxy_enabled=False, captcha_solver_enabled=False):
    """
    Asynchronously runs the Scrapy spider and collects results.
    This function must be called from within the Twisted reactor's thread.
    """
    settings = Settings()
    # Assuming 'my_scraper_project' is the root of your Scrapy project
    # and settings.py is directly inside it.
    settings_module = 'my_scraper_project.settings' 
    settings.setmodule(settings_module, priority='project')

    # Pass the queue to the spider via custom_settings, then retrieve it in the pipeline
    # NOTE: The queue itself cannot be pickled, so we pass it during spider init.
    # The pipeline will then access it from the spider instance.
    settings.set('ITEM_PIPELINES', {
        'scraper.JsonWriterPipeline': 300, # Reference the pipeline directly from scraper.py
    }, priority='cmdline') # Use high priority to ensure it overrides project settings

    runner = CrawlerRunner(settings)
    
    # Pass results_queue directly during spider instantiation
    deferred = runner.crawl(
        GenericSpider,
        start_urls=start_urls,
        scrape_mode=scrape_mode,
        user_query=user_query,
        proxy_enabled=proxy_enabled,
        captcha_solver_enabled=captcha_solver_enabled,
        results_queue=_scrapy_results_queue # Pass the queue here
    )
    
    yield deferred # Wait for the crawl to finish

    # Signal that the crawl is complete
    logger.info("Scrapy crawl finished for current request.")

def _stop_reactor_thread():
    """Stops the Twisted reactor if it's running."""
    global _reactor_deferred
    with _reactor_lock:
        if reactor.running:
            logger.info("Stopping Twisted reactor.")
            # Call stop() in the reactor's thread to avoid cross-thread issues
            reactor.callFromThread(reactor.stop)
            
            # Wait for the deferred to fire, indicating reactor has truly stopped
            if _reactor_deferred and not _reactor_deferred.called:
                # Use threads.blockingCallFromThread if you need to block the
                # current thread until the reactor stops.
                # For most cases, just letting the deferred be called is enough.
                logger.info("Waiting for reactor shutdown deferred.")
                _reactor_deferred.addErrback(lambda fail: logger.error(f"Reactor shutdown error: {fail.value}"))
                return defer.DeferredList([_reactor_deferred])
            else:
                logger.info("Reactor deferred already called or not set.")
                return defer.succeed(None)
        else:
            logger.info("Twisted reactor is not running.")
            return defer.succeed(None) # Return an immediately successful deferred


# --- Public API for scraping ---

def scrape_website(url: str, scrape_mode: str = 'beautify', user_query: str = '', proxy_enabled: bool = False, captcha_solver_enabled: bool = False) -> list:
    """
    Initiates a Scrapy crawl for a single URL and returns the results.
    The scrape_mode can be 'beautify' (default) for parsed content or 'raw' for raw HTML.
    """
    _start_reactor_thread() # Ensure reactor is running

    results = []
    # Clear the queue before starting a new scrape
    while not _scrapy_results_queue.empty():
        try:
            _scrapy_results_queue.get_nowait()
        except queue.Empty:
            pass

    # Call the spider run in the reactor's thread
    d = threads.deferToThread(
        _run_scrapy_spider_async,
        start_urls=[url],
        scrape_mode=scrape_mode,
        user_query=user_query,
        proxy_enabled=proxy_enabled,
        captcha_solver_enabled=captcha_solver_enabled
    )

    # This callback will run in the main thread once the crawl (and its deferred) completes
    def collect_results(result):
        logger.info("Scrapy crawl deferred completed. Collecting results.")
        while not _scrapy_results_queue.empty():
            results.append(_scrapy_results_queue.get())
        logger.info(f"Collected {len(results)} items.")
        return results

    def err_collect_results(failure):
        logger.error(f"Scrapy crawl deferred failed: {failure}", exc_info=True)
        # Collect any partial results if available
        while not _scrapy_results_queue.empty():
            results.append(_scrapy_results_queue.get())
        return results # Return partial results even on error

    d.addCallbacks(collect_results, err_collect_results)
    
    # We need to block the current thread until the deferred completes,
    # because the scraper function is synchronous from the caller's perspective.
    # This must be done carefully to avoid blocking the reactor thread itself.
    # The deferToThread ensures _run_scrapy_spider_async runs on reactor thread,
    # and the result collection happens when d fires.
    
    # Use a threading.Event to signal completion in the main thread
    completion_event = threading.Event()
    final_results = []
    final_error = None

    def on_crawl_complete(res):
        nonlocal final_results
        final_results = res
        completion_event.set()

    def on_crawl_error(failure):
        nonlocal final_error
        final_error = failure
        completion_event.set()
    
    d.addCallbacks(on_crawl_complete, on_crawl_error)

    # Wait for the event, ensuring the main thread is blocked until crawl finishes
    completion_event.wait(timeout=360) # Increased timeout to 6 minutes for potentially long crawls

    if not completion_event.is_set():
        logger.warning("Scrapy crawl did not complete within the allotted time. Partial results might be returned.")

    if final_error:
        logger.error(f"Scrapy crawl completed with error: {final_error.value}")
        # Optionally, re-raise the error or include it in results
        # For now, we'll return collected results.
        return final_results
    return final_results


def crawl_website(start_url: str, depth: int = 1, scrape_mode: str = 'beautify', user_query: str = '', proxy_enabled: bool = False, captcha_solver_enabled: bool = False) -> list:
    """
    Initiates a broader Scrapy crawl, following links up to a specified depth.
    NOTE: Link extraction logic needs to be integrated into GenericSpider's `parse` method
    if you want to use a CrawlSpider-like behavior with depth.
    For this current GenericSpider (which is a basic Spider), it only scrapes the start_url.
    If you need actual depth-based crawling, GenericSpider would need to implement
    rules similar to Scrapy's CrawlSpider, or you'd switch to CrawlSpider itself.
    """
    logger.warning("The current `GenericSpider` is a basic Spider and does not implement link following for depth. "
                   "It will only scrape the `start_url` provided. To enable depth crawling, "
                   "`GenericSpider` needs to be enhanced with `LinkExtractor` and rules, or converted to a `CrawlSpider`.")
    return scrape_website(start_url, scrape_mode, user_query, proxy_enabled, captcha_solver_enabled)
