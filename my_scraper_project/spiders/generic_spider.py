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
                        # This is a good general catch-all for dynamic content.
                        PageMethod("wait_for_load_state", "networkidle"),
                        # Explicitly wait for the "Pick your next destination" section which signals content readiness
                        PageMethod("wait_for_selector", "h2:has-text('Pick your NEXT DESTINATION')", state="visible", timeout=15000),
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
            # Using precise selectors from your screenshot
            try:
                # Scrape "LIVE with Code GODARSHAN"
                # This grabs the text content of the div containing "LIVE with Code" and the button/div "GODARSHAN"
                live_content_full = await page.locator(".top-left div:has-text('LIVE with')").text_content()
                item['live_content'] = live_content_full.strip() if live_content_full else None
            except Exception as e:
                self.logger.warning(f"Could not find or extract 'LIVE with Code GODARSHAN' content: {e}")
                item['live_content'] = None
            
            try:
                # Scrape "Pick your next destination from these sacred sites"
                destination_text_element = await page.locator("h2:has-text('Pick your NEXT DESTINATION')").text_content()
                item['destination_text'] = destination_text_element.strip() if destination_text_element else None
            except Exception as e:
                self.logger.warning(f"Could not find or extract 'Pick your next destination' text: {e}")
                item['destination_text'] = None

            # --- Handle tabs and their dynamic content ---
            all_tab_content = []

            # Selector for the tab buttons (North, South, etc.)
            # Assuming these are buttons or divs with specific classes forming the tabs
            # The screenshot shows them as <button> tags with text content
            tab_buttons_locators = page.locator("div.tabs_main button") # Adjust if not <button> or different parent

            # Get the count of tabs to iterate accurately
            num_tabs = await tab_buttons_locators.count()
            self.logger.info(f"Found {num_tabs} potential tab buttons.")

            for i in range(num_tabs):
                try:
                    # Re-locate the button in each iteration to avoid stale element references
                    tab_button = tab_buttons_locators.nth(i)
                    tab_name = await tab_button.text_content()
                    self.logger.info(f"Processing tab: {tab_name.strip()} (index: {i})")

                    # Click the tab button
                    await tab_button.click()

                    # CRUCIAL: Wait for the content specific to this tab to load/become visible.
                    # Based on screenshot: 'div.tabs-content.second-content' or 'div.cli.data-content-tabs<N>.tabopen.active'
                    # The content itself appears in div.main_offer inside the active tab.
                    # Let's wait for an element that is definitely inside the newly active tab's content.
                    # A good strategy is to wait for the main content container *inside* the active tab.
                    # The screenshot shows `div.cli.data-content-tabs2.tabopen.active` as the active tab content container.
                    # We can target the main offer container inside the currently active tab.
                    await page.wait_for_selector(f"div.tabs-content.second-content div.cli.tabopen.active div.main_offer", state="visible", timeout=20000)
                    
                    # Optional: Add a small delay if content truly takes time to settle visually
                    # await asyncio.sleep(0.5) 

                    # Get the HTML of the currently active tab's content
                    # Target the specific content container within the active tab.
                    tab_content_html = await page.locator("div.tabs-content.second-content div.cli.tabopen.active").inner_html()
                    
                    # Parse the tab-specific HTML with BeautifulSoup to extract structured data
                    tab_soup = BeautifulSoup(tab_content_html, 'html.parser')
                    tab_sections = self._extract_content_from_soup(tab_soup)

                    all_tab_content.append({
                        "tab_name": tab_name.strip() if tab_name else f"Tab {i+1}",
                        "content_sections": tab_sections
                    })

                except Exception as e:
                    tab_name_current = await tab_buttons_locators.nth(i).text_content() if i < await tab_buttons_locators.count() else f"Unknown Tab {i+1}"
                    self.logger.error(f"Error processing tab {i} ({tab_name_current.strip()}): {e}", exc_info=True)
                    all_tab_content.append({
                        "tab_name": tab_name_current.strip(),
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
        content_sections = []
        
        # Target common content-holding elements more precisely within the tab content
        # Your screenshot shows content within `div.main_offer` inside the active tab
        # Let's try to target those more specifically.
        main_offer_divs = soup.select('div.main_offer') # Use select for CSS selector

        if not main_offer_divs and soup.body:
            sections = [soup.body]
        elif not main_offer_divs:
            sections = [soup] # Fallback to entire soup if no specific tags found
        else:
            sections = main_offer_divs

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

            # Focus on paragraph-like elements that contain substantial text
            paragraphs = sec.find_all(['p', 'li', 'div'], string=lambda text: text and len(text.strip()) > 5)
            for p in paragraphs:
                text = p.get_text(strip=True)
                if text:
                    section_data["paragraphs"].append(text)

            for img in sec.find_all("img"):
                src = img.get("src")
                if src:
                    abs_url = urljoin(self.start_urls[0] if self.start_urls else '', src)
                    section_data["images"].append(abs_url)

            for a in sec.find_all("a"):
                href = a.get("href")
                if href:
                    abs_href = urljoin(self.start_urls[0] if self.start_urls else '', href)
                    section_data["links"].append(abs_href)

            if section_data["heading"] or section_data["paragraphs"] or section_data["images"] or section_data["links"]:
                content_sections.append(section_data)
        
        # Final fallback if no structured content is found but there's some text
        if not content_sections and soup.get_text(strip=True):
            content_sections.append({
                "heading": None,
                "paragraphs": [soup.get_text(separator=' ', strip=True)],
                "images": [],
                "links": []
            })
        
        return content_sections


    async def errback(self, failure):
        self.logger.error(f"Error in Playwright request: {repr(failure)}")
        request = failure.request
        if 'playwright_page' in request.meta:
            page = request.meta["playwright_page"]
            await page.close()
