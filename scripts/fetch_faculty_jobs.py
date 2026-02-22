#!/usr/bin/env python3
"""
Fetch faculty job postings from sources with working APIs/feeds.
Sources: GitHub CS faculty job wikis, RSS feeds that work, curated boards.
"""

import json
import urllib.request
import urllib.parse
import re
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

KEYWORDS_RE = re.compile(
    r"human.?computer|HCI|artificial.?intelligence|machine.?learning|NLP|natural.?language|"
    r"software.?engineering|data.?science|computer.?science|interactive|UX|human.?AI|"
    r"information.?science|intelligent.?systems",
    re.IGNORECASE
)


def fetch_url(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ Failed: {url}: {e}")
        return None


def detect_region(text):
    text_lower = text.lower()
    checks = [
        ("🇺🇸 US", ["united states", "usa", " us,", "california", "new york", "texas", "massachusetts",
                      "georgia", "illinois", "washington", "virginia", "pennsylvania", "ohio", "michigan",
                      "carnegie mellon", "mit ", "stanford", "berkeley", "cornell", "georgia tech",
                      "purdue", "umich", "ucla", "uiuc"]),
        ("🇬🇧 UK", ["united kingdom", " uk,", " uk ", "england", "london", "oxford", "cambridge", "imperial college", "edinburgh"]),
        ("🇨🇦 Canada", ["canada", "toronto", "waterloo", "montreal", "vancouver", "british columbia"]),
        ("🇩🇪 Germany", ["germany", "munich", "berlin", "max planck"]),
        ("🇨🇭 Switzerland", ["switzerland", "zurich", "eth ", "epfl"]),
        ("🇳🇱 Netherlands", ["netherlands", "amsterdam", "delft", "eindhoven", "twente"]),
        ("🇸🇬 Singapore", ["singapore", "nus ", "ntu ", "smu ", "sutd"]),
        ("🇭🇰 Hong Kong", ["hong kong", "hku", "cuhk", "hkust", "polyu", "cityu"]),
        ("🇦🇺 Australia", ["australia", "sydney", "melbourne", "queensland", "monash"]),
        ("🇪🇺 Europe", ["europe", "france", "italy", "spain", "sweden", "denmark", "norway", "finland"]),
        ("🌏 Asia", ["china", "japan", "korea", "taiwan", "india", "beijing", "shanghai", "tokyo", "seoul"]),
    ]
    for region, keywords in checks:
        for kw in keywords:
            if kw in text_lower:
                return region
    return "🌐 Global"


def fetch_github_cs_wiki():
    """Fetch from the CS faculty job market wiki on GitHub (community-maintained)."""
    print("📡 Fetching GitHub CS Job Wiki...")
    # Try the well-known CS faculty jobs repo
    year = datetime.now().year
    urls = [
        f"https://raw.githubusercontent.com/academic-cs-jobs/cs-faculty-jobs-{year}/main/README.md",
        f"https://raw.githubusercontent.com/jxmorris12/cs-faculty-jobs-{year}/main/README.md",
    ]
    
    jobs = []
    for url in urls:
        content = fetch_url(url)
        if not content:
            continue
        
        print(f"  ✓ Found wiki at {url}")
        # Parse markdown table rows: | University | Area | Rank | Deadline | Link |
        lines = content.split("\n")
        for line in lines:
            if "|" not in line or line.strip().startswith("|--") or line.strip().startswith("| ---"):
                continue
            cells = [c.strip() for c in line.split("|")]
            cells = [c for c in cells if c]  # remove empty
            if len(cells) < 3:
                continue
            
            # Try to extract info
            university = cells[0] if cells else ""
            # Skip header rows
            if "university" in university.lower() or "institution" in university.lower():
                continue
            
            # Find URL in cells
            url_match = re.search(r'\[([^\]]+)\]\(([^)]+)\)', line)
            link = url_match.group(2) if url_match else ""
            name = url_match.group(1) if url_match else university
            
            area = cells[1] if len(cells) > 1 else ""
            deadline = ""
            for c in cells:
                if re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\w+ \d{1,2},? \d{4}', c):
                    deadline = c
                    break
            
            full_text = " ".join(cells)
            region = detect_region(full_text)
            
            # Filter for relevant areas
            if not KEYWORDS_RE.search(full_text) and "cs" not in area.lower():
                continue
            
            summary = f"{area}" + (f" | Deadline: {deadline}" if deadline else "")
            
            jobs.append({
                "title": f"{name} — Faculty Position",
                "link": link or f"https://github.com/academic-cs-jobs/cs-faculty-jobs-{year}",
                "source": "faculty_jobs",
                "summary": summary[:300],
                "region": region,
                "origin": "GitHub Wiki",
                "ts": int(datetime.now().timestamp()),
            })
        
        if jobs:
            break  # Found a working source
    
    print(f"  ✓ GitHub Wiki: {len(jobs)} relevant jobs")
    return jobs


def fetch_csrankings_jobs():
    """Fetch from CSRankings job board."""
    print("📡 Fetching CSRankings Jobs...")
    url = "https://drafty.cs.brown.edu/csopenpositions/"
    content = fetch_url(url)
    if not content:
        return []
    
    jobs = []
    # Parse the HTML table
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) < 3:
            continue
        
        # Clean HTML
        def clean(s):
            return re.sub(r'<[^>]+>', '', s).strip()
        
        institution = clean(cells[0])
        if "institution" in institution.lower() or not institution:
            continue
        
        # Find link
        link_match = re.search(r'href="([^"]+)"', row)
        link = link_match.group(1) if link_match else ""
        
        area = clean(cells[1]) if len(cells) > 1 else ""
        rank = clean(cells[2]) if len(cells) > 2 else ""
        deadline = clean(cells[3]) if len(cells) > 3 else ""
        
        full_text = f"{institution} {area} {rank}"
        region = detect_region(full_text)
        
        summary = f"{rank} position" if rank else "Faculty position"
        if area:
            summary += f" in {area}"
        if deadline:
            summary += f" | Deadline: {deadline}"
        
        jobs.append({
            "title": f"{institution} — {summary[:80]}",
            "link": link if link.startswith("http") else f"https://drafty.cs.brown.edu{link}" if link else "https://drafty.cs.brown.edu/csopenpositions/",
            "source": "faculty_jobs",
            "summary": summary[:300],
            "region": region,
            "origin": "CSRankings",
            "ts": int(datetime.now().timestamp()),
        })
    
    print(f"  ✓ CSRankings: {len(jobs)} jobs")
    return jobs


def get_curated_job_boards():
    """Static list of useful job boards for faculty search."""
    return [
        {
            "title": "🔍 CRA Career Center — CS Faculty Positions",
            "link": "https://careercenter.cra.org/?s=&post_type=job_listing&search_category%5B%5D=faculty",
            "source": "faculty_jobs",
            "summary": "Computing Research Association job board. Largest source of CS academic positions in North America.",
            "region": "🇺🇸 US",
            "origin": "Board",
            
            "ts": int(datetime.now().timestamp()),
        },
        {
            "title": "🔍 HigherEdJobs — CS & IT Faculty",
            "link": "https://www.higheredjobs.com/faculty/search.cfm?JobCat=93",
            "source": "faculty_jobs",
            "summary": "Large US-focused academic job board. Filter by Computer Science / IT category.",
            "region": "🇺🇸 US",
            "origin": "Board",
            
            "ts": int(datetime.now().timestamp()),
        },
        {
            "title": "🔍 AcademicJobsOnline — Computer Science",
            "link": "https://academicjobsonline.org/ajo/jobs?department=Computer+Science",
            "source": "faculty_jobs",
            "summary": "Global academic recruitment platform. Strong for R1 universities.",
            "region": "🌐 Global",
            "origin": "Board",
            
            "ts": int(datetime.now().timestamp()),
        },
        {
            "title": "🔍 Times Higher Education — CS Academic Jobs",
            "link": "https://www.timeshighereducation.com/unijobs/en/listing/computer-science/",
            "source": "faculty_jobs",
            "summary": "Global academic job listings, strong for UK/Europe/Asia/Australia.",
            "region": "🌐 Global",
            "origin": "Board",
            
            "ts": int(datetime.now().timestamp()),
        },
        {
            "title": "🔍 jobs.ac.uk — CS & IT",
            "link": "https://www.jobs.ac.uk/search/?activeFacet=subjectFacet&subjectFacet%5B0%5D=Computing+%26+IT",
            "source": "faculty_jobs",
            "summary": "UK & Ireland academic positions. Best source for British universities.",
            "region": "🇬🇧 UK",
            "origin": "Board",
            
            "ts": int(datetime.now().timestamp()),
        },
        {
            "title": "🔍 EuroScienceJobs — Computer Science",
            "link": "https://www.eurosciencejobs.com/jobs/computer_science",
            "source": "faculty_jobs",
            "summary": "European academic positions across all countries.",
            "region": "🇪🇺 Europe",
            "origin": "Board",
            
            "ts": int(datetime.now().timestamp()),
        },
        {
            "title": "🔍 CSRankings Open Positions",
            "link": "https://drafty.cs.brown.edu/csopenpositions/",
            "source": "faculty_jobs",
            "summary": "Community-maintained list of CS open positions worldwide. Updated frequently by the community.",
            "region": "🌐 Global",
            "origin": "Board",
            
            "ts": int(datetime.now().timestamp()),
        },
        {
            "title": "🔍 GitHub CS Faculty Jobs Wiki 2026",
            "link": "https://github.com/academic-cs-jobs",
            "source": "faculty_jobs",
            "summary": "Community-maintained spreadsheet/wiki tracking CS faculty openings and their status.",
            "region": "🌐 Global",
            "origin": "Board",
            
            "ts": int(datetime.now().timestamp()),
        },
    ]


def deduplicate(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = re.sub(r'\W+', '', job["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def main():
    print("🎓 Fetching Faculty Job Postings...\n")
    
    all_jobs = []
    all_jobs.extend(fetch_csrankings_jobs())
    all_jobs.extend(fetch_github_cs_wiki())
    
    # Add curated job board links
    all_jobs.extend(get_curated_job_boards())
    
    all_jobs = deduplicate(all_jobs)
    
    # Add matched_keyword from region for filtering in dashboard
    for job in all_jobs:
        if "matched_keyword" not in job:
            job["matched_keyword"] = job.get("region", "🌐 Global")
    
    # Sort: boards last, actual listings first
    all_jobs.sort(key=lambda j: (j.get("origin") == "Board", -j.get("ts", 0)))
    
    print(f"\n✅ Total: {len(all_jobs)} faculty jobs")
    
    regions = {}
    for j in all_jobs:
        r = j.get("region", "Unknown")
        regions[r] = regions.get(r, 0) + 1
    for r, c in sorted(regions.items(), key=lambda x: -x[1]):
        print(f"  {r}: {c}")
    
    # Update latest.json
    latest_path = DATA_DIR / "latest.json"
    data = json.loads(latest_path.read_text()) if latest_path.exists() else {}
    data["faculty_jobs"] = all_jobs
    latest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    
    print(f"\n📁 Saved to {latest_path}")
    return all_jobs


if __name__ == "__main__":
    main()
