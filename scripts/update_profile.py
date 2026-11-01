"""Refresh this profile using public GitHub data and an optional RSS/Atom feed.

Python standard library only. Optional GITHUB_TOKEN authenticates GitHub requests
only; it is never sent to the blog feed.
"""

import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"


def get(url, github=False):
    headers = {"User-Agent": "ae7hr-profile-updater", "Accept": "application/json" if github else "application/xml"}
    if github and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("Response is too large")
    return json.loads(body) if github else body


def replace_block(text, key, body):
    pattern = rf"(<!-- {key}:START -->).*?(<!-- {key}:END -->)"
    if len(re.findall(pattern, text, flags=re.S)) != 1:
        raise ValueError(f"Expected one {key} marker pair")
    return re.sub(pattern, lambda m: m[1] + "\n" + body.strip() + "\n" + m[2], text, flags=re.S)


def stats_svg(stats):
    x = 0
    parts = []
    for label, value in stats:
        lw, vw = len(label) * 6.8 + 14, max(25, len(str(value)) * 7 + 14)
        parts.append(f'<g transform="translate({x},0)"><rect width="{lw + vw}" height="21" rx="3" fill="#555"/><path d="M{lw} 0h{vw - 3}q3 0 3 3v15q0 3-3 3h-{vw - 3}z" fill="#63a80a"/><text x="{lw / 2}" y="14.5">{html.escape(label)}</text><text x="{lw + vw / 2}" y="14.5">{html.escape(str(value))}</text></g>')
        x += lw + vw + 6
    return f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Public GitHub statistics" width="{x - 6}" height="21" viewBox="0 0 {x - 6} 21"><title>Public GitHub statistics</title><g font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" text-anchor="middle" fill="white">' + "".join(parts) + '</g></svg>\n'


def fetch_stats(username, now):
    profile = get(f"{API}/users/{username}", github=True)
    repos = []
    page = 1
    while True:
        batch = get(f"{API}/users/{username}/repos?type=owner&per_page=100&page={page}", github=True)
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    created = dt.datetime.fromisoformat(profile["created_at"].replace("Z", "+00:00")).date()
    years = now.year - created.year - ((now.month, now.day) < (created.month, created.day))
    stats = [("Repos", profile["public_repos"]), ("Years", years)]
    query = urllib.parse.urlencode({"q": f"author:{username} author-date:{now:%Y-%m}-01..{now:%Y-%m-%d}", "per_page": 1})
    try:
        commits = get(f"{API}/search/commits?{query}", github=True)
        if commits.get("incomplete_results"):
            raise ValueError("GitHub commit search returned incomplete results")
        stats.append(("Public commits this month", commits["total_count"]))
    except (OSError, ValueError) as exc:
        print(f"Commit count unavailable: {exc}")
        stats.append(("Public commits this month", "n/a"))
    stats.append(("Stars", sum(r["stargazers_count"] for r in repos if not r["fork"])))
    return stats_svg(stats)


def local_name(element):
    return element.tag.rsplit("}", 1)[-1]


def feed_posts(url, limit):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("feed_url must be an HTTPS URL without credentials")
    root = ET.fromstring(get(url))
    posts = []
    for item in root.iter():
        if local_name(item) not in ("item", "entry"):
            continue
        title, link = "", ""
        for child in item:
            tag = local_name(child)
            if tag == "title":
                title = " ".join("".join(child.itertext()).split())[:250]
            if tag == "link" and child.attrib.get("rel", "alternate") == "alternate":
                link = child.attrib.get("href", child.text or "").strip()
        resolved = urllib.parse.urljoin(url, link)
        target = urllib.parse.urlsplit(resolved)
        if title and link and target.scheme in ("http", "https") and target.netloc and not target.username and not target.password:
            safe_title = html.escape(title)
            safe_link = html.escape(resolved, quote=True)
            posts.append(f'<li><a href="{safe_link}">{safe_title}</a></li>')
        if len(posts) >= limit:
            break
    return "<ul>\n" + "\n".join(posts) + "\n</ul>" if posts else "No posts to show yet."


def main():
    config = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    username = config["username"]
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", username):
        raise ValueError("Invalid GitHub username")
    now = dt.datetime.now(dt.timezone.utc).date()
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    # Resolve all mandatory data before writing any files.
    svg = fetch_stats(username, now)
    readme = replace_block(readme, "STATS", f'<img src="assets/stats.svg" alt="Public GitHub statistics for {username}" />\n\n<sub>Updated {now.isoformat()} · public data only · commit search may lag.</sub>')
    if config.get("feed_url"):
        try:
            posts = feed_posts(config["feed_url"], max(1, min(int(config.get("post_limit", 5)), 10)))
            readme = replace_block(readme, "POSTS", posts)
        except (OSError, ValueError, ET.ParseError) as exc:
            # Preserve the last successful list when a feed is temporarily down.
            print(f"Keeping previous posts: {exc}")
    quotes = config.get("quotes", [])
    quote = ""
    if config.get("quote_enabled") and quotes:
        quote = "### 📣 Quote of the Day\n\n> " + " ".join(str(quotes[now.toordinal() % len(quotes)]).split())
    readme = replace_block(readme, "QUOTE", quote)
    (ROOT / "assets" / "stats.svg").write_text(svg, encoding="utf-8")
    readme_path.write_text(readme, encoding="utf-8")
    print("Updated public profile successfully.")


if __name__ == "__main__":
    main()
