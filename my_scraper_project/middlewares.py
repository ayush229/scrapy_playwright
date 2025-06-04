from scrapy import signals
import logging
import os
import random

logger = logging.getLogger(__name__)

class ProxyMiddleware:
    # Dummy proxy middleware. For real use, populate `proxies` from a file or external service.
    # PROXIES should be in the format 'http://user:pass@host:port' or 'http://host:port'
    
    # Example: If you have a file named 'proxies.txt' in the root of 'my_scraper_project'
    # with one proxy per line:
    # http://user1:pass1@proxy1.com:8080
    # https://user2:pass2@proxy2.com:8080
    
    def __init__(self):
        self.proxies = self._load_proxies()
        if not self.proxies:
            logger.warning("No proxies loaded for ProxyMiddleware. Ensure proxies.txt is present and correctly formatted.")

    def _load_proxies(self):
        # Adjusted path to look for proxies.txt in the root project directory (parent of parent of this file)
        proxy_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../proxies.txt')
        proxies = []
        if os.path.exists(proxy_file_path):
            with open(proxy_file_path, 'r') as f:
                for line in f:
                    proxy = line.strip()
                    if proxy:
                        proxies.append(proxy)
        return proxies

    def process_request(self, request, spider):
        if self.proxies and request.meta.get('playwright'): # Only apply proxy for Playwright requests
            # Scrapy-Playwright handles proxies via PLAYWRIGHT_PROXY in settings.
            # This middleware is more for standard HTTP requests if you were using requests.get directly or
            # if you had a custom proxy rotation logic not tied to Playwright's built-in proxy.
            # For Playwright, it's simpler to set PLAYWRIGHT_PROXY in settings.py.
            # We'll leave this here as a placeholder for general proxy management if needed.
            logger.debug(f"Playwright proxy should be configured via PLAYWRIGHT_PROXY in settings.py. Middleware skipped.")
            return None # Don't process request if Playwright handles it.
        elif self.proxies:
            proxy = random.choice(self.proxies)
            request.meta['proxy'] = proxy
            logger.info(f"Using proxy {proxy} for {request.url}")
        return None

class CaptchaSolverMiddleware:
    def process_response(self, request, response, spider):
        # This is a placeholder for a real CAPTCHA solving logic.
        # CAPTCHA solving is complex and usually involves external services (e.g., 2Captcha, Anti-Captcha)
        # or advanced browser automation with ML models.
        #
        # Example pseudo-code:
        # if "captcha" in response.url or "captcha" in response.text.lower():
        #     logger.warning(f"CAPTCHA detected on {response.url}. Attempting to solve...")
        #     # Here, you would integrate with a CAPTCHA solving API
        #     # solved_response = self.solve_captcha(request)
        #     # if solved_response:
        #     #    return solved_response # Return a new response after solving
        #     # else:
        #     #    spider.logger.error(f"Failed to solve CAPTCHA on {response.url}.")
        #     #    return request.replace(dont_filter=True) # Retry or mark as error
        #
        # For this copy-paste, it will just log a warning.
        if response.status == 403 and "cloudflare" in response.headers.get('Server', '').lower():
            logger.warning(f"Cloudflare CAPTCHA/DDoS protection detected on {response.url}. Manual intervention or specialized bypass might be required.")
        return response
