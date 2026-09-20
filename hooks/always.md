Keep this thread small. Almost every token here is the conversation so far,
read again, on every turn — measured at 98% over a month of real sessions. A
file read in a worker is read once; a file read here is re-read forever.

So, in a repository, these are rules and not suggestions:

- **Before you grep or read to find something, dispatch `scout`.** Not after
  a quick look first — instead of it. If your next tool call was going to be
  `Grep`, `Glob`, or a `Read` of a file you have not already been given, it is
  a `Task` to `scout` and you pass its question through verbatim.
- **Before you run anything whose output is long** — a test suite, a build, a
  linter — dispatch `verifier`. You want the counts, not the log.
- **Before a multi-file change**, write the contract, then dispatch `builder`.
  Do not explore the repository yourself to work out what the contract should
  say; that is what `scout` is for.
- **Never paste a log, a whole file, or a worker's transcript back into this
  thread.** Quote the one decisive line.
- **Pass a worker's answer through in the shape it came back.** A `file:line`
  list arrives as plain lines because a markdown table spends a third of its
  characters on pipes and padding — re-typesetting it here puts back exactly
  the cost the worker just saved.

Answering well after reading it all yourself is the failure this prevents: the
answer is right and the context now carries every line, for the rest of the
session.

Load the `context-diet` skill for the whole discipline. A typo, a single
question about something already on screen, and a change to a file you were
handed need none of this.
