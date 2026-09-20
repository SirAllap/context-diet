---
name: context-diet
description: >
  Run a substantial build, migration or refactor with the expensive thread kept
  small — it writes the contract and accepts the result; workers do the reading,
  the building and the checking. Use for multi-file work, for anything that will
  take more than a few turns, and whenever a session is getting long and slow.
  Skip it for a typo, a question, or a one-file edit: delegation costs a round
  trip and a typo does not earn one.
---

# The expensive thread decides. Workers do the work.

## Why this exists, measured

Over a month of real sessions, **98.2% of every token was a cache read** — the
conversation so far, read again, on every turn. The main thread carried **82%**
of that. Capping what the main thread holds at 200K would have cut **37% of the
month** without downgrading a single model or removing a single turn.

So the thing to protect is not how much you write. It is **what ends up in the
main thread's context, because it is re-read forever after**. A test log read
once on the main thread is read again on every turn for the rest of the session.
Read in a worker, it is read once and never again.

That is the whole idea. Everything below follows from it.

## The loop

One planning pass, one dispatch, one wait, one batched review, one answer.

**1 — Decide, do not explore.** Read the repository's own guidance and the
request. Decide whether this is a direct small fix or a bounded build. A small
fix stays here; do not force delegation onto something that does not earn it.

**2 — Find out what you need through `scout`, never by reading the repo.**
Where the thing lives, what already does it, what shape the data has: ask
`scout` and get a file:line table back. A grep on the main thread costs its
output forever; the same grep in `scout` costs it once.

**3 — Write the contract.** Objective, the interfaces that must not move, the
files in scope, what "done" means, and the exact commands that prove it. This
is the expensive thread's real work and the one thing a worker cannot do for
it. Architecture, auth, tenancy, payments, secrets, destructive migrations and
production behaviour are decided here and written down — never handed to a
worker under a vague "build it".

**4 — Dispatch one worker and let it finish.** One `builder` at a time, in a
worktree of its own branched from the integration branch — never in a checkout
somebody else is also sitting in. Give it the contract, the scope, and the
dependency outputs it needs — nothing else. Then **let it work**: no progress checks, no "how is it
going", no reading its output as it goes. A wait that feels long is not
evidence that anything is stuck.

**5 — Review the result, once, in one pass.** Read the actual diff. Two lenses
in the same pass: does it meet the contract, and is the code sound. Spot-check
where the evidence is missing or the risk justifies it; do not re-run the
worker's whole suite to watch it pass again. If there are corrections, send
**all** of them back to the same worker in **one** message. Default to one
correction cycle, then accept or reconsider the scope.

**6 — Check and accept.** Run the cross-cutting check once, at a real
integration boundary, through `verifier` — which brings back the counts, not
the log.

## What never comes back to the main thread

- **Test and build output.** `verifier` returns "172 passed" or the one failing
  assertion. Never the log.
- **File contents the worker already read.** If the worker read it, the main
  thread does not need to.
- **A worker's working notes.** The diff and the evidence are the deliverable.
- **Progress.** Asking costs a turn and the answer is always "still going".

## When a session is already long

Delegating does not shrink what is already in the context — it only stops it
growing. The lever for what is already there is clearing between unrelated
tasks. Finish the thing, then start fresh; a task that shares nothing with the
last one gains nothing from carrying it.

## What this cannot do

It does not make a weak plan work. A contract that is vague comes back as code
that is wrong, and the review then costs more than the delegation saved — which
is the honest failure mode of this pattern and the reason step 3 is the one
step that is not optional.

It does not stop you pasting a log into the main thread yourself. Nothing here
can.

And it saves nothing on a task small enough that the round trip is the
expensive part. Below roughly a handful of turns, just do it.
