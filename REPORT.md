# REPORT

## 1. Architecture

The system has two modes: **Discovery** and **Replay**. They share three parts underneath them.
Look at the diagram below while you read this section — the names match exactly.

Here is the system diagram:
<img width="1400" height="900" alt="bankgpt_architecture" src="https://github.com/user-attachments/assets/7e735c14-e066-46c7-be2b-45e0e0ac4aa5" />

Here is the corresponding interactive diagram as well:
<img width="1400" height="1060" alt="bankgpt_discovery_sequence" src="https://github.com/user-attachments/assets/535feffe-39a2-457e-8deb-12123006c637" />

**The two modes (top of the diagram):**

- **Discovery** (blue box) runs once per task. An AI model watches the screen, decides what to
  click or type, and does it. This is slow and needs an AI model, but it only has to happen once
  per task.
- **Replay** (green box) runs every time after that. It follows the exact steps saved from
  Discovery. It does not use an AI model at all. This is fast and always behaves the same way.

**The shared parts underneath (amber boxes):** both Discovery and Replay use the exact same three
things to actually touch the screen and stay safe:

- **Surface Adapter** — the only piece of code allowed to click, type, or read the screen. Both
  modes call it the same way. Neither mode ever touches the browser directly.
- **Policy Engine** — checks every single action before it happens. "Is this website allowed? Is
  this a safe kind of action?" If not, it stops the action and asks a human.
- **Evidence Store** — writes down everything that happens: every action, every screenshot, every
  result. This is the audit trail.

**How a task moves through the system, step by step:**

1. Discovery watches the screen, decides on one action, sends it to the Policy Engine.
2. The Policy Engine checks it's allowed, then hands it to the Surface Adapter.
3. The Surface Adapter does the click/type on the real screen.
4. The Evidence Store logs what happened.
5. If Discovery succeeds, the **Recorder** turns the whole trace into a saved **Capability
   Artifact** — a file describing the task so it can be repeated later without AI.
6. Later, Replay reads that file and does the same steps again, through the same Policy Engine and
   Surface Adapter — no AI involved this time.
7. If anything goes wrong or needs a judgment call, **Escalation** (pink box) pauses the run and
   hands the screen to a real person.

## 2. Artifact schema

The **Capability Artifact** is a single file that describes one task completely: what it needs,
what it does step by step, and what it returns. This is the file Discovery creates and Replay
reads. `src/types/artifact.py` defines its exact shape.

What's inside it, in plain terms:

- **Inputs / outputs** — what the task needs to run (like a member ID) and what it gives back
  (like a balance). Inputs marked `sensitive` (like a password) get hidden everywhere the system
  writes logs.
- **Steps** — an ordered list of actions: click this, type that, check this loaded. Each step
  saves several ways to find the same element on screen (not just one), ranked from most reliable
  to least reliable. If the top way fails, the system tries the next one down the list before
  giving up.
- **Recognized outcomes** — a list of "known situations" for each step, like "member not found" or
  "session expired," and what to do if one happens: treat it as a normal result, retry once, or
  ask a human. I write these down by hand when I build the artifact — the system can't learn them
  automatically, because a single successful run never actually sees its own failure cases.
- **Base URL** — the one part of the file that changes if you point the same task at a different
  bank/tenant. Everything else in the file stays the same.

## 3. Determinism & error handling

Every step in Replay checks a **checkpoint** afterward — "did the page actually reach the state I
expected," not just "did the click work without an error." If a locator (a way of finding an
element) fails, the system tries the next backup locator before giving up.

One bug I caught and fixed: when Discovery records a step like "search for member 12345," it
would, by default, save the exact URL that resulted, like `/member/12345`. That's a problem,
because Replay needs to work for *any* member ID, not just 12345. Fix: before saving, the system
replaces the specific ID in that URL with a wildcard pattern, so it becomes `/member/[anything]`
and works for every member.

When a checkpoint fails, the system checks the page against the list of "recognized outcomes" for
that exact step:

- **Business outcome** (e.g. "member not found") → this is a normal answer, not an error. Returned
  to the caller as a clear, named result.
- **Recoverable** → try the saved fix once (like clicking a "retry" button), check again, then
  continue if it worked.
- **Escalate** → stop and hand control to a human (see §5).
- **No match at all** → a real failure. The system saves exactly what it expected, what it saw
  instead, a screenshot, and the current page, so anyone can see what went wrong without having to
  re-run anything.

If the page changes in some way nobody planned for, it's treated the same as any unrecognized
situation: a clear failure with evidence, never quietly ignored or retried into a fake success.

## 4. Heterogeneity & multi-tenant

**Different kinds of screens.** The Surface Adapter is the only part of the code that knows how to
click, type, and read a specific kind of screen (right now: a web page, via Playwright). Everything
else in the system only talks to this adapter, never to the browser directly. That means: to
support an old-style multi-frame website, I would only need to change this one adapter. To support
a desktop app instead of a website, I would write a new adapter for that, using the desktop's
accessibility tools instead of a browser — nothing else in the system would need to change at all.

**Reusing the same task across different banks/tenants.** The base URL is the only thing in an
artifact that's specific to one tenant, so a saved task can be replayed against a different bank's
site just by swapping that one field. The backup locators (§1) already handle small visual
differences for free. For bigger differences — a really differently designed version of the same
screen — the right answer, which I designed for but did not build, is a small override file per
tenant that can patch just the parts that differ, without touching the original saved task. I also
did not build a way to automatically notice when a saved task starts failing more often for one
tenant, though the design leaves room for it.

## 5. Escalation & handoff

Only one side is ever in control at a time: either the automation, or a human. When the system
needs a human, it takes a screenshot, writes down why, and opens a small web page (the "operator
page") showing the reason, the screenshot, and a live link straight into the *same* browser window
the automation was just using — not a copy of it, the actual same session. The automation then
waits until a person resolves it.

Four situations cause this hand-off:

1. **An irreversible action** (like the final "Confirm" click when opening an account). The system
   never clicks this by itself — a human always has to approve it first.
2. **A known risky outcome**, like a session expiring mid-task. Logging back in automatically isn't
   safe without a stored password, so a human decides instead.
3. **Repeated failures**, or running out of allowed steps, during Discovery.
4. **Going in circles** — the model keeps successfully repeating the same action without making
   progress (like re-typing a field that's already filled). I saw this happen in a real test run.
   It's different from #3 because nothing actually fails — each action works, it just never moves
   the task forward. The system watches for this by comparing the last few actions to each other.

**When a human resolves it, the system never re-does the risky step by itself.** For something like
a money transfer, blindly redoing it could cause it to happen twice. Instead, the system just looks
at the current screen and continues from there. If the human didn't actually finish the step, the
next check will correctly report a failure — it won't pretend everything worked.

**What I simplified on purpose:** the operator page here is a plain form, not a full screen-sharing
tool, and it doesn't record every single thing the human does by hand. A real production version
would want to record the human's actions too. I noted this but didn't build it. What *is* real: the
pause, the live hand-off, and the fact that the automation genuinely picks back up afterward.

## 6. Safety

Every single action, in both Discovery and Replay, is checked against an allow-list before it's
allowed to happen: which websites, which pages, which kinds of actions are OK. Anything outside
that list is blocked immediately and, during Discovery, triggers a hand-off to a human instead of
silently failing or retrying.

An action is marked **risky** if its name matches a risky pattern (like "delete" or "confirm") or
if the AI model itself flags it as risky. Either signal is enough — risky actions always need
human approval, no exceptions, in both Discovery and Replay. I chose to be extra cautious here on
purpose: the cost of asking a human unnecessarily is small; the cost of letting an unapproved
money-moving action through is not.

Sensitive information (passwords, account numbers) gets hidden in two separate places, not just
one:

1. While the task is running: the system checks every field name for sensitive words itself,
   rather than trusting the AI model to notice and flag it. I found this gap myself during testing
   — a password would otherwise have been saved in plain text if the model forgot to flag it — and
   fixed it by not relying on the model at all for this check.
2. Whenever anything is written to a log or evidence file: a separate cleanup step scrubs out
   anything that looks like a password, SSN, or card number, right before it's saved.

