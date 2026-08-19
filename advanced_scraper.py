import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Set, List, Dict, Any
from playwright.async_api import async_playwright, Page, BrowserContext

# লগিং কনফিগারেশন
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class AdvancedStreamScraper:
    def __init__(self, target_urls: List[str]):
        self.target_urls = target_urls
        self.extracted_data: List[Dict[str, Any]] = []

    async def _configure_context(self, browser) -> BrowserContext:
        """ব্রাউজার কনটেক্সট এবং রিয়েলিস্টিক ইউজার-এজেন্ট সেটআপ"""
        return await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            locale="en-US",
            timezone_id="UTC"
        )

    async def _block_ads_and_trackers(self, route):
        """অ্যাডের ডোমেইন ও ইমেজ/ফন্ট ব্লক করে স্পিড বাড়ানোর ফাংশন"""
        excluded_resources = ["image", "stylesheet", "font", "media"]
        blocked_domains = ["google-analytics", "doubleclick", "popads", "popcash", "adsterra", "exoclick"]
        
        request_url = route.request.url
        resource_type = route.request.resource_type

        if resource_type in excluded_resources or any(domain in request_url for domain in blocked_domains):
            await route.abort()
        else:
            await route.continue_()

    def _is_valid_stream_link(self, url: str) -> bool:
        """ভিডিও ও স্ট্রিমিং ইউআরএল যাচাই করার ফিল্টার"""
        patterns = [r"\.m3u8", r"\.mpd", r"/embed/", r"/player/", r"stream", r"live"]
        ignored_patterns = [r"analytics", r"facebook", r"twitter", r"captcha", r"ad-provider"]
        
        is_matched = any(re.search(p, url, re.IGNORECASE) for p in patterns)
        is_ignored = any(re.search(p, url, re.IGNORECASE) for p in ignored_patterns)
        
        return is_matched and not is_ignored

    async def _handle_network_requests(self, page: Page, captured_links: Set[str]):
        """নেটওয়ার্ক ট্রাফিক থেকে ডাইনামিক লিংক এক্সট্র্যাক্ট করা"""
        def on_request(request):
            url = request.url
            if self._is_valid_stream_link(url):
                captured_links.add(url)

        page.on("request", on_request)

    async def _trigger_dynamic_elements(self, page: Page):
        """প্লেয়ার বোতাম, সার্ভার সুইচিং ট্যাব এবং ড্রপডাউনে অটো-ক্লিক করার ফাংশন"""
        try:
            # পেজের অ্যাড পপ-আপ রিমুভ করা
            await page.evaluate("""() => {
                const ads = document.querySelectorAll('div[id*="ad"], div[class*="ad"], iframe[src*="ad"]');
                ads.forEach(ad => ad.remove());
            }""")
            
            # সার্ভার পরিবর্তনের বোতাম থাকলে ক্লিক করা
            server_buttons = await page.query_selector_all("button:has-text('Server'), a:has-text('Server'), .server-btn")
            for btn in server_buttons[:5]: # প্রথম ৫টি সার্ভার ট্রাই করবে
                if await btn.is_visible():
                    await btn.click(force=True)
                    await page.wait_for_timeout(1500)
        except Exception as e:
            logging.warning(f"Error clicking dynamic elements: {e}")

    async def scrape_single_url(self, context: BrowserContext, url: str) -> Dict[str, Any]:
        """এক একক পেজ স্ক্র্যাপ করার মূল প্রসেস"""
        page = await context.new_page()
        captured_links: Set[str] = set()

        # রাউটিং অ্যাড-ব্লক অ্যাক্টিভেশন
        await page.route("**/*", self._block_ads_and_trackers)
        await self._handle_network_requests(page, captured_links)

        logging.info(f"Navigating to: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)

            # ডাইনামিক ক্লিক ও ইন্টারঅ্যাকশন
            await self._trigger_dynamic_elements(page)

            # HTML-এ থাকা সমস্ত iframe src উদ্ধার
            iframe_sources = await page.eval_on_selector_all("iframe", "iframes => iframes.map(i => i.src)")
            for src in iframe_sources:
                if src and src.startswith("http") and self._is_valid_stream_link(src):
                    captured_links.add(src)

        except Exception as e:
            logging.error(f"Failed to scrape {url}: {e}")
        finally:
            await page.close()

        return {
            "page_url": url,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "total_found": len(captured_links),
            "stream_links": list(captured_links)
        }

    async def run_pipeline(self):
        """মাল্টি-ইউআরএল মেথড চালনা করার মেন ফাংশন"""
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-web-security"]
            )
            context = await self._configure_context(browser)

            tasks = [self.scrape_single_url(context, url) for url in self.target_urls]
            self.extracted_data = await asyncio.gather(*tasks)

            await browser.close()
            self._save_to_json()

    def _save_to_json(self, filename: str = "advanced_links.json"):
        """রিপোর্ট জেনারেট করে লোকাল ফাইলে সেভ করা"""
        output = {
            "execution_time": datetime.utcnow().isoformat() + "Z",
            "total_targets": len(self.target_urls),
            "results": self.extracted_data
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        logging.info(f"Results successfully exported to {filename}")


if __name__ == "__main__":
    # ভবিষ্যতে একাধিক লিঙ্ক যুক্ত করতে পারেন
    TARGETS = [
        "https://footfytv.pro/watch/2328"
    ]
    
    scraper = AdvancedStreamScraper(target_urls=TARGETS)
    asyncio.run(scraper.run_pipeline())
