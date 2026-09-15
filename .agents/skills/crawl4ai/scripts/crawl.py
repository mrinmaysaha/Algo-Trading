import asyncio
import sys
import argparse
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

async def crawl(url: str, output_file: str = None):
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(markdown_generator=None)

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
        if result.success:
            content = result.markdown
            if output_file:
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"[SUCCESS] Crawled and wrote {len(content)} chars to {output_file}")
            else:
                # Print first 2000 chars for CLI summary
                print(f"[SUCCESS] Crawled {url} ({len(content)} chars):\n")
                print(content[:2500])
        else:
            print(f"[ERROR] Crawl failed: {result.error_message}")
            sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl4AI CLI tool")
    parser.add_argument("url", help="URL to crawl")
    parser.add_argument("--output", "-o", help="Optional output markdown file path")
    args = parser.parse_args()

    asyncio.run(crawl(args.url, args.output))
