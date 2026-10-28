# KeptWord

**Public pledges with skin in the game, verified from public web evidence.**
A GenLayer Intelligent Contract for Studionet.

You make a public promise ("I will publish a garden post every day for three days"),
lock a stake of in-contract points (WP), and name a public web page where the proof
will appear. The pledge is cut into equal checkpoint windows. After each window closes,
anyone can ask the GenLayer validators to check the page. Keep your word and you get
your stake back (plus rewards from sponsors). Break it and the stake is lost, half of
it compensating your sponsors.

```
contracts/kept_word.py     the Intelligent Contract (one file)
tests/                     offline test suite (pure Python, zero dependencies)
tests/sim/                 strict local stand-in for the GenLayer SDK
scripts/mutation_check.py  plants 41 bugs, checks the tests catch every one
e2e/                       deploy, live smoke and one-command live report scripts for Studionet (genlayer-js)
```

## The idea: the AI finds evidence, plain Python decides

A common weakness of AI-judged contracts is that the model outputs the verdict, so
anyone who can influence the page can try to talk the model into the answer they want.
KeptWord never asks the model for a verdict.

For every checkpoint the model only has to **find dated quotes** on the proof page that
show the promise being carried out. The contract then applies its own rules in Python:

1. every entry needs a `YYYY-MM-DD` date, and that date must lie inside the checkpoint window,
2. every quote must be a **verbatim substring** of the fetched page (a made-up quote is dropped),
3. quotes may not **overlap** (one page entry cannot be counted twice),
4. the checkpoint is **MET** when the number of surviving quotes reaches the pledge's
   `min_items`, otherwise **MISSED**.

### Consensus (`gl.vm.run_nondet_unsafe`)

The leader fetches the page and runs the model. Each validator then:

* re-fetches the page itself and checks, in Python, that **every leader quote is verbatim and distinct in the validator's own snapshot**,
* runs **its own independent model call** and requires it to land on the **same side of the `min_items` threshold**,
* only believes a reported "page unreachable" if it cannot read the page either,
* never accepts a model failure (the transaction simply does not settle and can be retried).

A lazy leader ("I saw nothing") or a leader that quotes an injected line cannot get
through, because the validators' own models disagree.

## Pledge life cycle

```
                        too many missed checkpoints              appeal fails / window ends
ACTIVE ───────────────────────────────────────────▶ BREACHED ──────────────────────────────▶ BROKEN
  │ ▲                                                   │
  │ └────────────────── appeal wins ────────────────────┘
  │
  ├── all checkpoints done, misses within tolerance ───▶ KEPT
  ├── owner cancels before the start ──────────────────▶ CANCELLED (everyone refunded)
  └── nothing settled for 7 days after a window opened ▶ ABORTED   (everyone refunded)
```

A pledge may miss at most `checkpoints - min_met` checkpoints. The first time that
tolerance is exceeded it is **BREACHED**: the owner has 6 hours to appeal **once**,
pointing the validators at a *different evidence URL* for the breaching window
(10 WP fee, burned). A won appeal flips that checkpoint to MET and the pledge continues.

Timing (all UTC, derived from the transaction timestamp):

| | |
|---|---|
| `start_at` | 5 minutes to 90 days in the future |
| window `i` | `[start_at + i*interval, start_at + (i+1)*interval)` |
| `interval_seconds` | 10 minutes to 30 days |
| a checkpoint can be verified | after its window **plus a grace period** of `min(interval/4, 6h)` so late-published entries dated inside the window still count |
| unreachable proof page | 3 failed attempts (5 minute cooldown each) record a MISS |
| appeal window | 6 hours after the breach |
| stall escape | 7 days after a checkpoint opened |

## Points economy (no native tokens are touched)

* `claim_starter_points()` gives every address **1000 WP**, once.
* Owner stake: 20 to 500 WP, locked. Sponsors lock 5 to 500 WP before the pledge starts (max 20 sponsors).

| Ending | Owner | Sponsors |
|---|---|---|
| KEPT | stake back **+ all sponsor rewards** | their locked WP goes to the owner |
| BROKEN | loses the whole stake | WP back **+ pro-rata share of 50 % of the stake** (rounded down; the rest is burned) |
| CANCELLED / ABORTED | stake back | WP back |

Invariant (checked by the tests after every operation):
`sum(free) + sum(locked) + burned == sum(grants)`.

Reputation per address: pledges kept / broken, checkpoint hit rate, current and best
streak, sponsor record, and a Laplace-smoothed `trust_bps = (kept+1)/(kept+broken+2)`.

## Public API

Writes

| method | who | what |
|---|---|---|
| `claim_starter_points()` | anyone, once | 1000 WP |
| `open_pledge(title, commitment, proof_url, start_at, interval_seconds, checkpoints, min_met, min_items, stake)` | anyone | locks the stake; the proof page must be readable by the validators |
| `sponsor_pledge(pledge_id, amount)` | not the owner, before the start | locks a reward |
| `cancel_pledge(pledge_id)` | owner, before the start | full refund |
| `verify_checkpoint(pledge_id)` | **anyone**, once the checkpoint is open | validators check the next window |
| `appeal_breach(pledge_id, evidence_url)` | owner, once, within 6 h of a breach | re-judges the breaching window |
| `finalize_breach(pledge_id)` | anyone, after the appeal window | settles a breach as BROKEN |
| `abort_stalled(pledge_id)` | anyone, after 7 days of stall | refunds everyone (no web, no model) |

Views: `get_version`, `get_config`, `get_stats`, `get_standing(address)`,
`get_profile`, `list_profiles`, `get_pledge`, `list_pledges`, `list_owner_pledges`,
`get_checkpoint`, `list_pledge_checkpoints`, `list_pledge_sponsorships`,
`list_sponsor_sponsorships`, `get_schedule(pledge_id)`, `preview_outcomes(pledge_id)`.

Error messages are tagged `[EXPECTED]` (a rule was violated) or `[EXTERNAL]` (web / model problem).

## Deploying on Studionet

You need Node.js 20+ and nothing else.

```bash
npm install
npm run deploy                      # throw-away key, funded via sim_fundAccount; prints the address
KW_ADDRESS=0x... npm run smoke      # claim -> open -> sponsor -> cancel, about 5 minutes
```

Use your own key with `KW_PRIVATE_KEY=0x... npm run deploy`.

**Using GenLayer Studio instead:** open the contract file in Studio, deploy with no
constructor arguments, then call the methods from the UI. `start_at` is a Unix
timestamp in seconds, for example `date -d "+10 minutes" +%s`.

### One-command live run with a report

```bash
npm install
npm run live:quick     # deploy, claim, open, sponsor, cancel: about 8 minutes
npm run live           # full path including checkpoint verification: about 25 minutes
```

Both print a report at the end and save it to `reports/live-report.md` and `reports/live-report.json`
(steps, transaction links, points after every stage, checkpoint evidence, and the last status seen for
every transaction, including whether it reached FINALIZED). Optional variables: `KW_ADDRESS` (reuse a
deployed contract), `KW_PRIVATE_KEY`, `KW_PROOF_URL` (use a public page that shows entries dated today
so the checkpoint is MET and the pledge ends KEPT), `KW_FINAL_WAIT_MIN`.

### A quick live demo

1. Two accounts call `claim_starter_points`.
2. Account A calls `open_pledge` with `start_at` = now + 6 min, `interval_seconds` = 600,
   `checkpoints` = 2, `min_met` = 1, `min_items` = 1, `stake` = 50, and a `proof_url`
   that shows dated entries (a public GitHub commits page works well for a demo).
3. Account B calls `sponsor_pledge(1, 20)` before the start.
4. During the first window, publish something on the proof page.
5. About 13 minutes after the start (window end plus 150 s grace), anyone calls
   `verify_checkpoint(1)`. Read `get_checkpoint(1)` for the quotes the validators accepted.
6. Watch `get_schedule(1)`, `get_standing(...)` and `preview_outcomes(1)`.

`KW_ADDRESS=0x... npm run smoke:verify` automates this against a fixed page and waits
for the checkpoint (about 25 minutes).

## Tests

```bash
npm test                # or: python3 -m unittest discover -s tests -p "test_*.py"
npm run test:fast       # fewer random seeds
npm run mutation        # plants 41 bugs, one at a time, and demands the tests catch each
```

No packages are installed: the tests are plain `unittest` and also run under `pytest`.

* **Unit and scenario tests** cover creation rules, URL safety (101 vectors), sponsoring,
  cancelling, every checkpoint rule, quote grounding, unreachable pages, model failures,
  breach, appeal, finalize, abort and exact settlement arithmetic.
* **Consensus attack tests** run the contract's real validator function against forged
  leader results, lazy and obedient leaders, stale and divergent validators.
* **Differential random test**: thousands of random operations (valid and invalid, with
  boundary timestamps) run against the contract and an independent reference model
  written from this README only; outcomes and state must match after every step.
* **Every test runs twice**, with and without rollback. Without rollback a rejected
  call keeps whatever it wrote, which proves every check happens before any write.
* **Mutation check**: 41 deliberately planted bugs; every one is caught.

### What the tests do and do not prove

The tests run on `tests/sim`, a strict local stand-in for the GenLayer SDK. It rejects a
plain `int` written to a `u256` field, copies dataclasses into storage, forbids storage
access inside non-deterministic blocks, checks that views do not change state, and
returns mixed-case (EIP-55) addresses. That catches logic and SDK-discipline bugs.

It is **not GenVM**. It cannot judge how a real LLM behaves on your prompt, how real
pages render, real consensus timing, or any quirk that only the real runtime has. The
contract only uses SDK calls that appear in the earlier ForesightLedger project, but
the final check is a run on Studionet (`npm run smoke`). `e2e/*.mjs` were syntax-checked
but could not be executed in the build environment (no network).

## Limitations and design notes

* **The proof page decides what the validators can see.** If the owner controls the
  page, the owner can forge dated entries. Sponsors should only back pledges whose proof
  lives on a site the owner cannot freely edit (a public commit history, a third-party
  platform). The contract proves *what the page showed*, not that the page is honest.
* Python verifies that quotes exist and that their claimed dates lie in the window;
  whether an entry is *relevant* and *truly dated that day* is judged by independent
  model runs on the validators. Window checks are at calendar-day granularity.
* WP is a play/reputation currency. Starter grants are free, so Sybil addresses are
  possible and nothing here has monetary value.
* A pledge only settles when someone calls `verify_checkpoint`. Anyone may call it, and
  sponsors have every reason to. If nobody does for 7 days the pledge can be aborted and
  everyone is refunded, so sponsors should verify promptly.
* Page text is capped at 8000 characters; entries further down are invisible to the
  model, so choose a proof page that lists recent entries first.
* Entries are matched case- and whitespace-insensitively against the collapsed page text.
