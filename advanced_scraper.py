import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Set, List, Dict, Any
from playwright.async_api import async_playwright, Page, BrowserContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class FootfyTVScraper:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.extracted_data: List[Dict[str, Any]] = []

    async def _configure_context(self, browser) -> BrowserContext:
        return await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US"
        )

    async def _block_ads(self, route):
        excluded_resources = ["image", "stylesheet", "font"]
        blocked_domains = ["google-analytics", "doubleclick", "popads", "popcash", "adsterra", "exoclick"]
        
        url = route.request.url
        resource_type = route.request.resource_type

        if resource_type in excluded_resources or any(domain in url for domain in blocked_domains):
            await route.abort()
        else:
            await route.continue_()

    def _is_stream_link(self, url: str) -> bool:
        patterns = [r"\.m3u8", r"\.mpd", r"/embed/", r"/player/", r"stream", r"watch"]
        ignored = [r"analytics", r"facebook", r"twitter", r"google"]
        return any(re.search(p, url, re.IGNORECASE) for p in patterns) and not any(re.search(i, url, re.IGNORECASE) for i in ignored)

    async def get_all_match_links(self, page: Page) -> List[str]:
        """হোমপেজ থেকে সকল লাইভ ও স্পোর্টস ম্যাচের লিংক সংগ্রহ করা"""
        logging.info("Fetching all match links from home page...")
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # /watch/ বা ম্যাচ আইডিযুক্ত লিংক ক্রল করা
        links = await page.eval_on_selector_all("a[href*='/watch/']", "elements => elements.map(el => el.href)")
        unique_matches = list(set(links))
        logging.info(f"Found {len(unique_matches)} matches on homepage.")
        return unique_matches

    async def scrape_match_player(self, context: BrowserContext, match_url: str) -> Dict[str, Any]:
        """প্রতিটি ম্যাচের ভেতরে গিয়ে মূল স্ট্রিমিং ও প্লেয়ার লিংক ধরা"""
        page = await context.new_page()
        player_links: Set[str] = set()

        await page.route("**/*", self._block_ads)

        def on_request(request):
            if self._is_stream_link(request.url):
                player_links.add(request.url)

        page.on("request", on_request)

        logging.info(f"Scraping player links from: {match_url}")
        try:
            await page.goto(match_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)

            # iframe সোর্স চেক করা
            iframes = await page.eval_on_selector_all("iframe", "iframes => iframes.map(i => i.src)")
            for src in iframes:
                if src and src.startswith("http") and self._is_stream_link(src):
                    player_links.add(src)

            # সার্ভার বাটনে অটো-ক্লিক করে নতুন ড্রাইভ/প্লেয়ার লোড করা
            servers = await page.query_selector_all("button:has-text('Server'), .server-btn, a[data-url]")
            for btn in servers[:4]:
                if await btn.is_visible():
                    await btn.click(force=True)
                    await page.wait_for_timeout(1000)

        except Exception as e:
            logging.error(f"Failed to extract from {match_url}: {e}")
        finally:
            await page.close()

        return {
            "match_url": match_url,
            "total_players_found": len(player_links),
            "player_links": list(player_links)
        }

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = await self._configure_context(browser)

            # ১. হোমপেজের এক্সেস নেওয়া
            main_page = await context.new_page()
            await main_page.route("**/*", self._block_ads)
            match_links = await self.get_all_match_links(main_page)
            await main_page.close()

            # ২. প্রতিটি ম্যাচ পেজ থেকে ভিডিও প্লেয়ার লিংক সংগ্রহ করা
            for match in match_links:
                data = await self.scrape_match_player(context, match)
                self.extracted_data.append(data)

            await browser.close()
            self._save_results()

    def _save_results(self, filename: str = "advanced_links.json"):
        output = {
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "source": self.base_url,
            "total_matches_scraped": len(self.extracted_data),
            "matches": self.extracted_data
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        logging.info(f"Successfully saved all player links to {filename}")

if __name__ == "__main__":
    BASE_URL = "https://footfytv.pro/"
    scraper = FootfyTVScraper(base_url=BASE_URL)
    asyncio.run(scraper.run())
