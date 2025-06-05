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
                        # Wait for a prominent element on the landing page, like the "North" tab button.
                        # This confirms the main interactive elements are loaded.
                        PageMethod("wait_for_selector", "div.tabs_main button:has-text('North')", state="visible", timeout=15000),
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
                item['raw_data'] = await page.content()
                yield item
                await page.close()
                return

            # --- Beautify mode: Scrape initial dynamic content ---
            # Refined selectors for the top section elements
            try:
                # "LIVE with Code GODARSHAN" - Targeting the container for this whole block
                # The div has class 'top-left' and contains the "LIVE with" text and the button.
                # Use a more specific locator for the 'GODARSHAN' part if needed, or get the whole text
                live_content_block_locator = page.locator(".top-left")
                await live_content_block_locator.wait_for(state='visible', timeout=5000)
                live_content_block_text = await live_content_block_locator.text_content()
                item['live_content'] = live_content_block_text.strip() if live_content_block_text else None
            except Exception as e:
                self.logger.warning(f"Could not find or extract 'LIVE with Code GODARSHAN' content: {e}")
                item['live_content'] = None
            
            try:
                # "Pick your next destination from these sacred sites" - Target the h2 directly
                destination_text_locator = page.locator("h2:has-text('Pick your NEXT DESTINATION from these sacred sites')")
                await destination_text_locator.wait_for(state='visible', timeout=5000)
                destination_text_element = await destination_text_locator.text_content()
                item['destination_text'] = destination_text_element.strip() if destination_text_element else None
            except Exception as e:
                self.logger.warning(f"Could not find or extract 'Pick your next destination' text: {e}")
                item['destination_text'] = None

            # --- Handle tabs and their dynamic content ---
            all_tab_content = []

            # Selector for the tab buttons (North, South, etc.)
            # Based on the screenshot: div.tabs_main contains button elements.
            tab_buttons_locators = page.locator("div.tabs_main button")

            num_tabs = await tab_buttons_locators.count()
            self.logger.info(f"Found {num_tabs} potential tab buttons.")

            for i in range(num_tabs):
                tab_name = "Unknown" # Default in case text_content fails

                try:
                    # Re-locate the button in each iteration to avoid stale element references
                    current_tab_button = tab_buttons_locators.nth(i)
                    tab_name = await current_tab_button.text_content()
                    self.logger.info(f"Processing tab: {tab_name.strip()} (index: {i})")

                    # Click the tab button
                    await current_tab_button.click()

                    # CRUCIAL: Wait for the content specific to this tab to load/become visible.
                    # The screenshot shows the content within `div.cli.data-content-tabsX.tabopen.active`.
                    # Let's wait for a *specific* element *inside* the content, like `div.food-box`.
                    # This ensures the new content has fully rendered.
                    # The selector for the active tab content: `div.tabs-content.second-content div.cli.tabopen.active`
                    # We then wait for a common element inside it, like `div.food-box`.
                    await page.wait_for_selector(f"div.tabs-content.second-content div.cli.tabopen.active div.food-box", state="visible", timeout=20000)
                    
                    # Optional: Add a small delay if content truly takes time to settle visually
                    # await asyncio.sleep(0.5) 

                    # Get the HTML of the currently active tab's content container.
                    # This should capture the entire active tab's rendered content, including food-box divs.
                    tab_content_container_html = await page.locator("div.tabs-content.second-content div.cli.tabopen.active").inner_html()
                    
                    # Parse the tab-specific HTML with BeautifulSoup to extract structured data
                    tab_soup = BeautifulSoup(tab_content_container_html, 'html.parser')
                    tab_sections = self._extract_content_from_soup(tab_soup)

                    all_tab_content.append({
                        "tab_name": tab_name.strip(),
                        "content_sections": tab_sections
                    })

                except Exception as e:
                    self.logger.error(f"Error processing tab {i} ({tab_name.strip()}): {e}", exc_info=True)
                    all_tab_content.append({
                        "tab_name": tab_name.strip(),
                        "error": str(e)
                    })

            # Combine all scraped content (initial page and tab contents)
            item['content'] = {
                "initial_live_content": item.pop('live_content', None),
                "initial_destination_text": item.pop('destination_text', None),
                "tabbed_content": all_tab_content
            }
            
            yield item

        except Exception as e:
            logger.error(f"An unhandled error occurred in parse_item for {response.url}: {e}", exc_info=True)
            item['error'] = str(e)
            yield item
        finally:
            if 'playwright_page' in response.meta:
                await response.meta["playwright_page"].close()

    def _extract_content_from_soup(self, soup):
        """Helper method to extract structured content from a BeautifulSoup object."""
        extracted_sections = []
        
        # Prioritize 'food-box' elements if they exist, based on your screenshot.
        # This is where content like "Mandua Ki Roti" resides.
        content_blocks = soup.select('div.food-box')
        
        if not content_blocks:
            # Fallback to broader sections if no specific food-box found
            content_blocks = soup.find_all(['section', 'div', 'article', 'main', 'body'])
            if not content_blocks and soup.body:
                content_blocks = [soup.body]
            elif not content_blocks:
                content_blocks = [soup] # Last resort: treat entire soup as one block

        for block in content_blocks:
            section_data = {
                "heading": None,
                "paragraphs": [],
                "images": [],
                "links": [],
                "list_items": [] # ADDED: To specifically capture list items like "Mandua Ki Roti"
            }
            
            # Extract heading (e.g., Kashi Vishwanath Temple Varanasi)
            # The screenshot shows h2 inside a div.main_offer.
            # We'll check for h2 first, then other general headings.
            main_heading = block.find('h2') # Direct H2 often means main heading
            if main_heading and main_heading.get_text(strip=True):
                section_data["heading"] = {"tag": main_heading.name, "text": main_heading.get_text(strip=True)}
            else:
                # Fallback to other heading tags if no h2
                for tag_name in ['h1', 'h3', 'h4', 'h5', 'h6']:
                    heading = block.find(tag_name)
                    if heading and heading.get_text(strip=True):
                        section_data["heading"] = { "tag": heading.name, "text": heading.get_text(strip=True) }
                        break
            
            # Extract main paragraphs (like "Food to treat your taste buds")
            # The screenshot shows a <p> tag within `div.food-text`
            food_text_p = block.select_one('div.food-text p')
            if food_text_p and food_text_p.get_text(strip=True):
                section_data["paragraphs"].append(food_text_p.get_text(strip=True))
            
            # Also extract other generic paragraphs if they exist
            other_paragraphs = block.find_all('p')
            for p_tag in other_paragraphs:
                text = p_tag.get_text(strip=True)
                if text and len(text) > 5 and p_tag != food_text_p: # Avoid duplicating
                    section_data["paragraphs"].append(text)


            # Extract list items (like "Mandua Ki Roti")
            # The screenshot shows <ul><li> for these items
            list_items = block.find_all('li')
            for li in list_items:
                text = li.get_text(strip=True)
                if text:
                    section_data["list_items"].append(text)

            # Extract images (e.g., from food-img div)
            for img in block.find_all("img"):
                src = img.get("src")
                if src:
                    abs_url = urljoin(self.start_urls[0] if self.start_urls else '', src)
                    section_data["images"].append(abs_url)

            # Extract links
            for a in block.find_all("a"):
                href = a.get("href")
                if href:
                    abs_href = urljoin(self.start_urls[0] if self.start_urls else '', href)
                    section_data["links"].append(abs_href)

            if section_data["heading"] or section_data["paragraphs"] or \
               section_data["images"] or section_data["links"] or section_data["list_items"]:
                extracted_sections.append(section_data)
        
        # Final fallback if no structured content is found but there's some text
        if not extracted_sections and soup.get_text(strip=True):
            extracted_sections.append({
                "heading": None,
                "paragraphs": [soup.get_text(separator=' ', strip=True)],
                "images": [],
                "links": [],
                "list_items": []
            })
        
        return extracted_sections


    async def errback(self, failure):
        self.logger.error(f"Error in Playwright request: {repr(failure)}")
        request = failure.request
        if 'playwright_page' in request.meta:
            page = request.meta["playwright_page"]
            await page.close()
