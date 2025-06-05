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
            # Changed to await page.content() to ensure all dynamic content is included
            page = response.playwright_page
            item['raw_data'] = await page.content()
            await page.close()
            yield item
            return

        try:
            # Access the Playwright page object
            page = response.playwright_page
            
            # Get the full rendered HTML content after Playwright has processed the page
            rendered_html = await page.content()
            soup = BeautifulSoup(rendered_html, 'html.parser')
            
            # --- Generalized content extraction ---
            scraped_content = {}

            # 1. Page Metadata
            scraped_content["metadata"] = {
                "title": soup.title.get_text(strip=True) if soup.title else None,
                "description": soup.find("meta", attrs={"name": "description"})["content"] if soup.find("meta", attrs={"name": "description"}) else None,
                "keywords": soup.find("meta", attrs={"name": "keywords"})["content"] if soup.find("meta", attrs={"name": "keywords"}) else None,
                "og_title": soup.find("meta", attrs={"property": "og:title"})["content"] if soup.find("meta", attrs={"property": "og:title"}) else None,
                "og_description": soup.find("meta", attrs={"property": "og:description"})["content"] if soup.find("meta", attrs={"property": "og:description"}) else None,
                "canonical_url": soup.find("link", attrs={"rel": "canonical"})["href"] if soup.find("link", attrs={"rel": "canonical"}) else None,
            }

            # 2. Extract visible text blocks from the initial page load
            # This captures general content without hardcoding specific phrases
            general_text_content = []
            for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'span', 'div']):
                text = tag.get_text(strip=True)
                if text and len(text) > 10: # Only capture substantial text blocks
                    general_text_content.append(text)
            scraped_content["general_page_text"] = general_text_content

            # 3. Dynamic Tab Content Extraction (Generalized)
            dynamic_sections_data = []

            # Find potential tab-like buttons. Common patterns:
            # - Buttons within a div with a "tabs" class
            # - Buttons with a "role=tab" attribute
            # - Links within a "nav" or "ul" that change content
            
            # Let's try to find elements that look like tab buttons.
            # This is still somewhat heuristic, but more general than hardcoded text.
            # Focus on buttons or anchors in common tab structures
            potential_tab_locators = page.locator("div[class*='tabs'] button, div[class*='nav'] button, [role='tab']")
            
            num_potential_tabs = await potential_tab_locators.count()
            self.logger.info(f"Found {num_potential_tabs} potential tab elements.")

            for i in range(num_potential_tabs):
                try:
                    # Re-locate the button in each iteration to avoid stale element references
                    current_tab_button = potential_tab_locators.nth(i)
                    if not await current_tab_button.is_visible():
                        self.logger.info(f"Skipping invisible tab button at index {i}.")
                        continue

                    tab_text = await current_tab_button.text_content()
                    if not tab_text or len(tab_text.strip()) < 2:
                        self.logger.info(f"Skipping tab button at index {i} with no meaningful text.")
                        continue

                    tab_name = tab_text.strip()
                    self.logger.info(f"Attempting to click tab: '{tab_name}'")

                    # Click the tab button
                    await current_tab_button.click(timeout=10000)

                    # Wait for network idle or a general indicator of content change.
                    # This is crucial for dynamic content loaded after a click.
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    
                    # After clicking, get the updated content of the whole page
                    updated_rendered_html = await page.content()
                    updated_soup = BeautifulSoup(updated_rendered_html, 'html.parser')

                    # Now, try to identify the main content area that likely changes.
                    # This might involve looking for common content containers, or a general
                    # scrape of the 'body' or 'main' tag after the click.
                    # For maximum robustness, we'll re-scrape the whole visible content for the tab
                    
                    tab_specific_content_soup = updated_soup # Assuming the relevant content is now in the main body

                    tab_sections = self._extract_content_from_soup(tab_specific_content_soup)

                    dynamic_sections_data.append({
                        "tab_name": tab_name,
                        "content_sections": tab_sections
                    })
                except Exception as e:
                    self.logger.warning(f"Could not scrape content for dynamic tab (index {i}): {e}")
            
            scraped_content["dynamic_tab_content"] = dynamic_sections_data

            # 4. Main page content (structural)
            # This is a broader, more generic scrape of the main page sections
            main_page_sections = self._extract_content_from_soup(soup)
            scraped_content["main_page_structured_content"] = main_page_sections


            item['content'] = scraped_content
            yield item

        except Exception as e:
            self.logger.error(f"Error parsing HTML for {response.url}: {e}", exc_info=True)
            item['error'] = str(e)
            yield item
        finally:
            if 'playwright_page' in response.meta:
                await response.meta["playwright_page"].close()

    def _extract_content_from_soup(self, soup_obj):
        """Helper method to extract structured content from a BeautifulSoup object."""
        extracted_blocks = []

        # Iterate over common semantic elements and generic divs that might contain content
        for element in soup_obj.find_all(['header', 'nav', 'main', 'article', 'section', 'aside', 'footer', 'div', 'span', 'body']):
            block_data = {
                "tag": element.name,
                "classes": element.get('class', []),
                "id": element.get('id', None),
                "attributes": {k: v for k, v in element.attrs.items() if k not in ['class', 'id']}, # Capture other attributes
                "text_content": element.get_text(strip=True)[:500] if element.get_text(strip=True) else None, # Snippet of text
                "headings": [],
                "paragraphs": [],
                "lists": [],
                "tables": [],
                "images": [],
                "links": [],
                "forms": []
            }
            
            # Headings
            for h in element.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                text = h.get_text(strip=True)
                if text:
                    block_data["headings"].append({"tag": h.name, "text": text})

            # Paragraphs
            for p in element.find_all('p'):
                text = p.get_text(strip=True)
                if text:
                    block_data["paragraphs"].append(text)

            # Lists
            for list_tag in element.find_all(['ul', 'ol', 'dl']):
                list_items = []
                if list_tag.name in ['ul', 'ol']:
                    for li in list_tag.find_all('li'):
                        text = li.get_text(strip=True)
                        if text:
                            list_items.append(text)
                elif list_tag.name == 'dl':
                    for dt, dd in zip(list_tag.find_all('dt'), list_tag.find_all('dd')):
                        dt_text = dt.get_text(strip=True)
                        dd_text = dd.get_text(strip=True)
                        if dt_text or dd_text:
                            list_items.append({"term": dt_text, "description": dd_text})
                if list_items:
                    block_data["lists"].append({"type": list_tag.name, "items": list_items})

            # Tables
            for table in element.find_all('table'):
                table_data = []
                headers = [th.get_text(strip=True) for th in table.find_all('th')]
                rows = []
                for tr in table.find_all('tr'):
                    row_cells = [td.get_text(strip=True) for td in tr.find_all('td')]
                    if row_cells:
                        rows.append(row_cells)
                block_data["tables"].append({"headers": headers, "rows": rows})

            # Images
            for img in element.find_all('img'):
                src = img.get('src')
                alt = img.get('alt')
                if src:
                    abs_src = urljoin(self.start_urls[0] if self.start_urls else '', src)
                    block_data["images"].append({"src": abs_src, "alt": alt})

            # Links
            for a in element.find_all('a'):
                href = a.get('href')
                text = a.get_text(strip=True)
                if href:
                    abs_href = urljoin(self.start_urls[0] if self.start_urls else '', href)
                    block_data["links"].append({"href": abs_href, "text": text})

            # Forms
            for form in element.find_all('form'):
                form_data = {
                    "action": form.get('action'),
                    "method": form.get('method'),
                    "inputs": []
                }
                for input_field in form.find_all(['input', 'textarea', 'select']):
                    input_info = {
                        "tag": input_field.name,
                        "name": input_field.get('name'),
                        "type": input_field.get('type') if input_field.name == 'input' else None,
                        "value": input_field.get('value') if input_field.name == 'input' else input_field.get_text(strip=True),
                        "placeholder": input_field.get('placeholder'),
                        "label": input_field.find_previous_sibling('label').get_text(strip=True) if input_field.find_previous_sibling('label') else None
                    }
                    if input_field.name == 'select':
                        input_info['options'] = [opt.get_text(strip=True) for opt in input_field.find_all('option')]
                    form_data["inputs"].append(input_info)
                block_data["forms"].append(form_data)

            # Only add block if it contains meaningful data
            if any([block_data["headings"], block_data["paragraphs"], block_data["lists"], 
                    block_data["tables"], block_data["images"], block_data["links"], 
                    block_data["forms"]]) or (block_data["text_content"] and len(block_data["text_content"]) > 20):
                extracted_blocks.append(block_data)
        
        return extracted_blocks


    async def errback(self, failure):
        self.logger.error(f"Error in Playwright request: {repr(failure)}")
        request = failure.request
        if 'playwright_page' in request.meta:
            page = request.meta["playwright_page"]
            await page.close()
