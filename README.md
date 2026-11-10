# MeDo Reddit pilot

A local Windows/macOS worker for **new-post discovery → thread reading → MeDo matching and drafting → human review → browser publication → verified comment link**.

The deployed worker does not use Codex, ChatGPT, this chat, or desktop computer-use tools. Playwright runs on the server; all runtime AI calls (Browser Use fallback, qualification/drafting, and final reply editing) go through the configured Comate OneAPI gateway. OneAPI is the provider; `model`, `browser_model`, and `review_model` select the actual models. The defaults use DeepSeek-V4.1-Flash.

**Windows Server 2022:** start with [WINDOWS.md](WINDOWS.md). **macOS:** use [START-HERE.md](START-HERE.md). Validation and remaining deployment requirements are in [VALIDATION.md](VALIDATION.md).

## Efficiency

- One dedicated Chrome profile; no Reddit API app or external browser service.
- Routine extraction uses rendered DOM and zero model calls. Browser Use is a read-only fallback for unfamiliar layouts, capped at four steps and charged to the same daily token budget.
- SQLite deduplicates post IDs before thread enrichment or AI calls. Repeated scans do not redraft the same post, including after a restart.
- One model call performs qualification and drafting; only a proposed recommendation gets a second, metered OneAPI editing/claim-check call. Local checks catch boilerplate and duplicate wording. A failed check holds the recommendation, never silently releases it. Catalog shortlisting and bounded thread context reduce input size.
- Discovery combines community new feeds with three rotating problem searches, sorted newest first and scoped to approved communities. One search runs every 30 minutes, with a 72-hour age cutoff and two-page cap. Each source records its own coverage and duplicates. Keywords rank requests; they do not discard unusual requests. Waiting items gain priority over time.
- Defaults: one candidate community, five-minute feed scans backing off to 30 minutes when idle, 24-hour feed lookback, three feed scroll batches, five analyses per cycle, 100 model calls / 150,000 tokens per UTC day. Page limits are reported as incomplete coverage, never as “all Reddit checked.”
- After a partial scan, later scans continue reporting the gap until they reach a valid boundary. Transient browser failures retry with backoff. Login, access challenges and account mismatches require attention.

## Publication behavior

Approve a draft, then choose **Publish approved reply**. Approval alone never sends. The worker verifies the account, source thread, unchanged original post, approved text, and visible top-level editor. It records a submission marker before clicking. A comment counts as verified only after its exact words and expected author are read back from a matching comment permalink; browser whitespace differences are normalized.

An interrupted or ambiguous submission is held as `uncertain` and never automatically retried. A failed preflight can be retried explicitly from the review screen. To reconcile an uncertain attempt, stop the worker and run:

```sh
.venv/bin/python -m pilot reconcile --item t3_POST_ID --url 'https://www.reddit.com/r/COMMUNITY/comments/POST_ID/comment/COMMENT_ID/'
```

On Windows, use `.\.venv\Scripts\python.exe` instead of `.venv/bin/python`. This checks an existing comment; it does not post another one. If a comment cannot be verified, it remains unresolved. A rejected, removed or moderated comment is not evidence of successful acquisition.

Publication defaults to disabled. The configured daily/interval limits are operational guardrails, not claims about what Reddit permits. The written permission/reference and community rules must cover the intended operation. No CAPTCHA, rate-limit or access-control bypass is implemented.

Reply pacing is persistent: at most **3 per rolling 24 hours globally**, **2 hours apart**, **2 per community per rolling 24 hours**, **6 hours apart within a community**, and no repeat pitch to the same author for **7 days**. These are maxima, not quotas. One approved community therefore permits at most two replies/day under the defaults. Similar recent replies are blocked. Existing visible MeDo mentions or prior participation by the account stop another pitch. Cooldowns are checked again immediately before submitting. An uncertain outcome pauses the worker, including after a restart.

Replies aim for 40–65 words, recommend MeDo in the opening sentence, reference the actual request, and give one concrete starting step. Affiliation is appended explicitly. Unsupported features, no-promotion requests, solved issues and poor matches get no promotional draft. See [UPGRADE.md](UPGRADE.md) for the latest evidence and next improvements.

## Scope

This release handles top-level replies to new posts in one or two selected communities. It does not claim to find all Reddit conversations, monitor every new comment, publish standalone promotional threads, or measure registrations without a connected attribution system. In r/nocode, product launches belong in the designated monthly launch thread; any future promotional-post support must honor that.

The catalog contains documented MeDo capabilities and two official showcase starting points. `verified` means the cited source was checked; the showcase apps have not been functionally certified. Drafts must preserve those limitations.

## Verification

```sh
PILOT_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

The browser tests intercept every request and use local test pages, so they never publish to Reddit. The optional `tools/agent_probe.py` exercises Browser Use with real OneAPI calls against a local browser fixture; it requires an authorized key and incurs model usage.

The original synthetic demo remains available by copying `config.example.json` to `config.json`. It is clearly labelled and never silently substitutes for failed live discovery.

Source modules: `browser.py` / `dom.py` handle page operations, `agent_browser.py` handles bounded recovery, `workflow.py` maintains the outbox and metrics, and `core.py` / `gateway.py` / `service.py` provide queueing, model calls and the review server. The interface binds only to 127.0.0.1 and checks origin and CSRF tokens on mutations.
