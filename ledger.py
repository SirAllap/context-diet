"""context-diet: the scale half, as its own process.

Talks to agentglass only through the channel its token opens: it reads the
turns the app already stores, keeps its own running tally of them, and draws
one panel. It never writes to agentglass, never answers an agent and never
starts anything.

Three loops. One asks for new turns, one waits on the app for a settings
change, and one redraws at most once every ten seconds — a redraw is a frame
to every open window, and a standing tally is not a ticker.

It asks rather than subscribing. The live stream is a WebSocket, and a
WebSocket client is a dependency or a hundred lines of frame parsing for a
number that is already a few seconds old when it arrives; asking every twenty
seconds costs one small request and keeps this file readable.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

import tally

BASE = os.environ.get("AGENTGLASS_URL", "http://127.0.0.1:4000").rstrip("/")
TOKEN = os.environ.get("AGENTGLASS_READ_TOKEN", "")
DATA = Path(os.environ.get("HOME", str(Path.home()))) / ".local" / "share" / "context-diet"
LEDGER = DATA / "ledger.jsonl"
STATE = DATA / "state.json"
PANEL = "ledger"

# The app answers at most this many events in one ask, whatever is asked for.
# Its own history therefore has to live here: the store is a window, not an
# archive, and it is a window measured in minutes on a busy machine.
MAX_RECENT = 2000
DRAW_EVERY = 10.0


def call(method: str, path: str, body: object | None = None, timeout: float = 40):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {TOKEN}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code}
    except (OSError, ValueError) as e:
        # A restart, a timeout, a body that is not JSON: an answer a loop can
        # back off from, never an exception that ends the plugin.
        return {"ok": False, "status": 0, "error": str(e)[:200]}


def log(*a: object) -> None:
    """The only debugger there is: the server spawns a plugin with its output
    discarded, so a crash leaves nothing behind but this file."""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "plugin.log", "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n")


class Ledger:
    def __init__(self) -> None:
        self.cfg = dict(tally.DEFAULTS)
        self.rows: list[dict] = []
        self.subs: dict[str, int] = {}
        self.sessions: dict[str, dict] = {}
        self.book = {"dropped": 0, "backfilled": 0, "missed": 0, "lastSeen": "—"}
        self.last_id = 0
        self.lock = threading.Lock()
        self.dirty = True

    # ---------------------------------------------------------------- disk

    def load(self) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        try:
            saved = json.loads(STATE.read_text())
            self.last_id = int(saved["lastId"])
            # The subagent tally lives here rather than in the ledger file,
            # which holds one line per main-thread turn. Counted in memory
            # only, it read as "no subagent turns" after every restart — a
            # panel saying nothing happened, when what happened is that the
            # bookmark had already passed those events and they never came
            # back.
            self.subs = {str(k): int(v) for k, v in (saved.get("subs") or {}).items()}
            self.sessions = {str(k): dict(v) for k, v in (saved.get("sessions") or {}).items()
                             if isinstance(v, dict)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.last_id, self.subs, self.sessions = 0, {}, {}
        first = time.time() * 1000 - self.cfg["windowDays"] * 86_400_000
        kept = []
        try:
            for line in LEDGER.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # a line half-written when the app was killed
                if row.get("t", 0) >= first:
                    kept.append(row)
        except OSError:
            pass  # first run
        self.rows = kept
        # Rewritten on every start so the file cannot grow past the window the
        # person asked for. Anything older is gone from the tally anyway.
        try:
            LEDGER.write_text("".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8")
        except OSError as e:
            log("could not rewrite the ledger:", e)

    def remember(self, rows: list[dict]) -> None:
        if rows:
            try:
                with open(LEDGER, "a", encoding="utf-8") as f:
                    f.write("".join(json.dumps(r) + "\n" for r in rows))
            except OSError as e:
                log("could not append to the ledger:", e)
        # Written even when no main-thread turn was added: a batch can be all
        # subagent turns, and the bookmark has moved past them either way.
        try:
            # Only the sessions inside the window: one row per session, and a
            # machine that has been running for months would otherwise keep
            # every session it ever saw in a file it rewrites on every batch.
            first = time.time() * 1000 - self.cfg["windowDays"] * 86_400_000
            live = {k: v for k, v in self.sessions.items() if v.get("t", 0) >= first}
            STATE.write_text(json.dumps({"lastId": self.last_id, "subs": self.subs,
                                         "sessions": live}))
        except OSError as e:
            log("could not save the bookmark:", e)

    # -------------------------------------------------------------- counting

    def ingest(self, events: list[dict]) -> int:
        """Everything newer than the last id this has seen. Ids are the app's
        own order, so one number is the whole bookmark."""
        fresh = [e for e in events if int(e.get("id") or 0) > self.last_id]
        if not fresh:
            return 0
        oldest = min(int(e["id"]) for e in fresh)
        if self.last_id and oldest > self.last_id + 1:
            # The answer starts after where this left off: turns happened while
            # it was not looking and they are not coming back. Counted and
            # drawn rather than passed over, because a tally that quietly
            # skips is worse than one that admits a hole.
            self.book["missed"] += oldest - self.last_id - 1
        added = []
        with self.lock:
            for e in sorted(fresh, key=lambda x: int(x["id"])):
                before = len(self.rows)
                what = tally.take(e, self.rows, self.subs, self.cfg, self.sessions)
                if what == "dropped":
                    self.book["dropped"] += 1
                elif len(self.rows) > before:
                    added.append(self.rows[-1])
                self.last_id = max(self.last_id, int(e["id"]))
            if self.rows:
                self.book["lastSeen"] = time.strftime("%H:%M", time.localtime(self.rows[-1]["t"] / 1000))
            self.dirty = True
        self.remember(added)
        return len(fresh)

    def seed_subs(self, events: list[dict], mark: int) -> None:
        """Count the subagent turns the bookmark has already gone past.

        The ledger file holds one line per main-thread turn, so a start with
        no saved subagent tally has nothing to show and the panel says "no
        subagent turns" about a stretch of work that had plenty. Only turns
        at or below `mark` are counted here: anything newer was counted by
        `ingest`, and counting it twice would be worse than starting empty.

        Once, and only into an empty tally — a second seeding would add the
        same turns to a count that already holds them.
        """
        if self.subs or not events:
            return
        with self.lock:
            for e in events:
                if int(e.get("id") or 0) <= mark and e.get("agent_id") and tally.context_of(e) > 0:
                    model = e.get("model_name") or "unknown"
                    self.subs[model] = self.subs.get(model, 0) + 1
            self.dirty = True
        if self.subs:
            log("seeded", sum(self.subs.values()), "subagent turns already behind the bookmark")
            self.remember([])

    # ---------------------------------------------------------------- drawing

    def draw(self) -> None:
        with self.lock:
            s = tally.stats(self.rows, self.cfg, int(time.time() * 1000))
            node = tally.tree(s, dict(self.subs), dict(self.cfg), dict(self.book),
                              dict(self.sessions))
        r = call("POST", "/plugin/self/panel", {"id": PANEL, "tree": node})
        if not isinstance(r, dict) or not r.get("ok"):
            log("the panel was refused:", json.dumps(r)[:300])

    # ------------------------------------------------------------------ loops

    def poll_turns(self) -> None:
        while True:
            try:
                limit = 300
                while True:
                    events = call("GET", f"/events/recent?limit={limit}")
                    if not isinstance(events, list):
                        break
                    fresh = self.ingest(events)
                    # Every row came back new, so the answer was full and there
                    # may be more behind it. Ask again for as much as the app
                    # will give before deciding nothing was missed.
                    if fresh < len(events) or limit >= MAX_RECENT:
                        break
                    limit = MAX_RECENT
            except Exception:
                log("poll_turns:", traceback.format_exc()[:500])
            time.sleep(max(5, self.cfg["pollSeconds"]))

    def poll_app(self) -> None:
        while True:
            try:
                r = call("GET", "/plugin/self/events?wait=25000", timeout=35)
                if not isinstance(r, dict) or not r.get("ok"):
                    # The long poll returns empty after its wait, so a fast
                    # answer here is the app being down, not a quiet minute.
                    time.sleep(2)
                    continue
                for ev in r.get("events", []):
                    if ev.get("type") == "settings":
                        with self.lock:
                            self.cfg = tally.config(ev.get("settings"))
                            self.dirty = True
                        log("settings:", json.dumps(self.cfg))
            except Exception:
                log("poll_app:", traceback.format_exc()[:500])
                time.sleep(2)

    def run(self) -> None:
        me = call("GET", "/plugin/self")
        if not isinstance(me, dict) or not me.get("ok"):
            log("the plugin channel did not answer; is the token set?", json.dumps(me)[:200])
        else:
            self.cfg = tally.config(me.get("settings"))
        self.load()
        from_file = len(self.rows)
        mark = self.last_id
        # One deep read before the loops, so the first panel is the whole
        # window this can see rather than the last twenty seconds of it.
        first = call("GET", f"/events/recent?limit={MAX_RECENT}")
        if isinstance(first, list):
            self.book["backfilled"] = self.ingest(first)
            self.seed_subs(first, mark)
        log("started with", from_file, "turns from its own file,", self.book["backfilled"],
            "events read at start,", len(self.rows), "turns counted, last id", self.last_id)
        self.draw()

        for fn in (self.poll_turns, self.poll_app):
            threading.Thread(target=fn, daemon=True).start()

        while True:
            time.sleep(DRAW_EVERY)
            with self.lock:
                due, self.dirty = self.dirty, False
            if due:
                self.draw()


if __name__ == "__main__":
    Ledger().run()
