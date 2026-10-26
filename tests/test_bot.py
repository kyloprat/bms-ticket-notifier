"""Offline tests: fake Telegram, fake BookMyShow API, fake District. Run: python -m pytest -q"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat  # noqa: E402
import district  # noqa: E402
import main  # noqa: E402

TODAY = date(2026, 10, 26)
NOW_MORNING = datetime(2026, 10, 27, 9, 0, tzinfo=main.IST)
NOW_8PM = datetime(2026, 10, 27, 20, 5, tzinfo=main.IST)

DOOMSDAY = "https://in.bookmyshow.com/movies/bengaluru/avengers-doomsday/ET00439706"
DOOMSDAY_BUY = "https://in.bookmyshow.com/movies/bengaluru/avengers-doomsday/buytickets/ET00439706/"


# ── fakes ───────────────────────────────────────────────────────────────
class FakeTg:
    chat_id = "42"

    def __init__(self, updates=None):
        self.inbox = list(updates or [])
        self.sent = []          # (text, buttons)
        self.edits = []

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))
        return len(self.sent)

    def edit_buttons(self, message_id, buttons):
        self.edits.append((message_id, buttons))

    def answer(self, callback_id):
        pass

    def updates(self, offset):
        out = [u for u in self.inbox if offset is None or u["update_id"] >= offset]
        return out

    @property
    def texts(self):
        return [t for t, _ in self.sent]


def msg(uid, text, chat_id=42):
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": chat_id}, "text": text}}


def tap(uid, data, chat_id=42):
    return {"update_id": uid, "callback_query": {
        "id": f"cb{uid}", "data": data, "message": {"message_id": 99, "chat": {"id": chat_id}}}}


def api(venues):
    """Minimal BookMyShow primary-dynamic payload with the given theatre names."""
    cards = [{"type": "venue-card", "additionalData": {"venueName": v, "venueCode": v[:4].upper()},
              "showtimes": [{"title": "07:20 PM", "additionalData": {
                  "sessionId": "1", "showDateCode": "20261218", "showTimeCode": "1920",
                  "categories": [{"priceDesc": "Gold", "curPrice": "300", "availStatus": "3"}]}}]}
             for v in venues]
    return {"data": {"topStickyWidgets": [], "showtimeWidgets": [
        {"type": "groupList", "data": [{"type": "venueGroup", "data": cards}]}]}}


def make_chat(trackers=None, meta=None, state=None, tg=None, region_ok=lambda s: True):
    tg = tg or FakeTg()
    c = chat.Chat(tg, trackers if trackers is not None else [], meta if meta is not None else {},
                  state if state is not None else {}, TODAY, region_ok=region_ok,
                  label_fn=lambda link: "Avengers: Doomsday")
    return c, tg


# ── links and dates ─────────────────────────────────────────────────────
def test_parse_link_movie_page_and_buytickets_and_query():
    for url in (DOOMSDAY, DOOMSDAY_BUY, DOOMSDAY + "?utm=x"):
        link = chat.parse_link(f"check this {url} thanks")
        assert link == {"event_code": "ET00439706", "region_slug": "bengaluru", "slug": "avengers-doomsday"}
    assert chat.parse_link("https://example.com/movies/x/y/ET00439706") is None
    assert chat.parse_link("https://in.bookmyshow.com/movies/bengaluru") is None


def test_buy_and_page_urls():
    t = {"region_slug": "bengaluru", "slug": "avengers-doomsday", "event_code": "ET00439706"}
    assert chat.buy_url(t) == DOOMSDAY_BUY
    assert chat.page_url(t) == DOOMSDAY


def test_parse_dates():
    assert chat.parse_dates("18 Dec", TODAY) == ["20261218"]
    assert chat.parse_dates("18-20 Dec, 25 December", TODAY) == ["20261218", "20261219", "20261220", "20261225"]
    assert chat.parse_dates("Dec 18th and 2027-01-02", TODAY) == ["20261218", "20270102"]
    assert chat.parse_dates("20261218", TODAY) == ["20261218"]
    assert chat.parse_dates("5 Jan", TODAY) == ["20270105"]        # already past this year -> next year
    for bad in ("someday", "31 Feb", "20-10 Dec", "1-30 Dec"):
        with pytest.raises(ValueError):
            chat.parse_dates(bad, TODAY)


# ── conversation ────────────────────────────────────────────────────────
def test_link_starts_tracking_and_offers_buttons():
    trackers = []
    c, tg = make_chat(trackers)
    c.handle(msg(1, DOOMSDAY))
    assert [t["id"] for t in trackers] == ["et00439706-bengaluru"]
    assert trackers[0]["label"] == "Avengers: Doomsday" and trackers[0]["enabled"]
    text, buttons = tg.sent[0]
    assert "alert when bookings open" in text
    assert {b[1] for row in buttons for b in row} == {
        "set:et00439706-bengaluru:dates", "set:et00439706-bengaluru:theatres",
        "set:et00439706-bengaluru:times", "set:et00439706-bengaluru:done"}


def test_same_link_twice_does_not_duplicate():
    trackers = []
    c, tg = make_chat(trackers)
    c.handle(msg(1, DOOMSDAY))
    c.handle(msg(2, DOOMSDAY_BUY))
    assert len(trackers) == 1 and "Already tracking" in tg.texts[1]


def test_dates_flow_via_button_then_text():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta)
    c.handle(msg(1, DOOMSDAY))
    c.handle(tap(2, "set:et00439706-bengaluru:dates"))
    assert meta["awaiting"] == {"id": "et00439706-bengaluru", "field": "dates"}
    c.handle(msg(3, "someday"))
    assert "Couldn't read" in tg.texts[-1] and meta["awaiting"]            # keeps waiting
    c.handle(msg(4, "18-19 Dec"))
    assert trackers[0]["dates"] == ["20261218", "20261219"] and "awaiting" not in meta
    c.handle(tap(5, "set:et00439706-bengaluru:dates"))
    c.handle(msg(6, "any"))
    assert trackers[0]["dates"] == []


def test_theatres_flow_and_time_toggle():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta)
    c.handle(msg(1, DOOMSDAY))
    c.handle(tap(2, "set:et00439706-bengaluru:theatres"))
    c.handle(msg(3, "PVR, INOX ,IMAX"))
    assert trackers[0]["theatres"] == ["pvr", "inox", "imax"]
    c.handle(tap(4, "set:et00439706-bengaluru:times"))
    c.handle(tap(5, "tm:et00439706-bengaluru:evening"))
    c.handle(tap(6, "tm:et00439706-bengaluru:night"))
    assert trackers[0]["times"] == ["evening", "night"]
    c.handle(tap(7, "tm:et00439706-bengaluru:evening"))
    assert trackers[0]["times"] == ["night"]
    assert tg.edits and any("✅ Night" in b[0] for row in tg.edits[-1][1] for b in row)
    c.handle(tap(8, "tm:et00439706-bengaluru:any"))
    assert trackers[0]["times"] == []


def test_commands_list_pause_resume_remove():
    trackers, meta, state = [], {"results": {"et00439706-bengaluru": "no showtimes listed yet"}}, {}
    c, tg = make_chat(trackers, meta, state)
    c.handle(msg(1, DOOMSDAY))
    state["et00439706-bengaluru"] = {"shows": {"x": 1}}
    c.handle(msg(2, "/list"))
    assert "1. Avengers: Doomsday" in tg.texts[-1] and "no showtimes listed yet" in tg.texts[-1]
    c.handle(msg(3, "/pause 1"))
    assert trackers[0]["enabled"] is False and "⏸ paused" in (c.handle(msg(4, "/list")) or tg.texts[-1])
    c.handle(msg(5, "/resume 1"))
    assert trackers[0]["enabled"] is True and "et00439706-bengaluru" not in state      # fresh start
    c.handle(msg(6, "/remove 1"))
    assert trackers == [] and meta["results"] == {}
    c.handle(msg(7, "/remove 9"))
    assert "Usage" in tg.texts[-1]


def test_other_chats_are_ignored():
    trackers = []
    c, tg = make_chat(trackers)
    c.handle(msg(1, DOOMSDAY, chat_id=666))
    c.handle(tap(2, "set:x:done", chat_id=666))
    assert trackers == [] and tg.sent == []


def test_unknown_city_is_refused():
    trackers = []
    c, tg = make_chat(trackers, region_ok=lambda s: s == "bengaluru")
    c.handle(msg(1, "https://in.bookmyshow.com/movies/atlantis/avengers-doomsday/ET00439706"))
    assert trackers == [] and "don't know the city" in tg.texts[0]


def test_district_link_with_and_without_city():
    trackers, meta = [], {}
    c, tg = make_chat(trackers, meta)
    c.handle(msg(1, "https://www.district.in/movies/namit-malhotra-s-ramayana-movie-tickets-in-kolkata-MV176276"))
    assert trackers[0]["source"] == "district" and trackers[0]["city"] == "kolkata"
    assert trackers[0]["slug"] == "namit-malhotra-s-ramayana" and trackers[0]["mv"] == "MV176276"
    c.handle(msg(2, "https://www.district.in/movies/avengers-doomsday-movie-tickets-MV176735"))
    assert meta["awaiting"]["field"] == "district_city" and len(trackers) == 1
    c.handle(msg(3, "Bangalore"))
    assert trackers[1]["id"] == "mv176735-bengaluru" and "awaiting" not in meta
    assert "refuses GitHub" in tg.texts[-1]


def test_district_parse_and_url():
    link = district.parse_district_link(
        "https://www.district.in/movies/namit-malhotra-s-ramayana-movie-tickets-in-bengaluru-MV176276?frmtid=rtb7dmwvuo")
    assert link == {"slug": "namit-malhotra-s-ramayana", "city": "bengaluru", "mv": "MV176276"}
    assert district.district_url(link).endswith("namit-malhotra-s-ramayana-movie-tickets-in-bengaluru-MV176276")
    assert district.parse_district_link("https://www.district.in/movies/") is None


def test_district_check_reads_sessions():
    def page(body):
        class R:
            status_code = 200
            text = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(body)}</script></html>'
        return lambda url, **kw: R()

    t = {"slug": "x", "city": "bengaluru", "mv": "MV1"}
    closed = {"props": {"isReleased": False, "release_date": "2026-12-18"}}
    live = {"languageFormatGroups": [{"number_of_sessions": 83}, {"number_of_sessions": 4}]}
    assert district.check_district(t, get=page(closed)) == (True, "no showtimes listed yet (release 2026-12-18)", False)
    assert district.check_district(t, get=page(live)) == (True, "87 sessions listed", True)

    class Blocked:
        status_code, text = 403, "denied"
    ok, result, is_open = district.check_district(t, get=lambda url, **kw: Blocked())
    assert (ok, is_open) == (False, False) and "HTTP 403" in result


# ── checks and the run loop ─────────────────────────────────────────────
def doomsday_tracker(**kw):
    t = chat.new_tracker(chat.parse_link(DOOMSDAY), "Avengers: Doomsday")
    t.update(kw)
    return t


def test_check_bms_first_showtimes_alert_then_quiet_then_new_theatre():
    t = doomsday_tracker()
    r = main.check_bms(t, {}, fetch=lambda *a: api([]))
    assert r["ok"] and r["result"] == "no showtimes listed yet" and r["changes"] == []
    r = main.check_bms(t, {}, fetch=lambda *a: api(["PVR Forum", "INOX Garuda"]))
    assert "BOOKINGS OPEN" in r["changes"][0]
    r2 = main.check_bms(t, r["state"], fetch=lambda *a: api(["PVR Forum", "INOX Garuda"]))
    assert r2["changes"] == []
    r3 = main.check_bms(t, r["state"], fetch=lambda *a: api(["PVR Forum", "INOX Garuda", "Cinepolis Orion"]))
    assert any("NEW" in c and "Cinepolis" in c for c in r3["changes"])


def test_check_bms_theatre_filter_and_no_data():
    t = doomsday_tracker(theatres=["pvr"])
    r = main.check_bms(t, {}, fetch=lambda *a: api(["INOX Garuda"]))
    assert r["ok"] and r["state"]["shows"] == {} and r["changes"] == []
    r = main.check_bms(t, r["state"], fetch=lambda *a: api(["INOX Garuda", "PVR Forum"]))
    assert "BOOKINGS OPEN" in r["changes"][0]
    assert main.check_bms(t, {}, fetch=lambda *a: None)["ok"] is False


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def read(name):
    return json.loads(Path(name).read_text(encoding="utf-8"))


def test_run_link_in_chat_is_tracked_and_checked_in_the_same_run(workdir):
    tg = FakeTg([msg(1, DOOMSDAY)])
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api([]))
    assert read("trackers.json")[0]["id"] == "et00439706-bengaluru"
    assert read("meta.json")["results"]["et00439706-bengaluru"] == "no showtimes listed yet"
    assert read("meta.json")["tg_offset"] == 2
    assert len(tg.sent) == 1                                   # just the "want to narrow it down?" question


def test_run_alerts_immediately_and_only_once(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api(["PVR Forum"]))
    assert len(tg.sent) == 1 and "BOOKINGS OPEN" in tg.texts[0] and DOOMSDAY_BUY in tg.texts[0]
    tg2 = FakeTg()
    main.run(now=NOW_MORNING, tg=tg2, fetch=lambda *a: api(["PVR Forum"]))
    assert tg2.sent == []


def test_run_daily_note_once_at_8pm_only(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    nothing = lambda *a: api([])  # noqa: E731
    tg = FakeTg()
    main.run(now=NOW_MORNING, tg=tg, fetch=nothing)
    assert tg.sent == []                                       # 9 am: silent
    main.run(now=NOW_8PM, tg=tg, fetch=nothing)
    assert len(tg.sent) == 1 and "Still watching 1 tracker" in tg.texts[0]
    assert "Avengers: Doomsday: no showtimes listed yet" in tg.texts[0]
    main.run(now=NOW_8PM.replace(minute=15), tg=tg, fetch=nothing)
    assert len(tg.sent) == 1                                   # not again the same day
    main.run(now=NOW_8PM.replace(day=28), tg=tg, fetch=nothing)
    assert len(tg.sent) == 2                                   # next day


def test_run_blocked_warning_once_after_three_failures(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    tg = FakeTg()
    for _ in range(5):
        main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: None)
    assert sum("Can't read BookMyShow" in t for t in tg.texts) == 1


def test_run_district_blocked_is_not_a_bookmyshow_failure_and_alerts_when_open(workdir):
    d = {"id": "mv1-bengaluru", "source": "district", "label": "Film", "slug": "film", "city": "bengaluru",
         "mv": "MV1", "dates": [], "theatres": [], "times": [], "enabled": True}
    Path("trackers.json").write_text(json.dumps([d]))
    tg = FakeTg()
    for _ in range(4):
        main.run(now=NOW_MORNING, tg=tg, district_check=lambda t: (False, "District blocked this network (HTTP 403)", False))
    assert tg.sent == [] and read("meta.json")["results"]["mv1-bengaluru"].startswith("District blocked")
    main.run(now=NOW_MORNING, tg=tg, district_check=lambda t: (True, "40 sessions listed", True))
    assert len(tg.sent) == 1 and "BOOKINGS OPEN on District" in tg.texts[0]
    main.run(now=NOW_MORNING, tg=tg, district_check=lambda t: (True, "40 sessions listed", True))
    assert len(tg.sent) == 1


def test_run_does_not_rewrite_files_when_nothing_changed(workdir):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    main.run(now=NOW_MORNING, tg=FakeTg(), fetch=lambda *a: api([]))
    before = {n: Path(n).stat().st_mtime_ns for n in ("trackers.json", "meta.json", "bms_state.json")}
    main.run(now=NOW_MORNING, tg=FakeTg(), fetch=lambda *a: api([]))
    assert before == {n: Path(n).stat().st_mtime_ns for n in before}


def test_run_survives_a_bad_chat_message(workdir, monkeypatch):
    Path("trackers.json").write_text(json.dumps([doomsday_tracker()]))
    tg = FakeTg([msg(1, "/list")])
    monkeypatch.setattr(chat.Chat, "_command", lambda self, text: 1 / 0)
    main.run(now=NOW_MORNING, tg=tg, fetch=lambda *a: api([]))
    assert any("Couldn't process" in t for t in tg.texts)
    assert read("meta.json")["tg_offset"] == 2                 # the bad message isn't retried forever
