# Discovery, conversations and operations

This update keeps the existing autonomous OneAPI workflow and fixed account bindings. It adds bounded comment discovery, direct conversation follow-ups and a dashboard organized around actual operations. It does not change live account configuration or enable publication during installation.

## Discovery and answers

New feeds and rotating problem searches remain the primary sources. Every 30 minutes by default, Reddit also samples visible comments in one recently discovered thread, rotating across saved candidates in the selected communities. Known fresh timestamps are required for this additional discovery. IDs are deduplicated before model calls. A layout failure in the sampled thread is recorded separately and does not discard the feed results; a CAPTCHA still stops the affected profile.

Priority favors help requests, app/website needs and direct incoming replies. Resolved requests, no-promotion requests and showcases are deprioritized. This is scheduling, not a claim that a discussion is a qualified lead; the OneAPI evaluation decides actual fit from the full source and verified catalog.

Initial replies directly recommend a relevant MeDo capability or example and give a concrete next step. Follow-ups answer the new question using the previous published answer and thread context. They can be shorter and do not repeat the promotional link. Product claims still require catalog evidence and a second OneAPI editing check. Affiliation and platform disclosures remain. Unsupported answers are held automatically, without adding a per-post human approval requirement in automatic mode.

## Conversation lifecycle

1. A browser-verified publication in the durable outbox becomes eligible for monitoring for up to seven days, bounded by content retention.
2. The monitor reads at most two due published messages per worker cycle. It checks the visible parent ID, original account and exact previous text before accepting direct incoming replies.
3. A new incoming comment enters the existing quality pipeline. Its author, parent, text and thread are rechecked before sending.
4. The browser targets that comment's own composer. One click is recorded durably, then author, text, parent and permalink must be visible after navigation. An uncertain result is not retried automatically.
5. Acknowledgments get no additional response. Opt-outs close the conversation. Default limit: four follow-up publications per thread. All existing global and community publication limits still apply. Only a verified direct reply can bypass the unsolicited-author cooldown.

Reddit uses explicit comment IDs and parent IDs. DEV uses each comment's own permalink, excluding nested child links. Configured Discourse forums must expose the selected reply target in their composer. A missing/ambiguous target stops publication. Indie Hackers remains inactive without its configured exception; its live adapter is not newly certified by this release. Fixtures do not establish that any authenticated platform currently accepts publication.

This is bounded visible-comment monitoring, not an inbox API or complete coverage. Folded, paginated or unavailable comments can be missed. Changed/deleted parent messages are held for inspection. No automatic CAPTCHA solving, account switching, unsolicited reminder messages or repeated pitches are introduced.

## Settings

Existing configs receive these defaults through `load_config`; no private file rewrite is needed:

| Setting | Default | Meaning |
| --- | --- | --- |
| `discover_comments` | `true` | Sample one selected Reddit thread in addition to feeds/search |
| `comment_scan_seconds` | `1800` | Minimum interval between comment samples per assigned profile |
| `conversation_monitoring` | `true` | Monitor verified publications when publication is enabled |
| `conversation_poll_seconds` | `900` | Minimum interval between checks of each published message |
| `max_conversation_replies` | `4` | Shared maximum follow-up publications per thread |

Pauses, shared budgets and community limits still apply. Disabling conversation monitoring stops new monitoring; pause the worker to stop already queued publications as well. Deploying this update preserves existing paused state and publication settings.

## Dashboard

- **Overview:** last-24-hour discoveries, passed quality checks, verified publications and incoming replies; blockers, coverage and publication pacing.
- **Opportunities:** searchable first-contact queue; source, evidence, matched catalog entry, exact draft, account and blocker in a detail drawer.
- **Conversations:** incoming replies plus recent monitoring checks and failures.
- **MeDo posts:** content-plan results and a local form for adding a brief.
- **Platforms:** fixed accounts, readiness, errors and local account/CAPTCHA controls.
- **Activity & costs:** individual scan outcomes, publication receipts, model calls, tokens, failed calls and unknown usage.

`GET /api/overview` aggregates the whole stored database rather than the latest-200 item list. Overview event counts use a rolling 24-hour window; budget counters use the existing UTC day. The counts are not a conversion funnel because events can concern different cohorts. Manual publication recording is not counted as a browser-verified publication. No registration, activation, revenue, qualified-lead or currency-cost figure is invented. Those integrations and Infoflow reporting are deferred.

The company dashboard remains read-only. Local changes still require loopback, the matching Origin and a CSRF token. Untrusted source/draft text is rendered as text, and external links accept only HTTP(S). A failed refresh leaves a visible stale-data warning.

## Validation and deployment

Run `PILOT_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v`. Intercepted Chrome fixtures exercise direct comment targeting, stale-source blocking, uncertain-send recovery, nested DEV parent extraction, dashboard interactions/mobile layout, opt-outs, receipt forgery and metrics beyond 200 rows. They never send to social platforms.

Follow [DEPLOYMENT.md](DEPLOYMENT.md) for a pinned GitHub release, candidate tests on Windows, backup, source-only replacement, health checks and rollback. Preserve `.env`, `config.json`, `channels.json`, `runtime/`, browser profiles and the outbox. No iCode dependency is required.
