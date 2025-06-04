import scrapy
from scrapy.spiders import CrawlSpider, Rule
from scrapy.linkextractors import LinkExtractor
from scrapy import Request
from urllib.parse import urlparse, urljoin
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Import the item definition
from my_scraper_project.my_scraper_project.items import ScrapedItem

class GenericSpider(CrawlSpider):
    name = 'generic_spider'
    # allowed_domains will be set dynamically in __init__
    
    rules = (
        # Follow all links within the same domain, but avoid common external sites.
        Rule(LinkExtractor(deny_domains=['google.com', 'facebook.com', 'twitter.com', 'linkedin.com']), callback='parse_item', follow=True),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = kwargs.get('start_urls')
        self.scrape_mode = kwargs.get('scrape_mode', 'beautify')
        self.user_query = kwargs.get('user_query', '')
        self.domain = kwargs.get('domain', '')
        self.proxy_enabled = kwargs.get('proxy_enabled', False)
        self.captcha_solver_enabled = kwargs.get('captcha_solver_enabled', False)

        # Set allowed_domains based on the first start_url
        if self.start_urls:
            parsed_start_url = urlparse(self.start_urls[0])
            self.domain = parsed_start_url.netloc
            self.allowed_domains = [self.domain] if self.domain else []
            logger.info(f"Spider initialized with start_urls: {self.start_urls}, allowed_domains: {self.allowed_domains}")

            # Update rules with dynamic allowed_domains
            self.rules = (
                Rule(LinkExtractor(allow_domains=self.allowed_domains, deny_domains=['google.com', 'facebook.com', 'twitter.com', 'linkedin.com']), callback='parse_item', follow=True),
            )
        else:
            logger.error("GenericSpider initialized without start_urls.")

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
