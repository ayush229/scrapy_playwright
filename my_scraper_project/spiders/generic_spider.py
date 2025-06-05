import scrapy
# CHANGE: Import scrapy.Spider instead of CrawlSpider
from scrapy import Request, Spider # Changed from CrawlSpider
# REMOVED: LinkExtractor and Rule are no longer needed as we are not crawling
# from scrapy.linkextractors import LinkExtractor
# from scrapy.spiders import CrawlSpider, Rule
from scrapy_playwright.page import PageMethod # ADDED: Import PageMethod for Playwright interactions
from urllib.parse import urlparse, urljoin
import logging
from bs4 import BeautifulSoup
import asyncio # ADDED: Import asyncio for async operations

logger = logging.getLogger(__name__)

# Import the item definition
# Assuming 'my_scraper_project' is the root of your Scrapy project
# and 'items.py' is inside 'my_scraper_project' directory
from my_scraper_project.items import ScrapedItem


# CHANGE: Inherit from scrapy.Spider
class GenericSpider(Spider): # Changed from CrawlSpider
    name = 'generic_spider'
    # allowed_domains will be set dynamically in __init__
    
    # REMOVED: rules attribute as scrapy.Spider does not use it for link following
    # rules = (
    #     Rule(LinkExtractor(deny_domains=['google.com', 'facebook.com', 'twitter.com', 'linkedin.com']), callback='parse_item', follow=True),
    # )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = kwargs.get('start_urls')
        self.scrape_mode = kwargs.get('scrape_mode', 'beautify')
        self.user_query = kwargs.get('user_query', '')
        self.domain = kwargs.get('domain', '')
        self.proxy_enabled = kwargs.get('proxy_enabled', False)
        self.captcha_solver_enabled = kwargs.get('captcha_solver_enabled', False)
        # Assuming results_queue is passed from _execute_scrapy_crawl
        self.results_queue = kwargs.get('results_queue', None)


        # Set allowed_domains based on the first start_url
        if self.start_urls:
            parsed_start_url = urlparse(self.start_urls[0])
            self.domain = parsed_start_url.netloc
            # allowed_domains are still useful for general Scrapy filtering,
            # though less critical for a non-crawling spider.
            self.allowed_domains = [self.domain] if self.domain else []
            logger.info(f"Spider initialized with start_urls: {self.start_urls}, allowed_domains: {self.allowed_domains}")

            # REMOVED: No need to update rules as they are no longer part of scrapy.Spider
            # self.rules = (
            #     Rule(LinkExtractor(allow_domains=self.allowed_domains, deny_domains=['google.com', 'facebook.com', 'twitter.com', 'linkedin.com']), callback='parse_item', follow=True),
            # )
        else:
            logger.error("GenericSpider initialized without start_urls.")

    def start_requests(self):
        for url in self.start_urls:
            # Tell Scrapy to use Playwright for this request
            # and include PageMethod calls to wait for dynamic content
            # Also, set playwright_include_page to True to access the page object in parse_item
            yield Request(
                url,
                meta={
                    'playwright': True,
                    'playwright_page_methods': [
                        # ADDED: Wait for "LIVE with Code" element (replace with actual selector)
                        # This ensures the main dynamic content is loaded.
                        PageMethod("wait_for_selector", "YOUR_LIVE_CONTENT_SELECTOR", state="visible", timeout=10000),
                        # ADDED: Wait for "Pick your next destination" element (replace with actual selector)
                        PageMethod("wait_for_selector", "YOUR_DESTINATION_TEXT_SELECTOR", state="visible", timeout=10000),
                        # You might also add: PageMethod("wait_for_load_state", "networkidle")
                        # if the page takes time to settle after initial JS execution.
                    ],
                    'playwright_include_page': True, # CRUCIAL: To get the Playwright page object in parse_item
                },
                callback=self.parse_item,
                errback=self.errback # ADDED: Error handling for Playwright requests
            )

    # CHANGE: parse_item is now an async function
    async def parse_item(self, response):
        item = ScrapedItem()
        item['url'] = response.url
        item['error'] = None

        try:
            # Access the Playwright page object
            page = response.meta["playwright_page"]

            if self.scrape_mode == 'raw':
                # For raw mode, the full HTML including dynamic content is available via page.content()
                item['raw_data'] = await page.content()
                yield item
                # Close the page after processing
                await page.close()
                return

            # --- Beautify mode: Scrape initial dynamic content ---
            # Extract "LIVE with Code" and "Pick your next destination"
            # REPLACE 'YOUR_LIVE_CONTENT_SELECTOR' and 'YOUR_DESTINATION_TEXT_SELECTOR'
            # with the actual CSS selectors from the website.
            try:
                live_content_element = await page.locator("YOUR_LIVE_CONTENT_SELECTOR").text_content()
                item['live_content'] = live_content_element.strip() if live_content_element else None
            except Exception as e:
                self.logger.warning(f"Could not find or extract 'LIVE with Code' content: {e}")
                item['live_content'] = None
            
            try:
                destination_text_element = await page.locator("YOUR_DESTINATION_TEXT_SELECTOR").text_content()
                item['destination_text'] = destination_text_element.strip() if destination_text_element else None
            except Exception as e:
                self.logger.warning(f"Could not find or extract 'Pick your next destination' text: {e}")
                item['destination_text'] = None

            # --- Handle tabs and their dynamic content ---
            all_tab_content = []

            # REPLACE 'YOUR_TAB_BUTTON_SELECTOR' with the CSS selector that matches all tab buttons
            tab_buttons = await page.locator("YOUR_TAB_BUTTON_SELECTOR").all()
            
            if not tab_buttons:
                self.logger.warning("No tab buttons found with the specified selector. Proceeding with static content.")
                # If no tabs, proceed with initial HTML parsing as fallback or primary method
                soup = BeautifulSoup(response.text, 'html.parser')
                content_sections = self._extract_content_from_soup(soup)
                item['content'] = { "sections": content_sections }
                yield item
                await page.close()
                return


            for i, tab_button in enumerate(tab_buttons):
                try:
                    tab_name = await tab_button.text_content()
                    self.logger.info(f"Clicking on tab: {tab_name} (index: {i})")

                    # Click the tab button
                    await tab_button.click()

                    # CRUCIAL: Wait for the content specific to this tab to load/become visible.
                    # REPLACE 'YOUR_TAB_CONTENT_CONTAINER_SELECTOR'
                    # This selector should target the *container* of the content that changes when tabs are clicked.
                    # It might be an ID like '#tabContentArea' or a class like '.active-tab-panel'
                    # You may need a more specific wait condition if content takes longer to render.
                    await page.wait_for_selector("YOUR_TAB_CONTENT_CONTAINER_SELECTOR", state="visible", timeout=15000)
                    
                    # Optional: Add a small delay if content truly takes time to settle visually
                    # await asyncio.sleep(1) 

                    # Get the HTML of the currently active tab's content
                    # Use inner_html() to get the HTML content of the container
                    tab_content_html = await page.locator("YOUR_TAB_CONTENT_CONTAINER_SELECTOR").inner_html()
                    
                    # Parse the tab-specific HTML with BeautifulSoup to extract structured data
                    tab_soup = BeautifulSoup(tab_content_html, 'html.parser')
                    tab_sections = self._extract_content_from_soup(tab_soup)

                    all_tab_content.append({
                        "tab_name": tab_name.strip() if tab_name else f"Tab {i+1}",
                        "content_sections": tab_sections
                    })

                except Exception as e:
                    self.logger.error(f"Error processing tab {i} ({tab_name}): {e}", exc_info=True)
                    all_tab_content.append({
                        "tab_name": tab_name.strip() if tab_name else f"Tab {i+1}",
                        "error": str(e)
                    })

            # Combine all scraped content (initial page and tab contents)
            item['content'] = {
                "initial_live_content": item.pop('live_content', None), # Move this to the main content dict
                "initial_destination_text": item.pop('destination_text', None), # Move this
                "tabbed_content": all_tab_content
            }
            
            yield item

        except Exception as e:
            logger.error(f"Error in parse_item for {response.url}: {e}", exc_info=True)
            item['error'] = str(e)
            yield item
        finally:
            # Ensure the Playwright page is closed in all cases
            if 'playwright_page' in response.meta:
                await response.meta["playwright_page"].close()

    def _extract_content_from_soup(self, soup):
        """Helper method to extract structured content from a BeautifulSoup object."""
        content_sections = []
        
        sections = soup.find_all(['section', 'div', 'article', 'main', 'body'])
        if not sections and soup.body:
            sections = [soup.body]
        elif not sections:
            sections = [soup] # Fallback to entire soup if no specific tags found

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

            paragraphs = sec.find_all(['p', 'li', 'span', 'div']) # Include more tags for text
            for p in paragraphs:
                text = p.get_text(strip=True)
                if text and len(text) > 5: # Filter out very short or empty strings
                    section_data["paragraphs"].append(text)

            for img in sec.find_all("img"):
                src = img.get("src")
                if src:
                    # urljoin correctly handles relative and absolute URLs
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
        # Log and handle errors during Playwright requests
        self.logger.error(f"Error in Playwright request: {repr(failure)}")
        request = failure.request
        if 'playwright_page' in request.meta:
            page = request.meta["playwright_page"]
            await page.close()
