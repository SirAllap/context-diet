"""The tally itself: turns in, numbers and a panel out.

Nothing here touches the network or the disk, so the whole of it is testable
from a list of dictionaries — which is the shape agentglass hands over anyway.
`ledger.py` is the process around it.

The one idea: a turn's context is what it had to read to answer, and on the
main thread almost all of that is a cache read of everything said so far. The
tally counts how many turns read more than a cap and how much of the re-reading
those turns carry, because that second number is the one that decides whether
clearing earlier is worth anything.
"""
from __future__ import annotations

DEFAULTS = {"cap": 200_000, "ceiling": 1_000_000, "windowDays": 30, "pollSeconds": 20}

# Where the two tones come from. Both were measured over a month of real turns,
# and both are drawn beside the live figure rather than hidden here, so a person
# can see whether their own machine sits above or below the baseline.
#
#   share of main turns above a 200K cap .......... 73.7% (events store)
#                                                   79.3% (agent transcripts)
#   share of turns above 800K ..................... 12.5%, carrying 26.2%
#                                                   of every cache-read token
#
# The two sources disagree because the events store under-reports: it records
# what it was told, and it is told about a turn only when a tool runs.
WARN_OVER_SHARE = 0.737
DANGER_CTX = 800_000

BAND_MID = 800_000


def config(raw: object) -> dict:
    """Settings as the app returns them, with anything missing or nonsense
    falling back to the default rather than to zero — a cap of 0 would put
    every turn over it and a ceiling of 0 would drop the lot."""
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key, default in DEFAULTS.items():
            value = raw.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and value > 0:
                out[key] = int(value)
    return out


def context_of(event: dict) -> int:
    """What the model had to read for this turn.

    The three input classes added together, which is agentglass's own
    definition of a session's context — output is what came back and is not
    read again.
    """
    total = 0
    for key in ("input_tokens", "cache_read_tokens", "cache_creation_tokens"):
        value = event.get(key)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def seen(sessions: dict[str, dict], sid: str, at: int) -> dict:
    row = sessions.setdefault(sid, {"main": 0, "sub": 0, "t": 0, "ctx": 0, "subctx": 0})
    row.setdefault("subctx", 0)  # a session counted before this field existed
    row["t"] = max(row["t"], at)
    return row


def take(event: dict, rows: list[dict], subs: dict[str, int], cfg: dict,
         sessions: dict[str, dict] | None = None) -> str:
    """Sort one event into the tally. Returns what was done with it.

    `agent_id` is the main/subagent split: the app sets it on every turn a
    subagent produced and leaves it null on the main thread, so no heuristic
    is needed and none is used here.

    `sessions` is the same counting kept per session, so the panel can answer
    "is the session I am in right now delegating anything" — which is the
    question a person actually acts on. Optional: the month's figures do not
    need it and the callers that only want those should not have to carry it.
    """
    ctx = context_of(event)
    if ctx <= 0:
        return "skip"  # an event that carries no usage — a plain hook line
    model = event.get("model_name") or "unknown"
    sid = str(event.get("session_id") or "?")
    at = int(event.get("timestamp") or 0)
    if event.get("agent_id"):
        subs[model] = subs.get(model, 0) + 1
        if sessions is not None:
            row = seen(sessions, sid, at)
            row["sub"] += 1
            # What the worker read. This is the whole point of the number: it
            # was read once, over there, and never entered this thread — so it
            # is never re-read, whatever happens for the rest of the session.
            row["subctx"] += ctx
        return "sub"
    if ctx > cfg["ceiling"]:
        # The first usage-bearing event of a session carries the whole
        # cumulative transcript rather than that turn's share, because the
        # subtraction that makes a delta needs a previous total and there
        # isn't one yet. Measured on a real cockpit database: 156 such rows,
        # the largest of them 137,777,956 tokens — 137 times the biggest
        # context window that exists.
        #
        # Dropped rather than clamped. A clamp invents a turn at exactly the
        # ceiling and leaves it in the numerator, which is how a guard ends up
        # inventing the number it was added to protect.
        return "dropped"
    if sessions is not None:
        row = seen(sessions, sid, at)
        row["main"] += 1
        if at >= row["t"]:
            row["ctx"] = ctx
    rows.append({
        "t": int(event.get("timestamp") or 0),
        "ctx": ctx,
        "cr": int(event.get("cache_read_tokens") or 0),
        "usd": float(event.get("cost_usd") or 0.0),
        "model": model,
    })
    return "main"


def window(rows: list[dict], cfg: dict, now_ms: int) -> list[dict]:
    first = now_ms - cfg["windowDays"] * 86_400_000
    return [r for r in rows if r["t"] >= first]


def stats(rows: list[dict], cfg: dict, now_ms: int) -> dict:
    """Every number the panel prints, from the turns in the window."""
    w = window(rows, cfg, now_ms)
    cap = cfg["cap"]
    ctx_sum = sum(r["ctx"] for r in w)
    cr_sum = sum(r["cr"] for r in w)
    over = [r for r in w if r["ctx"] > cap]
    capped = sum(min(r["ctx"], cap) for r in w)

    def band(low: int, high: int) -> dict:
        rows_in = [r for r in w if low < r["ctx"] <= high]
        return {
            "turns": len(rows_in),
            "share": len(rows_in) / len(w) if w else 0.0,
            "carries": sum(r["cr"] for r in rows_in) / cr_sum if cr_sum else 0.0,
        }

    return {
        "turns": len(w),
        "over": len(over),
        # Share of turns above the cap, and — the number that matters — the
        # share of all re-reading those turns are responsible for. A handful
        # of enormous turns can carry most of it, and then the habit worth
        # changing is a small one.
        "overShare": len(over) / len(w) if w else 0.0,
        "overCarries": sum(r["cr"] for r in over) / cr_sum if cr_sum else 0.0,
        "overUsd": sum(r["usd"] for r in over),
        # What a real cap would leave: every turn charged at most the cap.
        # It is a floor, not a promise — a cap also forces a re-prime that
        # this does not model, and modelling it would be inventing a number.
        "wouldLeave": capped / ctx_sum if ctx_sum else 0.0,
        "bands": [band(0, cap), band(cap, BAND_MID), band(BAND_MID, cfg["ceiling"])],
        "newest": w[-1] if w else None,
    }


def human(n: float) -> str:
    n = float(n)
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{round(n / 1e3)}K"
    return str(int(n))


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def newest_session(sessions: dict[str, dict]) -> dict | None:
    """The session whose last turn is the most recent — the one the person is
    almost certainly looking at. Not perfect with two open at once, and the
    panel says so rather than pretending to know which window has focus."""
    if not sessions:
        return None
    return max(sessions.values(), key=lambda r: r["t"])


def tree(s: dict, subs: dict[str, int], cfg: dict, book: dict,
         sessions: dict[str, dict] | None = None) -> dict:
    """The panel, in the vocabulary the app draws.

    There is no chart node, so the distribution is three bars and there is no
    time series at all. A fourth bar pretending to be one would be worse than
    the gap.
    """
    cap = cfg["cap"]
    live = newest_session(sessions or {})
    if not s["turns"]:
        return {"type": "empty", "title": "Nothing counted yet",
                "body": "Usage arrives when an agent runs a tool. Work for a minute and this fills in."}

    headline = [
        {"type": "stat", "label": f"Over {human(cap)}",
         "value": f"{s['over']} of {s['turns']}",
         "tone": "warning" if s["overShare"] > WARN_OVER_SHARE else "default",
         "hint": f"{pct(s['overShare'])} of main-thread turns. Measured baselines: 73.7% and 79.3%."},
        {"type": "stat", "label": "They carry",
         "value": pct(s["overCarries"]), "tone": "warning",
         "hint": "of every cache-read token on the main thread"},
        {"type": "stat", "label": "They cost",
         "value": f"${s['overUsd']:.2f}",
         "hint": "what those turns did cost, priced per turn by agentglass. Not a counterfactual."},
    ]
    newest = s["newest"]
    if newest and newest["ctx"] > DANGER_CTX:
        headline.append({"type": "stat", "label": "Newest turn", "value": human(newest["ctx"]),
                         "tone": "danger",
                         "hint": "above 800K, where an eighth of turns carry a quarter of all re-reading"})

    labels = [f"under {human(cap)}", f"{human(cap)}–{human(BAND_MID)}", f"over {human(BAND_MID)}"]
    bands = [
        {"type": "progress", "value": b["share"],
         "label": f"{name} · {b['turns']} turns · {pct(b['carries'])} of re-reading"}
        for name, b in zip(labels, s["bands"])
    ]

    ranked = sorted(subs.items(), key=lambda kv: -kv[1])[:6]
    sub_items = []
    for model, n in ranked:
        item = {"label": model, "value": f"{n} turns"}
        if "haiku" in model.lower():
            item["tone"] = "success"  # the cheap side of the only 5x line here
        sub_items.append(item)

    return {"type": "stack", "gap": "md", "children": [
        {"type": "section", "title": f"{s['turns']} main turns · last {cfg['windowDays']} days",
         "children": [
             {"type": "row", "gap": "md", "wrap": True, "children": headline},
             {"type": "progress", "value": s["wouldLeave"],
              "label": f"a {human(cap)} cap would leave {pct(s['wouldLeave'])} of what you re-read"},
         ]},
        *([{"type": "section", "title": "The session that moved last",
            "subtitle": "with two windows open this is the busier one, not the focused one",
            "children": [
                {"type": "row", "gap": "md", "wrap": True, "children": [
                    {"type": "stat", "label": "Main turns", "value": str(live["main"]),
                     "hint": "read here, and re-read on every turn after"},
                    {"type": "stat", "label": "Delegated",
                     "value": str(live["sub"]),
                     "tone": "success" if live["sub"] else "warning",
                     "hint": "read once inside a worker and thrown away"},
                    {"type": "stat", "label": "Read in a worker", "value": human(live.get("subctx", 0)),
                     "tone": "success" if live.get("subctx") else "default",
                     "hint": "read once over there and thrown away. It never entered this "
                             "thread, so it is never re-read. Counted from the moment this "
                             "started watching, and a floor even then: the store "
                             "under-reports the subagent side."},
                    {"type": "stat", "label": "Newest turn read", "value": human(live["ctx"]),
                     "tone": "danger" if live["ctx"] > DANGER_CTX else "default",
                     "hint": f"the cap is {human(cap)}"},
                ]},
                {"type": "text", "size": "sm", "tone": "muted",
                 "text": "Nothing delegated in a long session is the habit lapsing. "
                         "Delegating does not shrink what is already here — for that, start fresh."
                         if not live["sub"] else
                         "Every delegated turn is reading that never comes back to this thread."},
            ]}] if live else []),
        {"type": "section", "title": "Where the turns sit", "children": bands},
        {"type": "section", "title": "Subagents",
         "subtitle": "a ratio, never a total — this side is under-reported",
         "children": [
             {"type": "keyValue", "items": sub_items} if sub_items else
             {"type": "text", "size": "sm", "tone": "muted", "text": "No subagent turns counted yet."},
             {"type": "text", "size": "sm", "tone": "muted",
              "text": "Haiku reads cache at a fifth of what Opus does. Setting "
                      "CLAUDE_CODE_SUBAGENT_MODEL moves this side and costs nothing."},
         ]},
        {"type": "section", "title": "Bookkeeping", "children": [
            {"type": "keyValue", "items": [
                {"label": "dropped above the ceiling", "value": str(book.get("dropped", 0))},
                {"label": "read at start", "value": f"{book.get('backfilled', 0)} events"},
                {"label": "went past while not looking", "value": str(book.get("missed", 0)),
                 **({"tone": "warning"} if book.get("missed") else {})},
                {"label": "last turn seen", "value": book.get("lastSeen", "—")},
            ]},
            {"type": "text", "size": "sm", "tone": "muted",
             "text": "Turns are read every few seconds, and usage itself is a few "
                     "seconds behind that. This is a standing tally, not a live meter."},
        ]},
    ]}
