"""Telegram conversation: share a BookMyShow link, tune what to watch, manage trackers.

Send the bot a BookMyShow movie link. It starts tracking "bookings open" right away,
then offers buttons to narrow by dates, theatres and time of day.
Commands: /list  /pause N  /resume N  /remove N  /edit N  /help   (N = number from /list)
"""
from __future__ import annotations

import calendar
import re
from datetime import date

import requests

from district import district_url, parse_district_link

PERIODS = ["morning", "afternoon", "evening", "night"]
BMS_LINK = re.compile(r"https?://(?:www\.)?in\.bookmyshow\.com/movies/\S+", re.I)
EVENT_CODE = re.compile(r"ET\d{8,}", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9

HELP = (
    "Send me a BookMyShow movie link and I'll track it.\n"
    "I start with \"alert me when bookings open\", then you can narrow it by dates, "
    "theatres and time of day.\n\n"
    "/list - what I'm tracking\n"
    "/edit N - change dates, theatres or time of day\n"
    "/pause N, /resume N, /remove N\n"
    "N is the number shown in /list. Replies can take up to ~10 minutes."
)


# ── links ───────────────────────────────────────────────────────────────
def parse_link(text: str) -> dict | None:
    m = BMS_LINK.search(text or "")
    if not m:
        return None
    url = m.group(0).rstrip(").,>")
    path = [p for p in url.split("?")[0].split("#")[0].split("/")[3:] if p]   # movies/<city>/...
    code = next((p for p in path if EVENT_CODE.fullmatch(p)), None)
    if not code or len(path) < 3:
        return None
    slug = next((p for p in path[2:] if p.lower() != "buytickets" and not EVENT_CODE.fullmatch(p)), "")
    return {"event_code": code.upper(), "region_slug": path[1].lower(), "slug": slug.lower()}


def buy_url(t: dict) -> str:
    return (f"https://in.bookmyshow.com/movies/{t['region_slug']}/{t['slug'] or 'movie'}"
            f"/buytickets/{t['event_code']}/")


def page_url(t: dict) -> str:
    """Works before bookings open; /buytickets/ shows 'no shows' until then."""
    return f"https://in.bookmyshow.com/movies/{t['region_slug']}/{t['slug'] or 'movie'}/{t['event_code']}"


def slug_label(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-") if w) or "Movie"


def fetch_label(link: dict) -> str:
    """Movie name from the page title, falling back to the URL slug."""
    t = {"region_slug": link["region_slug"], "slug": link["slug"], "event_code": link["event_code"]}
    try:
        r = requests.get(page_url(t), headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"},
                         timeout=8)
        title = re.search(r"<title>([^<]+)</title>", r.text)
        if r.ok and title:
            name = re.split(r"\s+\(\d{4}\)|\s+-\s+Movie|\s+Movie\b|\s+\|", title.group(1))[0].strip()
            name = name.replace("&amp;", "&").replace("&#x27;", "'").replace("&#39;", "'")
            if name:
                return name
    except requests.RequestException:
        pass
    return slug_label(link["slug"])


# ── dates ───────────────────────────────────────────────────────────────
def _make(y: int | None, mo: int, d: int, today: date, part: str) -> str:
    years = [y] if y else [today.year, today.year + 1]
    for yr in years:
        try:
            dt = date(yr, mo, d)
        except ValueError:
            continue
        if y or dt >= today:
            return dt.strftime("%Y%m%d")
    raise ValueError(f"Couldn't read '{part}' as a date")


def parse_dates(text: str, today: date) -> list[str]:
    """'18-20 Dec, 25 Dec, 2027-01-01' -> ['20261218', '20261219', ...]. Raises ValueError."""
    out: list[str] = []
    for part in re.split(r"[;,]|\band\b", text.lower()):
        part = part.strip()
        if not part:
            continue
        if re.fullmatch(r"\d{8}", part):
            out.append(part)
        elif m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", part):
            out.append(_make(int(m[1]), int(m[2]), int(m[3]), today, part))
        elif m := re.fullmatch(r"(\d{1,2})\s*(?:-|to)\s*(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?", part):
            mo = MONTHS.get(m[3])
            lo, hi = int(m[1]), int(m[2])
            if not mo or hi < lo or hi - lo > 14:
                raise ValueError(f"Couldn't read '{part}' as a date range (max 15 days)")
            out += [_make(int(m[4]) if m[4] else None, mo, d, today, part) for d in range(lo, hi + 1)]
        elif m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)(?:\s+(\d{4}))?", part):
            mo = MONTHS.get(m[2])
            if not mo:
                raise ValueError(f"Couldn't read '{part}' as a date")
            out.append(_make(int(m[3]) if m[3] else None, mo, int(m[1]), today, part))
        elif m := re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", part):
            mo = MONTHS.get(m[1])
            if not mo:
                raise ValueError(f"Couldn't read '{part}' as a date")
            out.append(_make(int(m[3]) if m[3] else None, mo, int(m[2]), today, part))
        else:
            raise ValueError(f"Couldn't read '{part}' as a date")
    return sorted(set(out))


def fmt_date(code: str) -> str:
    return date(int(code[:4]), int(code[4:6]), int(code[6:8])).strftime("%d %b %Y").lstrip("0")


# ── trackers ────────────────────────────────────────────────────────────
def new_tracker(link: dict, label: str) -> dict:
    return {"id": f"{link['event_code'].lower()}-{link['region_slug']}", "label": label,
            "event_code": link["event_code"], "region_slug": link["region_slug"], "slug": link["slug"],
            "dates": [], "theatres": [], "times": [], "enabled": True}


def describe(t: dict) -> str:
    if t.get("source") == "district":
        return f"{t['label']} - District ({t['city']})\n   alert when showtimes appear"
    dates = ", ".join(fmt_date(d) for d in t["dates"]) or "any date"
    theatres = ", ".join(t["theatres"]) or "any theatre"
    times = ", ".join(t["times"]) or "any time"
    return f"{t['label']} ({t['region_slug']})\n   📅 {dates} · 🏛 {theatres} · 🕐 {times}"


# ── Telegram client ─────────────────────────────────────────────────────
class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)

    def _call(self, method: str, **payload):
        try:
            r = requests.post(f"{self.base}/{method}", json=payload, timeout=30)
            if r.ok:
                return r.json().get("result")
            print(f"  ❌ Telegram {method} {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            print(f"  ❌ Telegram {method} failed: {e}")
        return None

    def send(self, text: str, buttons=None):
        payload = {"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in row] for row in buttons]}
        res = self._call("sendMessage", **payload)
        return (res or {}).get("message_id")

    def edit_buttons(self, message_id: int, buttons) -> None:
        self._call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=message_id,
                   reply_markup={"inline_keyboard": [
                       [{"text": label, "callback_data": data} for label, data in row] for row in buttons]})

    def answer(self, callback_id: str) -> None:
        self._call("answerCallbackQuery", callback_query_id=callback_id)

    def updates(self, offset: int | None) -> list[dict]:
        payload = {"timeout": 0, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", **payload) or []


class ConsoleTelegram:
    """Used when no token is set: prints instead of sending."""
    chat_id = ""

    def __init__(self):
        self.sent: list[str] = []

    def send(self, text, buttons=None):
        self.sent.append(text)
        print("\n[telegram]\n" + text + "\n")

    def edit_buttons(self, message_id, buttons):
        pass

    def answer(self, callback_id):
        pass

    def updates(self, offset):
        return []


# ── conversation ────────────────────────────────────────────────────────
def setup_buttons(tid: str):
    return [[("📅 Dates", f"set:{tid}:dates"), ("🏛 Theatres", f"set:{tid}:theatres")],
            [("🕐 Time of day", f"set:{tid}:times"), ("✅ Done", f"set:{tid}:done")]]


def time_buttons(t: dict):
    rows = [[(("✅ " if p in t["times"] else "") + p.capitalize(), f"tm:{t['id']}:{p}") for p in PERIODS[:2]],
            [(("✅ " if p in t["times"] else "") + p.capitalize(), f"tm:{t['id']}:{p}") for p in PERIODS[2:]],
            [("Any time", f"tm:{t['id']}:any"), ("✅ Done", f"set:{t['id']}:done")]]
    return rows


class Chat:
    """Owns trackers.json and chat.json (`meta` here = pending question). The checker owns
    the results/state files; `results` is only read, for /list."""

    def __init__(self, tg, trackers: list, meta: dict, today: date, results: dict | None = None,
                 region_ok=lambda slug: True, label_fn=fetch_label):
        self.tg, self.trackers, self.meta = tg, trackers, meta
        self.results = results or {}
        self.today, self.region_ok, self.label_fn = today, region_ok, label_fn

    # -- helpers
    def _find(self, ref: str) -> dict | None:
        ref = (ref or "").strip().lower()
        if ref.isdigit() and 1 <= int(ref) <= len(self.trackers):
            return self.trackers[int(ref) - 1]
        return next((t for t in self.trackers if t["id"] == ref), None)

    def _ask_setup(self, t: dict, prefix: str = "") -> None:
        self.tg.send(f"{prefix}{describe(t)}\n\nWant to narrow it down?", setup_buttons(t["id"]))

    # -- entry point
    def handle(self, update: dict) -> None:
        cb = update.get("callback_query")
        msg = update.get("message") or (cb or {}).get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) != self.tg.chat_id and self.tg.chat_id:
            return                                    # only the owner can talk to the bot
        if cb:
            self.tg.answer(cb["id"])
            self._callback(cb.get("data", ""), msg.get("message_id"))
        elif update.get("message") and msg.get("text"):
            self._text(msg["text"].strip())

    # -- buttons
    def _callback(self, data: str, message_id) -> None:
        kind, _, rest = data.partition(":")
        tid, _, arg = rest.partition(":")
        t = self._find(tid)
        if not t:
            self.tg.send("That tracker no longer exists. /list")
            return
        if kind == "set":
            if arg == "done":
                self.meta.pop("awaiting", None)
                self.tg.send(f"✅ Tracking:\n{describe(t)}\n\n/list to see everything I'm tracking.")
            elif arg == "times":
                self.meta.pop("awaiting", None)
                self.tg.send("Which times of day? Tap to toggle.", time_buttons(t))
            elif arg in ("dates", "theatres"):
                self.meta["awaiting"] = {"id": t["id"], "field": arg}
                example = "18-20 Dec, 25 Dec" if arg == "dates" else "PVR, INOX, IMAX"
                self.tg.send(f"Send the {arg}, e.g. {example}\n(or 'any' to clear).")
        elif kind == "tm":
            if arg == "any":
                t["times"] = []
            elif arg in PERIODS:
                t["times"] = [p for p in PERIODS if (p in t["times"]) != (p == arg)]
            if message_id:
                self.tg.edit_buttons(message_id, time_buttons(t))

    # -- text
    def _text(self, text: str) -> None:
        d = parse_district_link(text)
        if d:
            self._add_district(d)
            return
        link = parse_link(text)
        if link:
            self._add(link)
            return
        if text.startswith("/"):
            self._command(text)
            return
        aw = self.meta.get("awaiting")
        if aw and aw.get("field") == "district_city":
            self._finish_district(aw, text)
            return
        if aw:
            self._apply_awaiting(aw, text)
            return
        self.tg.send("Send me a BookMyShow movie link, or /help.")

    def _add(self, link: dict) -> None:
        if not self.region_ok(link["region_slug"]):
            self.tg.send(f"I don't know the city '{link['region_slug']}' yet. "
                         "Tell Claude to add it to REGION_MAP.")
            return
        tid = f"{link['event_code'].lower()}-{link['region_slug']}"
        existing = self._find(tid)
        if existing:
            self._ask_setup(existing, "Already tracking:\n")
            return
        t = new_tracker(link, self.label_fn(link))
        self.trackers.append(t)
        self._ask_setup(t, "👀 Tracking started, alert when bookings open:\n")

    def _add_district(self, d: dict) -> None:
        if not d["city"]:
            self.meta["awaiting"] = {"field": "district_city", "link": d}
            self.tg.send("That District link has no city. Which city? (e.g. Bengaluru)")
            return
        tid = f"{d['mv'].lower()}-{d['city']}"
        existing = self._find(tid)
        if existing:
            self.tg.send(f"Already tracking:\n{describe(existing)}")
            return
        t = {"id": tid, "source": "district", "label": slug_label(d["slug"]), "slug": d["slug"],
             "city": d["city"], "mv": d["mv"], "dates": [], "theatres": [], "times": [], "enabled": True}
        self.trackers.append(t)
        self.tg.send(f"👀 Tracking started:\n{describe(t)}\n\n"
                     "Heads-up: district.in refuses GitHub's servers, so this only works when "
                     "the watcher runs from another network (it will show as blocked in /list).")

    def _finish_district(self, aw: dict, text: str) -> None:
        city = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        city = {"bangalore": "bengaluru", "bombay": "mumbai", "gurgaon": "gurugram"}.get(city, city)
        self.meta.pop("awaiting", None)
        if not city:
            self.tg.send("Send the District link again.")
            return
        self._add_district({**aw["link"], "city": city})

    def _apply_awaiting(self, aw: dict, text: str) -> None:
        t = self._find(aw["id"])
        if not t:
            self.meta.pop("awaiting", None)
            return
        field = aw["field"]
        if text.lower() in ("any", "clear", "all", "none"):
            t[field] = []
        elif field == "dates":
            try:
                t["dates"] = parse_dates(text, self.today)
            except ValueError as e:
                self.tg.send(f"{e}. Try: 18-20 Dec, 25 Dec")
                return
        else:
            t["theatres"] = [p.strip().lower() for p in text.split(",") if p.strip()]
        self.meta.pop("awaiting", None)
        self._ask_setup(t, f"✅ Updated {field}.\n")

    def _command(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            self.tg.send(HELP)
        elif cmd == "/list":
            if not self.trackers:
                self.tg.send("Nothing tracked yet. Send me a BookMyShow movie link.")
                return
            lines = []
            for i, t in enumerate(self.trackers, 1):
                flag = "" if t.get("enabled", True) else " ⏸ paused"
                res = self.results.get(t["id"], "not checked yet")
                lines.append(f"{i}. {describe(t)}{flag}\n   last: {res}")
            self.tg.send("\n\n".join(lines))
        elif cmd in ("/pause", "/resume", "/remove", "/edit"):
            t = self._find(arg)
            if not t:
                self.tg.send(f"Usage: {cmd} N  (N from /list)")
            elif cmd == "/edit" and t.get("source") == "district":
                self.tg.send("District trackers only alert when showtimes appear; nothing to edit.")
            elif cmd == "/edit":
                self._ask_setup(t)
            elif cmd == "/remove":
                self.trackers.remove(t)                 # the checker drops its state next run
                self.tg.send(f"🗑 Stopped tracking {t['label']}.")
            else:
                t["enabled"] = cmd == "/resume"
                if t["enabled"]:
                    t["epoch"] = t.get("epoch", 0) + 1  # fresh start: alert again if already open
                self.tg.send(f"{'▶️ Resumed' if t['enabled'] else '⏸ Paused'} {t['label']}.")
        else:
            self.tg.send("Unknown command. /help")
