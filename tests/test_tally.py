"""What the tally must get right, asserted where the decision is made.

The fixtures are the shape agentglass hands over — the same keys, the same
null for a main-thread turn — because a test against an invented shape passes
while the plugin reads nothing.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tally  # noqa: E402

HOUR = 3_600_000
NOW = 1_800_000_000_000


def turn(ctx, *, cr=None, agent=None, usd=0.0, model="claude-opus-5", at=NOW, eid=1):
    """One event. `cr` defaults to all of the context being a cache read,
    which is what a main-thread turn past the first one looks like."""
    return {"id": eid, "timestamp": at, "agent_id": agent, "model_name": model,
            "input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0,
            "cache_read_tokens": ctx if cr is None else cr, "cost_usd": usd}


class TakeTest(unittest.TestCase):
    def setUp(self):
        self.rows, self.subs = [], {}
        self.cfg = dict(tally.DEFAULTS)

    def test_main_thread_turn_is_counted(self):
        self.assertEqual(tally.take(turn(120_000), self.rows, self.subs, self.cfg), "main")
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.subs, {})

    def test_a_subagent_turn_never_reaches_the_rows(self):
        # agent_id is the split. A subagent's 400K is its own business and
        # counting it on the main thread would put the tally above 100%.
        self.assertEqual(tally.take(turn(400_000, agent="sub-1"), self.rows, self.subs, self.cfg), "sub")
        self.assertEqual(self.rows, [])
        self.assertEqual(self.subs, {"claude-opus-5": 1})

    def test_an_event_with_no_usage_is_not_a_turn(self):
        self.assertEqual(tally.take(turn(0), self.rows, self.subs, self.cfg), "skip")
        self.assertEqual(self.rows, [])

    def test_a_turn_above_the_ceiling_is_dropped_not_clamped(self):
        # The first usage-bearing event of a session carries the whole
        # cumulative transcript. Clamping would keep 137M tokens in the
        # numerator as a turn of exactly the ceiling.
        self.assertEqual(tally.take(turn(137_777_956), self.rows, self.subs, self.cfg), "dropped")
        self.assertEqual(self.rows, [])

    def test_context_is_the_three_input_classes(self):
        e = turn(0)
        e["input_tokens"], e["cache_read_tokens"], e["cache_creation_tokens"] = 1_000, 90_000, 9_000
        e["output_tokens"] = 500_000  # what came back is not read again
        self.assertEqual(tally.context_of(e), 100_000)


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(tally.DEFAULTS)

    def tally_of(self, events):
        rows, subs = [], {}
        for e in events:
            tally.take(e, rows, subs, self.cfg)
        return tally.stats(rows, self.cfg, NOW)

    def test_few_turns_can_carry_most_of_the_re_reading(self):
        # This is the whole point of the panel: a share of turns and a share
        # of re-reading are different numbers, and only the second one says
        # whether a habit is worth changing.
        s = self.tally_of([turn(10_000, eid=i) for i in range(9)] + [turn(900_000, eid=9)])
        self.assertAlmostEqual(s["overShare"], 0.1)
        self.assertGreater(s["overCarries"], 0.9)

    def test_would_leave_charges_every_turn_at_most_the_cap(self):
        # Two turns, 100K and 500K: 600K read, 300K under a 200K cap.
        s = self.tally_of([turn(100_000, eid=1), turn(500_000, eid=2)])
        self.assertAlmostEqual(s["wouldLeave"], 0.5)

    def test_turns_outside_the_window_are_not_counted(self):
        self.cfg["windowDays"] = 1
        s = self.tally_of([turn(100_000, at=NOW - 48 * HOUR, eid=1), turn(100_000, eid=2)])
        self.assertEqual(s["turns"], 1)

    def test_an_empty_window_divides_by_nothing(self):
        s = tally.stats([], self.cfg, NOW)
        self.assertEqual(s["turns"], 0)
        self.assertEqual(s["overCarries"], 0.0)
        self.assertEqual(s["wouldLeave"], 0.0)


class ConfigTest(unittest.TestCase):
    def test_nonsense_falls_back_to_the_default_not_to_zero(self):
        # A cap of 0 would put every turn over it; a ceiling of 0 would drop
        # the lot. Both read as an alarming panel rather than a broken one.
        cfg = tally.config({"cap": 0, "ceiling": -1, "windowDays": "thirty", "pollSeconds": True})
        self.assertEqual(cfg, tally.DEFAULTS)

    def test_a_number_that_arrives_as_a_float_is_kept(self):
        self.assertEqual(tally.config({"cap": 250_000.0})["cap"], 250_000)

    def test_settings_absent_entirely(self):
        self.assertEqual(tally.config(None), tally.DEFAULTS)


class TreeTest(unittest.TestCase):
    """The app draws a closed vocabulary and refuses a panel it cannot read,
    so the shape is asserted here rather than discovered on screen."""

    NODES = {"stack", "row", "section", "split", "tabs", "heading", "text", "markdown",
             "code", "badge", "stat", "keyValue", "list", "timeline", "button", "form",
             "progress", "empty", "link", "divider"}
    TONES = {"default", "muted", "accent", "success", "warning", "danger"}

    def walk(self, node, seen):
        if isinstance(node, dict):
            if "type" in node:
                seen.add(node["type"])
            for value in node.values():
                self.walk(value, seen)
        elif isinstance(node, list):
            for item in node:
                self.walk(item, seen)
        return seen

    def tones(self, node, out):
        if isinstance(node, dict):
            if "tone" in node:
                out.append(node["tone"])
            for value in node.values():
                self.tones(value, out)
        elif isinstance(node, list):
            for item in node:
                self.tones(item, out)
        return out

    def built(self, events, book=None):
        rows, subs, cfg = [], {}, dict(tally.DEFAULTS)
        for e in events:
            tally.take(e, rows, subs, cfg)
        return tally.tree(tally.stats(rows, cfg, NOW), subs, cfg, book or {})

    def test_every_node_is_one_the_app_draws(self):
        node = self.built([turn(900_000, usd=0.42, eid=1),
                           turn(10_000, eid=2),
                           turn(300_000, agent="sub-1", model="claude-haiku-4-5", eid=3)])
        self.assertTrue(self.walk(node, set()) <= self.NODES)

    def test_no_tone_is_ever_null(self):
        # A null tone is what a conditional colour written as an expression
        # leaves behind, and the panel is refused rather than drawn without it.
        node = self.built([turn(50_000, eid=1),
                           turn(300_000, agent="a", model="claude-opus-5", eid=2)])
        self.assertTrue(all(t in self.TONES for t in self.tones(node, [])))

    def test_every_stat_value_is_a_string(self):
        node = self.built([turn(900_000, usd=1.5, eid=1)])
        stats = []

        def find(n):
            if isinstance(n, dict):
                if n.get("type") == "stat":
                    stats.append(n)
                for v in n.values():
                    find(v)
            elif isinstance(n, list):
                for i in n:
                    find(i)

        find(node)
        self.assertTrue(stats)
        self.assertTrue(all(isinstance(s["value"], str) for s in stats))

    def test_nothing_counted_yet_says_so_rather_than_drawing_zeroes(self):
        self.assertEqual(self.built([])["type"], "empty")

    def test_progress_values_stay_between_zero_and_one(self):
        node = self.built([turn(900_000, eid=1), turn(10_000, eid=2)])
        bars = []

        def find(n):
            if isinstance(n, dict):
                if n.get("type") == "progress":
                    bars.append(n["value"])
                for v in n.values():
                    find(v)
            elif isinstance(n, list):
                for i in n:
                    find(i)

        find(node)
        self.assertTrue(bars)
        self.assertTrue(all(0.0 <= b <= 1.0 for b in bars))


class ManifestTest(unittest.TestCase):
    """A setting renamed here and not there stops working in silence: the app
    would keep its own default and the panel would look right."""

    def setUp(self):
        self.manifest = json.loads((ROOT / "plugin.json").read_text())

    def test_every_declared_setting_has_a_default_in_the_code(self):
        declared = {f["key"] for f in self.manifest["contributes"]["settings"]}
        self.assertEqual(declared, set(tally.DEFAULTS))

    def test_the_declared_default_is_the_one_the_code_uses(self):
        for field in self.manifest["contributes"]["settings"]:
            self.assertEqual(field["default"], tally.DEFAULTS[field["key"]], field["key"])

    def test_the_panel_the_process_draws_is_the_one_declared(self):
        import ledger
        self.assertIn(ledger.PANEL, [p["id"] for p in self.manifest["contributes"]["panels"]])

    def test_it_asks_for_no_more_than_read(self):
        # It only ever reads. A wider scope would be a request a reviewer has
        # to weigh for nothing.
        self.assertEqual(self.manifest["scope"], "read")


if __name__ == "__main__":
    unittest.main()


class StateTest(unittest.TestCase):
    """What survives a restart. A local install is a copy, so the plugin is
    stopped and started again often, and anything it only held in memory reads
    afterwards as "nothing ever happened"."""

    def setUp(self):
        import tempfile
        import ledger
        self.ledger = ledger
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.saved = (ledger.DATA, ledger.STATE, ledger.LEDGER)
        ledger.DATA, ledger.STATE, ledger.LEDGER = root, root / "state.json", root / "ledger.jsonl"

    def tearDown(self):
        self.ledger.DATA, self.ledger.STATE, self.ledger.LEDGER = self.saved
        self.tmp.cleanup()

    def restarted(self, events):
        first = self.ledger.Ledger()
        first.load()
        first.ingest(events)
        again = self.ledger.Ledger()
        again.load()
        return first, again

    def test_the_subagent_tally_survives_a_restart(self):
        # The bookmark has already passed those events, so a tally that starts
        # empty stays empty: the panel would say no subagent ever ran.
        _, again = self.restarted([turn(100_000, eid=1),
                                   turn(50_000, agent="a", model="claude-opus-5", eid=2),
                                   turn(50_000, agent="b", model="claude-opus-5", eid=3)])
        self.assertEqual(again.subs, {"claude-opus-5": 2})

    def test_main_turns_and_the_bookmark_survive_a_restart(self):
        first, again = self.restarted([turn(100_000, eid=7), turn(300_000, eid=9)])
        self.assertEqual(len(again.rows), 2)
        self.assertEqual(again.last_id, first.last_id)

    def test_a_batch_of_only_subagent_turns_still_moves_the_bookmark(self):
        _, again = self.restarted([turn(50_000, agent="a", eid=4)])
        self.assertEqual(again.last_id, 4)

    def test_a_half_written_state_file_is_a_fresh_start_not_a_crash(self):
        self.ledger.STATE.write_text("{not json")
        fresh = self.ledger.Ledger()
        fresh.load()
        self.assertEqual((fresh.last_id, fresh.subs), (0, {}))

    def test_subagent_turns_behind_the_bookmark_are_seeded_once(self):
        first = self.ledger.Ledger()
        first.load()
        events = [turn(100_000, eid=1),
                  turn(50_000, agent="a", model="claude-opus-5", eid=2),
                  turn(50_000, agent="b", model="claude-opus-5", eid=3)]
        first.ingest(events)
        # A restart with a tally saved before subagents were persisted.
        self.ledger.STATE.write_text(json.dumps({"lastId": 3}))
        again = self.ledger.Ledger()
        again.load()
        again.seed_subs(events, 3)
        self.assertEqual(again.subs, {"claude-opus-5": 2})

    def test_seeding_never_adds_to_a_tally_that_already_has_one(self):
        again = self.ledger.Ledger()
        again.subs = {"claude-opus-5": 2}
        again.seed_subs([turn(50_000, agent="a", model="claude-opus-5", eid=2)], 9)
        self.assertEqual(again.subs, {"claude-opus-5": 2})

    def test_seeding_skips_what_ingest_already_counted(self):
        # Newer than the mark: ingest owns it, and counting it here as well
        # would double every turn on a restart.
        again = self.ledger.Ledger()
        again.seed_subs([turn(50_000, agent="a", model="claude-opus-5", eid=10)], 3)
        self.assertEqual(again.subs, {})


class SessionTest(unittest.TestCase):
    """The question a person acts on is not "how was my month", it is "is the
    window I am in right now delegating anything"."""

    def setUp(self):
        self.cfg = dict(tally.DEFAULTS)
        self.rows, self.subs, self.sessions = [], {}, {}

    def feed(self, events):
        for e in events:
            tally.take(e, self.rows, self.subs, self.cfg, self.sessions)

    def test_main_and_delegated_are_counted_per_session(self):
        a = [turn(300_000, at=NOW, eid=1), turn(50_000, agent="x", at=NOW + 1, eid=2)]
        b = [turn(90_000, at=NOW + 2, eid=3)]
        for e in a:
            e["session_id"] = "orbit"
        for e in b:
            e["session_id"] = "acme"
        self.feed(a + b)
        self.assertEqual(self.sessions["orbit"]["main"], 1)
        self.assertEqual(self.sessions["orbit"]["sub"], 1)
        self.assertEqual(self.sessions["acme"],
                         {"main": 1, "sub": 0, "t": NOW + 2, "ctx": 90_000, "subctx": 0})

    def test_what_a_worker_read_is_counted_and_never_becomes_main_context(self):
        # The number a person acts on: read once over there, so it is never
        # re-read here. It must not leak into the main-thread figures.
        a = turn(300_000, at=NOW, eid=1)
        b = turn(40_000, agent="x", at=NOW + 1, eid=2)
        c = turn(18_200, agent="x", at=NOW + 2, eid=3)
        for e in (a, b, c):
            e["session_id"] = "orbit"
        self.feed([a, b, c])
        self.assertEqual(self.sessions["orbit"]["subctx"], 58_200)
        self.assertEqual(self.sessions["orbit"]["ctx"], 300_000)
        self.assertEqual(self.sessions["orbit"]["main"], 1)

    def test_the_session_shown_is_the_one_that_moved_last(self):
        old = turn(300_000, at=NOW - 10 * HOUR, eid=1)
        new = turn(90_000, at=NOW, eid=2)
        old["session_id"], new["session_id"] = "stale", "live"
        self.feed([old, new])
        self.assertEqual(tally.newest_session(self.sessions)["ctx"], 90_000)

    def test_no_sessions_leaves_the_section_out_rather_than_drawing_zeroes(self):
        self.feed([turn(90_000, eid=1)])
        node = tally.tree(tally.stats(self.rows, self.cfg, NOW), self.subs, self.cfg, {}, None)
        self.assertNotIn("The session that moved last",
                         [c.get("title") for c in node["children"]])

    def test_a_session_with_nothing_delegated_is_flagged(self):
        e = turn(300_000, eid=1)
        e["session_id"] = "orbit"
        self.feed([e])
        node = tally.tree(tally.stats(self.rows, self.cfg, NOW), self.subs, self.cfg, {}, self.sessions)
        section = [c for c in node["children"] if c.get("title") == "The session that moved last"][0]
        tones = [n.get("tone") for n in section["children"][0]["children"]]
        self.assertIn("warning", tones)


class AgentModelTest(unittest.TestCase):
    """The one thing this plugin decides outright.

    Whether the main thread delegates is a judgement it makes each time, and
    an eval can only report how often. WHICH model a delegation lands on is
    not a judgement: it is a line in the agent's frontmatter, and without it
    the work goes to whatever the session's default subagent model happens to
    be — Opus, on a machine where nobody set one. Haiku reads cache at a fifth
    of what Opus does, so this line is the saving, and a rename that drops it
    would go unnoticed by every other test here.
    """

    PINNED = {"scout": "haiku", "verifier": "haiku", "builder": "sonnet"}

    def frontmatter(self, name):
        text = (ROOT / "agents" / f"{name}.md").read_text()
        self.assertTrue(text.startswith("---\n"), f"{name}.md has no frontmatter")
        return text.split("---\n")[1]

    def test_every_agent_pins_its_model(self):
        for name, model in self.PINNED.items():
            front = self.frontmatter(name)
            self.assertRegex(front, rf"(?m)^model:\s*{model}\s*$",
                             f"{name} must pin model: {model}")

    def test_the_cheap_agents_are_the_reading_ones(self):
        # scout and verifier exist to read; if either drifts up to the
        # expensive model the plugin is paying full price to save nothing.
        for name in ("scout", "verifier"):
            self.assertNotIn("opus", self.frontmatter(name))

    def test_every_shipped_agent_is_covered_here(self):
        # A new agent with no pin would inherit the session default in silence.
        shipped = {p.stem for p in (ROOT / "agents").glob("*.md")}
        self.assertEqual(shipped, set(self.PINNED))
