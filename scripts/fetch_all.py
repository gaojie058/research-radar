#!/usr/bin/env python3
"""
Research Radar - Automated research signal aggregator
Fetches from: arXiv, Semantic Scholar, HackerNews, Reddit
Outputs: JSON data files for dashboard consumption
"""

import json
import os
import time
import re
from datetime import datetime, timedelta
from pathlib import Path
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_MD = PROJECT_ROOT / "information-source.md"


# ─── Configuration (loaded from information-source.md) ───────────────────────

def load_config():
    """Parse the '自动扫描配置' section from information-source.md."""
    config = {
        "arxiv_categories": ["cs.HC", "cs.AI", "cs.CL", "cs.SE"],
        "arxiv_keywords": ["human-AI collaboration", "LLM agent", "AI agent"],
        "s2_keywords": ["human-AI collaboration LLM"],
        "tracked_authors": {},
        "hn_keywords": ["AI agent", "LLM agent", "human-AI"],
        "reddit_subreddits": ["MachineLearning", "artificial", "LocalLLaMA"],
        "reddit_keywords": ["agent", "human-AI", "agentic"],
        "days_lookback": 7,
        "bluesky_keywords": ["human-AI collaboration", "coding agents", "LLM agent", "HCI research"],
        "bluesky_handles": ["janchristianblaise.bsky.social", "hci.bsky.social"],
    }

    if not CONFIG_MD.exists():
        print(f"  ⚠ Config file not found ({CONFIG_MD}), using defaults")
        return config

    text = CONFIG_MD.read_text(encoding="utf-8")

    # Only parse the "自动扫描配置" section
    marker = "## 九、Research Radar 自动扫描配置"
    idx = text.find(marker)
    if idx == -1:
        print("  ⚠ Config section not found in markdown, using defaults")
        return config
    section = text[idx:]

    def extract_section(heading):
        """Extract content between a ### heading and the next ### or ## heading."""
        pattern = rf"### {re.escape(heading)}\s*\n(.*?)(?=\n###|\n##|\Z)"
        m = re.search(pattern, section, re.DOTALL)
        return m.group(1).strip() if m else ""

    def parse_bullet_list(block):
        """Parse '- item' lines into a list."""
        return [line.lstrip("- ").strip() for line in block.splitlines()
                if line.strip().startswith("- ")]

    def parse_comma_list(block):
        """Parse comma-separated values."""
        return [item.strip() for item in block.split(",") if item.strip()]

    def parse_link_table(block):
        """Parse a markdown table into a list of dicts."""
        items = []
        headers = None
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if not cells:
                continue
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if headers is None:
                headers = cells
            else:
                row = {}
                for i, h in enumerate(headers):
                    if i < len(cells):
                        row[h] = cells[i]
                items.append(row)
        return items

    # arXiv categories
    raw = extract_section("arXiv 分类")
    if raw:
        config["arxiv_categories"] = parse_comma_list(raw)

    # arXiv keywords
    raw = extract_section("arXiv 关键词")
    if raw:
        config["arxiv_keywords"] = parse_bullet_list(raw)

    # Semantic Scholar keywords
    raw = extract_section("Semantic Scholar 关键词")
    if raw:
        config["s2_keywords"] = parse_bullet_list(raw)

    # Tracked authors (table: | Name | ID |)
    raw = extract_section("Semantic Scholar 跟踪作者")
    if raw:
        authors = {}
        for line in raw.splitlines():
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2 and parts[1].isdigit():
                authors[parts[0]] = parts[1]
        if authors:
            config["tracked_authors"] = authors

    # HackerNews keywords
    raw = extract_section("HackerNews 关键词")
    if raw:
        config["hn_keywords"] = parse_bullet_list(raw)

    # Reddit subreddits
    raw = extract_section("Reddit 子版块")
    if raw:
        config["reddit_subreddits"] = [
            s.strip().lstrip("r/") for s in raw.split(",") if s.strip()
        ]

    # Reddit filter keywords
    raw = extract_section("Reddit 过滤关键词")
    if raw:
        config["reddit_keywords"] = parse_comma_list(raw)

    # Bluesky keywords
    raw = extract_section("Bluesky 关键词")
    if raw:
        config["bluesky_keywords"] = parse_bullet_list(raw)

    # Bluesky tracked handles
    raw = extract_section("Bluesky 跟踪账号")
    if raw:
        config["bluesky_handles"] = parse_comma_list(raw)

    # Days lookback
    raw = extract_section("时间窗口")
    if raw:
        try:
            config["days_lookback"] = int(raw.splitlines()[0].strip())
        except ValueError:
            pass

    # Static link directories
    config["blogs"] = parse_link_table(extract_section("公司/实验室博客"))
    config["newsletters"] = parse_link_table(extract_section("Newsletter / 个人博客"))
    config["researchers"] = parse_link_table(extract_section("推荐关注"))
    config["seminars"] = parse_link_table(extract_section("Seminars/Talks"))
    config["podcasts"] = parse_link_table(extract_section("播客"))
    config["conferences"] = parse_link_table(extract_section("会议/Workshop"))
    config["opportunities"] = parse_link_table(extract_section("Career/Grant Opportunities"))

    return config


# Load config at module level
CFG = load_config()
ARXIV_CATEGORIES = CFG["arxiv_categories"]
ARXIV_KEYWORDS = CFG["arxiv_keywords"]
SEMANTIC_SCHOLAR_KEYWORDS = CFG["s2_keywords"]
TRACKED_AUTHORS = CFG["tracked_authors"]
HN_KEYWORDS = CFG["hn_keywords"]
REDDIT_SUBREDDITS = CFG["reddit_subreddits"]
REDDIT_KEYWORDS = CFG["reddit_keywords"]
DAYS_LOOKBACK = CFG["days_lookback"]
BLUESKY_KEYWORDS = CFG.get("bluesky_keywords", ["human-AI collaboration", "coding agents", "LLM agent", "HCI research"])
BLUESKY_HANDLES = CFG.get("bluesky_handles", ["janchristianblaise.bsky.social", "hci.bsky.social"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def safe_request(url, headers=None, max_retries=3, delay=2):
    """Make HTTP request with retries and rate limiting."""
    if headers is None:
        headers = {"User-Agent": "ResearchRadar/1.0 (academic research tool)"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                print(f"  ✗ Failed after {max_retries} attempts: {url[:80]}... — {e}")
                return None


def keyword_match(text, keywords):
    """Check if any keyword appears in text (case-insensitive)."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


# ─── arXiv ───────────────────────────────────────────────────────────────────

def fetch_arxiv():
    """Fetch recent papers from arXiv matching our categories and keywords."""
    print("📄 Fetching arXiv papers...")
    results = []
    seen_ids = set()

    for keyword in ARXIV_KEYWORDS:
        # Search across our categories with this keyword
        cat_query = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
        query = f'({cat_query}) AND all:"{keyword}"'
        url = (
            f"http://export.arxiv.org/api/query?search_query={urllib.parse.quote(query)}"
            f"&start=0&max_results=20&sortBy=submittedDate&sortOrder=descending"
        )

        xml_data = safe_request(url)
        if not xml_data:
            continue

        root = ET.fromstring(xml_data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        for entry in root.findall("atom:entry", ns):
            arxiv_id = entry.find("atom:id", ns).text.split("/abs/")[-1]
            if arxiv_id in seen_ids:
                continue
            seen_ids.add(arxiv_id)

            title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
            summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
            published = entry.find("atom:published", ns).text[:10]
            authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)]
            categories = [c.get("term") for c in entry.findall("atom:category", ns)]
            link = f"https://arxiv.org/abs/{arxiv_id}"

            # Check if within lookback window
            pub_date = datetime.strptime(published, "%Y-%m-%d")
            if pub_date < datetime.now() - timedelta(days=DAYS_LOOKBACK + 2):
                continue

            results.append({
                "id": arxiv_id,
                "title": title,
                "authors": authors[:5],  # Limit to first 5
                "summary": summary[:500],
                "published": published,
                "categories": categories[:5],
                "link": link,
                "matched_keyword": keyword,
                "source": "arxiv",
            })

        time.sleep(3)  # Be nice to arXiv API

    # Deduplicate and sort by date
    results.sort(key=lambda x: x["published"], reverse=True)
    print(f"  ✓ Found {len(results)} arXiv papers")
    return results


# ─── Semantic Scholar ────────────────────────────────────────────────────────

def fetch_semantic_scholar():
    """Fetch papers from Semantic Scholar by keywords and tracked authors."""
    print("🔬 Fetching Semantic Scholar papers...")
    results = []
    seen_ids = set()
    api_key = os.environ.get("S2_API_KEY", "")
    headers = {"User-Agent": "ResearchRadar/1.0"}
    if api_key:
        headers["x-api-key"] = api_key

    # Keyword search
    for keyword in SEMANTIC_SCHOLAR_KEYWORDS:
        url = (
            f"https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={urllib.parse.quote(keyword)}"
            f"&limit=10&fields=title,authors,abstract,year,url,publicationDate,citationCount"
            f"&year={datetime.now().year}-{datetime.now().year}"
        )

        data = safe_request(url, headers=headers)
        if not data:
            continue

        try:
            parsed = json.loads(data)
            for paper in parsed.get("data", []):
                pid = paper.get("paperId", "")
                if pid in seen_ids or not pid:
                    continue
                seen_ids.add(pid)

                pub_date = paper.get("publicationDate", "")
                if pub_date:
                    try:
                        pd = datetime.strptime(pub_date, "%Y-%m-%d")
                        if pd < datetime.now() - timedelta(days=DAYS_LOOKBACK * 4):
                            continue
                    except ValueError:
                        pass

                results.append({
                    "id": pid,
                    "title": paper.get("title", ""),
                    "authors": [a["name"] for a in paper.get("authors", [])[:5]],
                    "summary": (paper.get("abstract") or "")[:500],
                    "published": pub_date or str(datetime.now().year),
                    "citations": paper.get("citationCount", 0),
                    "link": paper.get("url", ""),
                    "matched_keyword": keyword,
                    "source": "semantic_scholar",
                })
        except json.JSONDecodeError:
            pass

        time.sleep(1)

    # Tracked authors - recent papers
    for author_name, author_id in TRACKED_AUTHORS.items():
        url = (
            f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers"
            f"?limit=5&fields=title,authors,abstract,year,url,publicationDate,citationCount"
        )
        data = safe_request(url, headers=headers)
        if not data:
            continue

        try:
            parsed = json.loads(data)
            for paper in parsed.get("data", []):
                pid = paper.get("paperId", "")
                if pid in seen_ids or not pid:
                    continue
                seen_ids.add(pid)

                pub_date = paper.get("publicationDate", "")
                if pub_date:
                    try:
                        pd = datetime.strptime(pub_date, "%Y-%m-%d")
                        if pd < datetime.now() - timedelta(days=60):
                            continue
                    except ValueError:
                        pass

                results.append({
                    "id": pid,
                    "title": paper.get("title", ""),
                    "authors": [a["name"] for a in paper.get("authors", [])[:5]],
                    "summary": (paper.get("abstract") or "")[:500],
                    "published": pub_date or str(datetime.now().year),
                    "citations": paper.get("citationCount", 0),
                    "link": paper.get("url", ""),
                    "matched_keyword": f"author:{author_name}",
                    "source": "semantic_scholar",
                })
        except json.JSONDecodeError:
            pass

        time.sleep(1)

    results.sort(key=lambda x: x.get("published", ""), reverse=True)
    print(f"  ✓ Found {len(results)} Semantic Scholar papers")
    return results


# ─── HackerNews ──────────────────────────────────────────────────────────────

def fetch_hackernews():
    """Fetch relevant HN stories using Algolia search API."""
    print("🟠 Fetching HackerNews stories...")
    results = []
    seen_ids = set()
    cutoff = int((datetime.now() - timedelta(days=DAYS_LOOKBACK)).timestamp())

    for keyword in HN_KEYWORDS:
        url = (
            f"https://hn.algolia.com/api/v1/search?"
            f"query={urllib.parse.quote(keyword)}"
            f"&tags=story&numericFilters=created_at_i>{cutoff}"
            f"&hitsPerPage=10"
        )

        data = safe_request(url)
        if not data:
            continue

        try:
            parsed = json.loads(data)
            for hit in parsed.get("hits", []):
                hid = hit.get("objectID", "")
                if hid in seen_ids:
                    continue
                seen_ids.add(hid)

                results.append({
                    "id": hid,
                    "title": hit.get("title", ""),
                    "link": hit.get("url") or f"https://news.ycombinator.com/item?id={hid}",
                    "hn_link": f"https://news.ycombinator.com/item?id={hid}",
                    "points": hit.get("points", 0),
                    "comments": hit.get("num_comments", 0),
                    "published": hit.get("created_at", "")[:10],
                    "matched_keyword": keyword,
                    "source": "hackernews",
                })
        except json.JSONDecodeError:
            pass

        time.sleep(0.5)

    results.sort(key=lambda x: x.get("points", 0), reverse=True)
    print(f"  ✓ Found {len(results)} HN stories")
    return results


# ─── Reddit ──────────────────────────────────────────────────────────────────

def fetch_reddit():
    """Fetch relevant Reddit posts."""
    print("🔴 Fetching Reddit posts...")
    results = []
    seen_ids = set()

    for subreddit in REDDIT_SUBREDDITS:
        url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=50"
        headers = {"User-Agent": "ResearchRadar/1.0 (academic research aggregator)"}

        data = safe_request(url, headers=headers)
        if not data:
            continue

        try:
            parsed = json.loads(data)
            for child in parsed.get("data", {}).get("children", []):
                post = child.get("data", {})
                rid = post.get("id", "")
                if rid in seen_ids:
                    continue

                title = post.get("title", "")
                selftext = post.get("selftext", "")
                combined = f"{title} {selftext}"

                if not keyword_match(combined, REDDIT_KEYWORDS):
                    continue

                seen_ids.add(rid)

                created = datetime.fromtimestamp(post.get("created_utc", 0))
                if created < datetime.now() - timedelta(days=DAYS_LOOKBACK):
                    continue

                results.append({
                    "id": rid,
                    "title": title,
                    "subreddit": subreddit,
                    "link": f"https://reddit.com{post.get('permalink', '')}",
                    "score": post.get("score", 0),
                    "comments": post.get("num_comments", 0),
                    "published": created.strftime("%Y-%m-%d"),
                    "matched_keyword": next(
                        (kw for kw in REDDIT_KEYWORDS if kw.lower() in combined.lower()),
                        ""
                    ),
                    "source": "reddit",
                })
        except json.JSONDecodeError:
            pass

        time.sleep(2)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    print(f"  ✓ Found {len(results)} Reddit posts")
    return results


# ─── Bluesky ─────────────────────────────────────────────────────────────────

def fetch_bluesky():
    """Fetch posts from Bluesky via public API (no auth required)."""
    print("🦋 Fetching Bluesky posts...")
    results = []
    seen_uris = set()

    def parse_post(post, matched_keyword):
        uri = post.get("uri", "")
        if not uri or uri in seen_uris:
            return None
        seen_uris.add(uri)
        author = post.get("author", {})
        handle = author.get("handle", "")
        rkey = uri.split("/")[-1]
        link = f"https://bsky.app/profile/{handle}/post/{rkey}"
        record = post.get("record", {})
        text = record.get("text", "")
        created_at = record.get("createdAt", "")
        published = created_at[:10] if created_at else ""
        if published:
            try:
                pub_date = datetime.strptime(published, "%Y-%m-%d")
                if pub_date < datetime.now() - timedelta(days=DAYS_LOOKBACK):
                    return None
            except ValueError:
                pass
        return {
            "id": uri,
            "title": text[:120] + ("..." if len(text) > 120 else ""),
            "authors": [handle],
            "summary": text[:500],
            "published": published,
            "likes": post.get("likeCount", 0),
            "reposts": post.get("repostCount", 0),
            "link": link,
            "matched_keyword": matched_keyword,
            "source": "bluesky",
        }

    # Keyword search
    for keyword in BLUESKY_KEYWORDS:
        url = (
            f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
            f"?q={urllib.parse.quote(keyword)}&limit=10"
        )
        data = safe_request(url)
        if not data:
            continue
        try:
            parsed = json.loads(data)
            for post in parsed.get("posts", []):
                item = parse_post(post, keyword)
                if item:
                    results.append(item)
        except json.JSONDecodeError:
            pass
        time.sleep(0.5)

    # Fetch from tracked handles
    for handle in BLUESKY_HANDLES:
        url = (
            f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
            f"?actor={urllib.parse.quote(handle)}&limit=10"
        )
        data = safe_request(url)
        if not data:
            continue
        try:
            parsed = json.loads(data)
            for feed_item in parsed.get("feed", []):
                post = feed_item.get("post", {})
                item = parse_post(post, f"handle:{handle}")
                if item:
                    results.append(item)
        except json.JSONDecodeError:
            pass
        time.sleep(0.5)

    results.sort(key=lambda x: x.get("published", ""), reverse=True)
    print(f"  ✓ Found {len(results)} Bluesky posts")
    return results


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  Research Radar — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    all_data = {
        "meta": {
            "fetched_at": datetime.now().isoformat(),
            "lookback_days": DAYS_LOOKBACK,
            "keywords": ARXIV_KEYWORDS,
        },
        "arxiv": fetch_arxiv(),
        "semantic_scholar": fetch_semantic_scholar(),
        "hackernews": fetch_hackernews(),
        "reddit": fetch_reddit(),
        "bluesky": fetch_bluesky(),
        "blogs": CFG["blogs"],
        "newsletters": CFG["newsletters"],
        "researchers": CFG["researchers"],
        "seminars": CFG.get("seminars", []),
        "podcasts": CFG["podcasts"],
        "conferences": CFG["conferences"],
        "opportunities": CFG.get("opportunities", []),
    }

    # Fetch faculty jobs
    try:
        from fetch_faculty_jobs import main as fetch_faculty
        faculty_jobs = fetch_faculty()
        all_data["faculty_jobs"] = faculty_jobs
    except Exception as e:
        print(f"⚠ Faculty jobs fetch failed: {e}")
        all_data["faculty_jobs"] = []

    # Write combined data
    output_path = DATA_DIR / "latest.json"
    with open(output_path, "w") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)

    # Also write a dated archive
    date_str = datetime.now().strftime("%Y-%m-%d")
    archive_path = DATA_DIR / f"archive-{date_str}.json"
    with open(archive_path, "w") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)

    total = sum(len(all_data[k]) for k in ["arxiv", "semantic_scholar", "hackernews", "reddit", "bluesky"])
    print(f"\n✅ Done! Total items: {total}")
    print(f"   Output: {output_path}")


if __name__ == "__main__":
    main()
