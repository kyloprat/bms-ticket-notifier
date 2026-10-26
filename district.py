"""District (district.in) movie pages: link parsing and 'are showtimes listed yet?'.

District pages embed their data as __NEXT_DATA__ JSON. For a movie with bookings open,
`languageFormatGroups[].number_of_sessions` is positive; before that it is absent/zero
and `isReleased` is false. NOTE: district.in answers GitHub's servers with HTTP 403, so
this only works from a network District accepts (e.g. a home device).
"""
from __future__ import annotations

import json
import re

import requests

DISTRICT_LINK = re.compile(
    r"https?://(?:www\.)?district\.in/movies/(?P<slug>[a-z0-9-]+?)-movie-tickets"
    r"(?:-in-(?P<city>[a-z0-9-]+?))?-(?P<mv>MV\d+)", re.I)
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")


def parse_district_link(text: str) -> dict | None:
    m = DISTRICT_LINK.search(text or "")
    if not m:
        return None
    return {"slug": m["slug"].lower(), "city": (m["city"] or "").lower(), "mv": m["mv"].upper()}


def district_url(link: dict) -> str:
    city = f"-in-{link['city']}" if link["city"] else ""
    return f"https://www.district.in/movies/{link['slug']}-movie-tickets{city}-{link['mv']}"


def sessions_in_page(html: str) -> tuple[int, str]:
    """(total sessions listed, release date or '') from a District movie page."""
    m = NEXT_DATA.search(html)
    if not m:
        raise ValueError("no page data found")
    raw = m.group(1)
    json.loads(raw)                                    # make sure it really is JSON
    total = sum(int(x) for x in re.findall(r'"number_of_sessions":\s*(\d+)', raw))
    rel = re.search(r'"release_date":\s*"([^"]*)"', raw)
    return total, rel.group(1) if rel else ""


def check_district(t: dict, get=requests.get) -> tuple[bool, str, bool]:
    """-> (got_data, result text, is_open)."""
    url = district_url({"slug": t["slug"], "city": t["city"], "mv": t["mv"]})
    try:
        r = get(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}, timeout=30)
    except requests.RequestException as e:
        return False, f"District unreachable ({type(e).__name__})", False
    if r.status_code != 200:
        return False, f"District blocked this network (HTTP {r.status_code})", False
    try:
        total, rel = sessions_in_page(r.text)
    except ValueError as e:
        return False, f"District page not readable ({e})", False
    if total > 0:
        return True, f"{total} sessions listed", True
    return True, "no showtimes listed yet" + (f" (release {rel})" if rel else ""), False
