---
name: agent-reach
description: Search the web, scrape complex web pages, extract YouTube transcripts, search GitHub, and scrape social platforms (Twitter, Reddit, Facebook, Instagram, V2EX, Xueqiu) with zero API costs using Agent-Reach.
---

# Agent Reach Skill

Use Agent-Reach to give the assistant live internet searching, web scraping, video transcription, and social media browsing capabilities without paid API subscriptions.

Agent Reach venv path: `C:\Users\mrinm\.agent-reach-venv\Scripts\`

---

## 1. Web Page Reading (Jina Reader - Zero Config)
Extracts clean, reader-friendly Markdown from any URL (bypasses JavaScript rendering, cookie popups, and paywall banners):

```bash
curl -s "https://r.jina.ai/<TARGET_URL>"
```

*Example:*
```bash
curl -s "https://r.jina.ai/https://en.wikipedia.org/wiki/Algorithmic_trading"
```

---

## 2. Full-Web Semantic Search (Exa AI via mcporter)
Executes deep neural web search across the entire internet without an API key:

```bash
mcporter call exa.web_search_exa query="<SEARCH_QUERY>" numResults=5
```

---

## 3. YouTube Transcripts & Video Metadata (yt-dlp)
Extracts transcripts, subtitles, and metadata from YouTube videos:

```powershell
& "$env:USERPROFILE\.agent-reach-venv\Scripts\yt-dlp.exe" --write-sub --write-auto-sub --skip-download --sub-lang en -o "$env:TEMP\%(id)s" "<YOUTUBE_URL>"
Get-Content "$env:TEMP\<VIDEO_ID>.en.vtt"
```

---

## 4. GitHub Code & Repository Search (gh CLI)
Search repositories, code, issues, and PRs:

```bash
gh search repos "<query>" --sort stars --limit 10
gh search code "<query>" --repo <owner/repo>
```

---

## 5. Social & Community Platforms (OpenCLI / rdt-cli / twitter-cli)
Reuses active browser sessions or Cookie-Editor tokens to search social platforms without 403 blocks or paid API tiers:

### Reddit
```bash
opencli reddit search "<query>" -f yaml
# Or via rdt:
rdt search "<query>" --limit 10
```

### Twitter / X
```bash
# Requires cookie configured via: & "$env:USERPROFILE\.agent-reach-venv\Scripts\agent-reach.exe" configure twitter-cookies
twitter search "<query>" -n 10
```

### Facebook & Instagram (Desktop Chrome Session)
```bash
opencli facebook search "<query>" -f yaml
opencli instagram search "<query>" -f yaml
```

### Tech & Finance Forums (V2EX / Xueqiu)
```bash
# V2EX Hot Topics (Zero Config Public API)
curl -s "https://www.v2ex.com/api/topics/hot.json" -H "User-Agent: agent-reach/1.0"
```

---

## 6. Health & Diagnostics
To check channel status or add new platforms:

```powershell
& "$env:USERPROFILE\.agent-reach-venv\Scripts\agent-reach.exe" doctor
```
