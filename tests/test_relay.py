"""Relay tests: fake GitHub store + fake Telegram."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import relay  # noqa: E402
from test_bot import DOOMSDAY, TODAY, FakeTg, msg, tap  # noqa: E402

ENV = {"WEBHOOK_SECRET": "s3cret", "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "42",
       "GITHUB_TOKEN": "x", "GITHUB_REPO": "o/r"}
LABEL = lambda link: "Avengers: Doomsday"  # noqa: E731


class FakeStore:
    def __init__(self, files=None, conflicts=0):
        self.files = {k: (v, f"sha-{k}") for k, v in (files or {}).items()}
        self.writes = []
        self.conflicts = conflicts

    def get(self, path, default):
        data, sha = self.files.get(path, (default, None))
        return json.loads(json.dumps(data)), sha

    def put(self, path, data, sha, message):
        if self.conflicts:
            self.conflicts -= 1
            raise relay.Conflict(path)
        assert sha == self.files.get(path, (None, None))[1]
        self.files[path] = (data, f"sha-{path}-{len(self.writes)}")
        self.writes.append(path)


def test_link_creates_tracker_and_replies_after_saving():
    store, tg = FakeStore({"meta.json": {"results": {}}}), FakeTg()
    relay.process(msg(1, DOOMSDAY), store, tg, TODAY, label_fn=LABEL)
    assert store.files["trackers.json"][0][0]["id"] == "et00439706-bengaluru"
    assert store.writes == ["trackers.json"] and "Tracking started" in tg.texts[0]


def test_button_then_text_round_trip_through_the_store():
    store, tg = FakeStore(), FakeTg()
    relay.process(msg(1, DOOMSDAY), store, tg, TODAY, label_fn=LABEL)
    relay.process(tap(2, "set:et00439706-bengaluru:dates"), store, tg, TODAY)
    assert store.files["chat.json"][0]["awaiting"]["field"] == "dates"
    relay.process(msg(3, "18-19 Dec"), store, tg, TODAY)
    assert store.files["trackers.json"][0][0]["dates"] == ["20261218", "20261219"]
    assert "awaiting" not in store.files["chat.json"][0]


def test_list_reads_results_written_by_the_checker():
    store = FakeStore({"trackers.json": [], "meta.json": {"results": {"et00439706-bengaluru": "no showtimes listed yet"}}})
    tg = FakeTg()
    relay.process(msg(1, DOOMSDAY), store, tg, TODAY, label_fn=LABEL)
    relay.process(msg(2, "/list"), store, tg, TODAY)
    assert "no showtimes listed yet" in tg.texts[-1]
    assert "meta.json" not in store.writes                      # never written by the relay


def test_conflict_is_retried_without_double_replies():
    store, tg = FakeStore(conflicts=1), FakeTg()
    relay.process(msg(1, DOOMSDAY), store, tg, TODAY, label_fn=LABEL)
    assert len(tg.sent) == 1 and store.writes == ["trackers.json"]


def test_webhook_rejects_wrong_secret_and_ignores_strangers():
    store, tg = FakeStore(), FakeTg()
    raw = json.dumps(msg(1, DOOMSDAY)).encode()
    assert relay.handle_webhook("wrong", raw, env=ENV, store=store, tg=tg) == 403
    assert relay.handle_webhook("", raw, env={**ENV, "WEBHOOK_SECRET": ""}, store=store, tg=tg) == 403
    assert relay.handle_webhook("s3cret", b"not json", env=ENV, store=store, tg=tg) == 400
    stranger = json.dumps(msg(1, DOOMSDAY, chat_id=666)).encode()
    assert relay.handle_webhook("s3cret", stranger, env=ENV, store=store, tg=tg) == 200
    assert store.writes == [] and tg.sent == []
    assert relay.handle_webhook("s3cret", raw, env=ENV, store=store, tg=tg, label_fn=LABEL) == 200
    assert store.writes == ["trackers.json"]


def test_webhook_reports_save_failure_to_the_owner():
    class Broken(FakeStore):
        def put(self, *a):
            raise RuntimeError("github down")
    tg = FakeTg()
    status = relay.handle_webhook("s3cret", json.dumps(msg(1, DOOMSDAY)).encode(), env=ENV,
                                  store=Broken(), tg=tg, label_fn=LABEL)
    assert status == 200 and tg.texts == ["⚠️ Couldn't save that (RuntimeError). Please try again."]
