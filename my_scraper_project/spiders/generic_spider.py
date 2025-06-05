# my_scraper_project/spiders/generic_spider.py

import scrapy
from scrapy_playwright.page import PageMethod
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import json
import re
import datetime # Import the datetime module

# Assume ScrapedItem is defined in my_scraper_project.items
# Make sure you have my_scraper_project/items.py with ScrapedItem definition
from my_scraper_project.items import ScrapedItem

class GenericSpider(scrapy.Spider):
    name = 'generic_spider'
    # allowed_domains will be set dynamically based on start_urls in start_requests

    # This queue will be set by the scraper.py to put results into
    # We pass it as a custom spider argument.
    results_queue = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dynamically set allowed_domains from the first start_url
        if self.start_urls:
            parsed_uri = urlparse(self.start_urls[0])
            self.allowed_domains = [parsed_uri.netloc]
            self.logger.info(f"Spider initialized with start_urls: {self.start_urls}, allowed_domains: {self.allowed_domains}")
        else:
            self.logger.warning("Spider initialized without start_urls.")
            self.allowed_domains = []

        # Store scrape_mode and user_query if provided as spider arguments
        self.scrape_mode = kwargs.get('scrape_mode', 'beautify')
        self.user_query = kwargs.get('user_query', '')
        
        # Changed from proxy_enabled to proxy_url
        self.proxy_url = kwargs.get('proxy_url', None) 
        self.captcha_solver_enabled = kwargs.get('captcha_solver_enabled', False)
        
        # Get the results_queue instance passed from the CrawlerRunner
        self.results_queue = kwargs.get('results_queue')


    def start_requests(self):
        for url in self.start_urls:
            self.logger.info(f"Making Playwright request for: {url}")
            meta_args = {
                "playwright": True,
                "playwright_include_page": True, # Keep the Playwright page object for dynamic interactions
                # Add Playwright page methods for initial page load if needed, e.g., wait_for_selector
                "playwright_page_methods": [
                    # Wait for the network to be idle, meaning most requests are done
                    PageMethod("wait_for_load_state", "networkidle"),
                    # You can add more actions here if initial page needs interaction
                    # PageMethod("click", "selector_for_cookie_banner"),
                    # PageMethod("wait_for_selector", "body"),
                ],
                "captcha_solver": self.captcha_solver_enabled # Pass captcha setting
            }
            # Only add 'proxy' to meta if a proxy_url is provided
            if self.proxy_url:
                meta_args["proxy"] = self.proxy_url # Pass the proxy_url string

            yield scrapy.Request(
                url,
                meta=meta_args,
                callback=self.parse,
                errback=self.errback, # Ensure errback is correctly referenced
                dont_filter=True # Essential if start_urls can be visited multiple times during development
            )

    async def parse(self, response):
        self.logger.info(f"Parsing response from: {response.url}")
        page = response.meta["playwright_page"]

        item = ScrapedItem()
        item['url'] = response.url
        item['raw_data'] = response.text # Store raw HTML as a fallback

        # --- Dynamic Interaction Phase (if needed) ---
        # Example: Scroll down to load more content
        # await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # await page.wait_for_load_state("networkidle")
        
        # Example: Click a "Load More" button until it's gone
        # while await page.locator("text='Load More'").is_visible():
        #     await page.locator("text='Load More'").click()
        #     await page.wait_for_timeout(1000) # Wait a bit for content to load

        # Get the updated HTML content after all interactions
        html_content = await page.content()
        await page.close() # Close the page when done to free up resources

        soup = BeautifulSoup(html_content, 'html.parser')

        # --- Content Extraction Phase ---
        extracted_content = self._extract_content_from_soup(soup, response.url)
        item['content'] = {
            "metadata": {
                "title": soup.title.string if soup.title else None,
                "description": soup.find('meta', attrs={'name': 'description'})['content'] if soup.find('meta', attrs={'name': 'description'}) else None,
                "keywords": soup.find('meta', attrs={'name': 'keywords'})['content'] if soup.find('meta', attrs={'name': 'keywords'}) else None,
                "url": response.url,
                "timestamp": datetime.datetime.now().isoformat(), # Corrected timestamp acquisition
                "scrape_mode": self.scrape_mode,
                "user_query": self.user_query
            },
            "structured_content": extracted_content
        }
        item['error'] = None # No error if we reached here

        yield item

    def _extract_content_from_soup(self, soup: BeautifulSoup, base_url: str):
        """
        Extracts various content blocks from the BeautifulSoup object,
        focusing on common semantic elements.
        """
        extracted_sections = []
        
        # Prioritize main content areas if identifiable
        main_content_selectors = [
            'main',
            '.main-content',
            '#main-content',
            'article',
            '.post',
            '.entry-content',
            'div[role="main"]'
        ]
        
        main_content_block = None
        for selector in main_content_selectors:
            main_content_block = soup.select_one(selector)
            if main_content_block:
                break
        
        # Fallback to body if no specific main content block is found
        if not main_content_block:
            main_content_block = soup.find('body')

        if not main_content_block:
            self.logger.warning(f"Could not find a main content block for {base_url}. Extracting from entire soup.")
            main_content_block = soup # Fallback to entire soup if body also not found (unlikely)


        # Define block-level elements to iterate over
        block_elements = main_content_block.find_all(
            ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'table', 'img', 'a', 'form', 'li', 'div', 'span']
        )
        
        # Filter out elements that are likely part of navigation, footer, or sidebars
        # This is a heuristic and might need tuning per site
        unwanted_selectors = [
            'nav', 'footer', 'aside', '.sidebar', '#sidebar', '.header', '#header',
            '.menu', '.pagination', '.breadcrumbs', '.ad', '.ads', '.modal', '.popup',
            '[role="navigation"]', '[role="complementary"]', '[role="banner"]', '[role="contentinfo"]',
            '[aria-hidden="true"]', '.hidden', '.display-none'
        ]

        def is_relevant_block(tag):
            if tag.name in ['script', 'style', 'noscript', 'meta', 'link', 'template']:
                return False
            # Check if any parent (or self) matches an unwanted selector
            for selector in unwanted_selectors:
                if tag.select_one(f":scope > {selector}, :scope {selector}"):
                    return False
            return True


        for block in block_elements:
            if not is_relevant_block(block):
                continue

            section_data = {
                "tag": block.name,
                "text": block.get_text(strip=True), # Raw text for the block
                "heading": None,
                "paragraphs": [],
                "list_items": [],
                "table": None, # Will store table as structured dict/list
                "images": [],
                "links": [],
                "forms": None, # Will store form details
                "html": str(block) # Store raw HTML of the block
            }

            # Extract headings
            if block.name.startswith('h') and block.name[1:].isdigit():
                section_data["heading"] = block.get_text(strip=True)
            
            # Extract paragraphs
            elif block.name == 'p':
                section_data["paragraphs"].append(block.get_text(strip=True))

            # Extract lists (ul, ol)
            elif block.name in ['ul', 'ol']:
                list_items = []
                for li in block.find_all('li', recursive=False): # Only direct children
                    list_items.append(li.get_text(strip=True))
                if list_items:
                    section_data["list_items"] = list_items

            # Extract tables
            elif block.name == 'table':
                table_data = []
                for row in block.find_all('tr'):
                    row_data = []
                    for cell in row.find_all(['td', 'th']):
                        row_data.append(cell.get_text(strip=True))
                    table_data.append(row_data)
                if table_data:
                    section_data["table"] = table_data

            # Extract images within the block (if block is not an img itself)
            if block.name != 'img':
                for img in block.find_all("img"):
                    src = img.get("src")
                    if src:
                        abs_url = urljoin(base_url, src)
                        img_alt = img.get("alt", "")
                        section_data["images"].append({"src": abs_url, "alt": img_alt})
            else: # If the block itself is an img
                src = block.get("src")
                if src:
                    abs_url = urljoin(base_url, src)
                    img_alt = block.get("alt", "")
                    section_data["images"].append({"src": abs_url, "alt": img_alt})

            # Extract links within the block (if block is not an a itself)
            if block.name != 'a':
                for a in block.find_all("a"):
                    href = a.get("href")
                    if href:
                        abs_href = urljoin(base_url, href)
                        link_text = a.get_text(strip=True)
                        section_data["links"].append({"href": abs_href, "text": link_text})
            else: # If the block itself is an a
                href = block.get("href")
                if href:
                    abs_href = urljoin(base_url, href)
                    link_text = block.get_text(strip=True)
                    section_data["links"].append({"href": abs_href, "text": link_text})

            # Extract forms
            if block.name == 'form':
                form_details = {
                    "action": urljoin(base_url, block.get('action', '')),
                    "method": block.get('method', 'get').lower(),
                    "inputs": []
                }
                for input_tag in block.find_all(['input', 'textarea', 'select']):
                    input_detail = {
                        "tag": input_tag.name,
                        "type": input_tag.get('type', 'text') if input_tag.name == 'input' else input_tag.name,
                        "name": input_tag.get('name'),
                        "value": input_tag.get('value'),
                        "placeholder": input_tag.get('placeholder')
                    }
                    form_details["inputs"].append(input_detail)
                section_data["forms"] = form_details

            # Add block to extracted sections if it contains meaningful data
            # Adjust condition to check against newly structured data
            if (section_data["heading"] or section_data["paragraphs"] or 
                section_data["list_items"] or section_data["table"] or 
                section_data["images"] or section_data["links"] or section_data["forms"]):
                extracted_sections.append(section_data)
        
        # Final fallback if no structured content is found but there's some text
        if not extracted_sections and soup.get_text(strip=True):
            extracted_sections.append({
                "tag": "body_text_fallback",
                "text": soup.get_text(separator=' ', strip=True),
                "heading": None,
                "paragraphs": [soup.get_text(separator=' ', strip=True)],
                "list_items": [],
                "table": None,
                "images": [],
                "links": [],
                "forms": None,
                "html": str(soup) # Full soup HTML as fallback
            })
        
        return extracted_sections


    async def errback(self, failure):
        self.logger.error(f"Error in Playwright request for {failure.request.url}: {repr(failure)}")
        # You can inspect failure.value for specific exceptions
        # For example, if 'playwright_page' is in response.meta:
        if 'playwright_page' in failure.request.meta:
            page = failure.request.meta['playwright_page']
            if page:
                try:
                    await page.close()
                except Exception as e:
                    self.logger.error(f"Error closing Playwright page in errback: {e}")

        # Yield an item to signal an error for this URL
        item = ScrapedItem()
        item['url'] = failure.request.url
        item['content'] = None # No content if there was an error
        item['raw_data'] = None
        item['error'] = {
            "type": failure.type.__name__,
            "message": str(failure.value),
            "traceback": failure.getTraceback()
        }
        yield item
