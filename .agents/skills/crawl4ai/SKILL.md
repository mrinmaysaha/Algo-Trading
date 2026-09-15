---
name: crawl4ai
description: >-
  High-speed, open-source asynchronous web crawler engineered for LLMs. Converts complex JavaScript-heavy
  websites, news articles, exchange circulars, and technical documentation into clean, token-efficient Markdown.
---

# Crawl4AI Skill

Crawl4AI extracts clean Markdown from modern dynamic websites, SPA dashboards, and financial news without ads or layout clutter.

## When to Use

- When web pages require JavaScript rendering (dynamic charts, Angular/React tables).
- When scraping financial news, exchange circulars (NSE/BSE/MCX), or API documentation.
- When an article or page is too cluttered with headers/sidebars/ads for standard fetch tools.

## How to Run

Run via the configured python environment:

```powershell
& "C:\Users\mrinm\.agent-reach-venv\Scripts\python.exe" "c:\Users\mrinm\Algo_tading\openalgo\.agents\skills\crawl4ai\scripts\crawl.py" "https://example.com"
```

To save directly to a markdown file:
```powershell
& "C:\Users\mrinm\.agent-reach-venv\Scripts\python.exe" "c:\Users\mrinm\Algo_tading\openalgo\.agents\skills\crawl4ai\scripts\crawl.py" "https://example.com" -o "output.md"
```
