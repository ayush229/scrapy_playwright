# generic_spider.py

import scrapy
from scrapy import Request, Spider
from scrapy_playwright.page import PageMethod
from urllib.parse import urlparse, urljoin
import logging
from bs4 import BeautifulSoup
import asyncio

logger = logging.getLogger(__name__)

# Assuming 'my_scraper_project' is the root of your Scrapy project
# and 'items.py' is inside 'my_scraper_project' directory
from my_scraper_project.items import ScrapedItem

class GenericSpider(Spider):
    name = 'generic_spider'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = kwargs.get('start_urls')
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
            logger.info(f"Spider initialized with start_urls: {self.start_urls}, allowed_domains: {self.allowed_domains}")
        else:
            logger.error("GenericSpider initialized without start_urls.")

    def start_requests(self):
        for url in self.start_urls:
            yield Request(
                url,
                meta={
                    'playwright': True,
                    'playwright_page_methods': [
                        # Wait for the entire page to settle after initial JS execution.
                        PageMethod("wait_for_load_state", "networkidle"),
                        # A general wait for common interactive elements to be present,
                        # like buttons or links within the body. This is a heuristic.
                        PageMethod("wait_for_selector", "body > *:visible", state="attached", timeout=15000),
                    ],
                    'playwright_include_page': True,
                },
                callback=self.parse_item,
                errback=self.errback
            )

    async def parse_item(self, response):
        item = ScrapedItem()
        item['url'] = response.url
        item['error'] = None

        page = response.meta["playwright_page"]

        try:
            if self.scrape_mode == 'raw':
                # Get the full rendered HTML content after Playwright has processed the page
                item['raw_data'] = await page.content()
                yield item
                await page.close()
                return

            # --- Beautify mode: Scrape all available content ---
            scraped_content = {}

            # Get the full rendered HTML content from the Playwright page
            rendered_html = await page.content()
            soup = BeautifulSoup(rendered_html, 'html.parser')
            
            # 1. Page Metadata (Title, Meta Descriptions, etc.)
            scraped_content["metadata"] = {
                "title": soup.title.get_text(strip=True) if soup.title else None,
                "description": soup.find("meta", attrs={"name": "description"})["content"] if soup.find("meta", attrs={"name": "description"}) else None,
                "keywords": soup.find("meta", attrs={"name": "keywords"})["content"] if soup.find("meta", attrs={"name": "keywords"}) else None,
                "og_title": soup.find("meta", attrs={"property": "og:title"})["content"] if soup.find("meta", attrs={"property": "og:title"}) else None,
                "og_description": soup.find("meta", attrs={"property": "og:description"})["content"] if soup.find("meta", attrs={"property": "og:description"}) else None,
                "canonical_url": soup.find("link", attrs={"rel": "canonical"})["href"] if soup.find("link", attrs={"rel": "canonical"}) else None,
            }

            # 2. General Page Structured Content (from initial load)
            # This extracts content blocks from the main page body
            scraped_content["main_page_structured_content"] = self._extract_content_from_soup(soup)

            # 3. Dynamic Tab Content Extraction (Generalized)
            dynamic_sections_data = []

            # Find potential tab-like buttons. These are heuristics:
            # - Buttons directly under a common "tabs" or "nav" class div
            # - Elements with ARIA role="tab"
            # - Buttons that might be within a section that looks like a tab bar (e.g., div with class containing "tabs" or "nav")
            # This attempts to be general but might need fine-tuning for very unique tab UIs.
            
            # Prioritize elements with role="tab" as they are semantically correct for tabs.
            # Fallback to buttons or anchors in common tab-like containers.
            potential_tab_locators = page.locator("[role='tab'], div[class*='tab'] button, div[class*='nav'] button, div[class*='tab'] a, div[class*='nav'] a")
            
            num_potential_tabs = await potential_tab_locators.count()
            self.logger.info(f"Found {num_potential_tabs} potential tab elements to interact with.")

            for i in range(num_potential_tabs):
                try:
                    # Re-locate the button in each iteration to avoid stale element references
                    # and to ensure we're always interacting with the current state of the DOM.
                    current_tab_element = potential_tab_locators.nth(i)
                    
                    if not await current_tab_element.is_visible(timeout=5000):
                        self.logger.info(f"Skipping invisible tab element at index {i}.")
                        continue

                    # Get text content; if no text, try aria-label or title
                    tab_text = (await current_tab_element.text_content()).strip()
                    if not tab_text:
                        tab_text = await current_tab_element.get_attribute('aria-label') or await current_tab_element.get_attribute('title')
                    
                    if not tab_text or len(tab_text.strip()) < 2:
                        self.logger.info(f"Skipping tab element at index {i} with no meaningful text or attributes.")
                        continue

                    tab_name = tab_text.strip()
                    self.logger.info(f"Attempting to click dynamic tab: '{tab_name}' (index: {i})")

                    # Click the tab button
                    await current_tab_element.click(timeout=10000)

                    # CRUCIAL: Wait for the new content to load or become stable after the click.
                    # This waits for the network to be idle again and potentially a small delay for rendering.
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    await asyncio.sleep(0.5) # Small buffer for rendering

                    # After clicking, get the updated content of the *entire page*
                    # because the dynamic content could be anywhere.
                    updated_rendered_html = await page.content()
                    updated_soup = BeautifulSoup(updated_rendered_html, 'html.parser')

                    # Extract structured content from the *entire updated page*.
                    # The `_extract_content_from_soup` function will then identify and categorize content.
                    tab_sections = self._extract_content_from_soup(updated_soup)

                    dynamic_sections_data.append({
                        "tab_name": tab_name,
                        "content_on_tab_click": tab_sections
                    })

                except Exception as e:
                    self.logger.warning(f"Could not scrape content for dynamic tab (index {i}, text '{tab_name}'): {e}", exc_info=True)
                    dynamic_sections_data.append({
                        "tab_name": tab_name if tab_name else f"Tab {i}",
                        "error": str(e)
                    })
            
            scraped_content["dynamic_tab_content"] = dynamic_sections_data

            item['content'] = scraped_content
            yield item

        except Exception as e:
            self.logger.error(f"An unhandled error occurred in parse_item for {response.url}: {e}", exc_info=True)
            item['error'] = str(e)
            yield item
        finally:
            if 'playwright_page' in response.meta:
                await response.meta["playwright_page"].close()

    def _extract_content_from_soup(self, soup_obj):
        """
        Helper method to comprehensively extract structured content from a BeautifulSoup object.
        It tries to identify common content blocks within a given soup.
        """
        extracted_blocks = []

        # Define a list of tags that typically contain meaningful content blocks
        # Prioritize semantic HTML5 tags, then common structural divs/spans
        content_block_tags = ['body', 'main', 'article', 'section', 'div', 'span']

        for element in soup_obj.find_all(content_block_tags):
            # Avoid processing elements that are likely script/style containers or empty
            if element.name in ['script', 'style', 'noscript', 'meta', 'link', 'title'] or not element.get_text(strip=True):
                continue
            
            # Simple heuristic to avoid very small or likely decorative elements if they're not
            # explicitly a heading, paragraph, etc.
            if element.name in ['div', 'span'] and len(element.get_text(strip=True)) < 50 and \
               not any(element.find_all(['h1', 'h2', 'h3', 'p', 'ul', 'ol', 'table', 'img', 'a', 'form'])):
                continue

            block_data = {
                "tag": element.name,
                "classes": element.get('class', []),
                "id": element.get('id', None),
                "attributes": {k: v for k, v in element.attrs.items() if k not in ['class', 'id', 'style']}, # Capture other relevant attributes
                "text_snippet": element.get_text(strip=True)[:500] if element.get_text(strip=True) else None, # Snippet of text
                "headings": [],
                "paragraphs": [],
                "lists": [],
                "tables": [],
                "images": [],
                "links": [],
                "forms": [],
                "structured_data": [] # For microdata, JSON-LD, etc., if needed (advanced)
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
                elif list_tag.name == 'dl': # Definition lists
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
                # Extract headers if available (from thead or th)
                headers = [th.get_text(strip=True) for th in table.find_all('th')]
                
                rows = []
                for tr in table.find_all('tr'):
                    # Only get td, not th (if headers were already processed)
                    row_cells = [cell.get_text(strip=True) for cell in tr.find_all(['td', 'th']) if cell.name == 'td' or not headers]
                    if row_cells:
                        rows.append(row_cells)
                block_data["tables"].append({"headers": headers, "rows": rows})

            # Images
            for img in element.find_all('img'):
                src = img.get('src')
                alt = img.get('alt')
                title = img.get('title')
                if src:
                    abs_src = urljoin(self.start_urls[0] if self.start_urls else '', src)
                    block_data["images"].append({"src": abs_src, "alt": alt, "title": title})

            # Links
            for a in element.find_all('a'):
                href = a.get('href')
                text = a.get_text(strip=True)
                title = a.get('title')
                if href:
                    abs_href = urljoin(self.start_urls[0] if self.start_urls else '', href)
                    block_data["links"].append({"href": abs_href, "text": text, "title": title})

            # Forms and their input elements
            for form in element.find_all('form'):
                form_data = {
                    "action": form.get('action'),
                    "method": form.get('method'),
                    "id": form.get('id'),
                    "name": form.get('name'),
                    "inputs": []
                }
                for input_field in form.find_all(['input', 'textarea', 'select']):
                    input_info = {
                        "tag": input_field.name,
                        "name": input_field.get('name'),
                        "id": input_field.get('id'),
                        "type": input_field.get('type') if input_field.name == 'input' else None,
                        "value": input_field.get('value') if input_field.name == 'input' else input_field.get_text(strip=True),
                        "placeholder": input_field.get('placeholder'),
                        "label_text": None # Will try to find an associated label
                    }
                    # Try to find an associated label using 'for' attribute or by proximity
                    if input_field.get('id'):
                        label_tag = soup_obj.find('label', attrs={'for': input_field.get('id')})
                        if label_tag:
                            input_info['label_text'] = label_tag.get_text(strip=True)
                    if not input_info['label_text']: # Fallback to previous sibling label
                        prev_sibling = input_field.find_previous_sibling('label')
                        if prev_sibling:
                            input_info['label_text'] = prev_sibling.get_text(strip=True)

                    if input_field.name == 'select':
                        input_info['options'] = [{"value": opt.get('value'), "text": opt.get_text(strip=True)} for opt in input_field.find_all('option')]
                    form_data["inputs"].append(input_info)
                block_data["forms"].append(form_data)

            # Only add block if it contains meaningful data
            if any([block_data["headings"], block_data["paragraphs"], block_data["lists"], 
                    block_data["tables"], block_data["images"], block_data["links"], 
                    block_data["forms"]]) or (block_data["text_snippet"] and len(block_data["text_snippet"]) > 20):
                extracted_blocks.append(block_data)
        
        return extracted_blocks
