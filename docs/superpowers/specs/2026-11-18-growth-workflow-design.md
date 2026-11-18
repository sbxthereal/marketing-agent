# Discovery, conversations and operator dashboard

The user approved improving discovery, answering and continuing conversations, with a clear detailed dashboard. Infoflow and conversion attribution are deferred. GitHub remains the delivery repository; do not activate the iCode updater.

## Design

Extend the existing Python/SQLite/Playwright application without new dependencies. OneAPI remains the only live AI provider. Keep bounded browser reads, fixed account routing, durable publication receipts, global/community limits and manual CAPTCHA handling.

Discovery ranks concrete unresolved needs, constraints and freshness, explains its signals, and adds bounded comment discovery from already selected Reddit threads. Missing keywords never permanently discard a request. Track partial coverage explicitly. Model qualification still precedes writing and the second OneAPI pass checks the final answer.

Represent an incoming comment as a normal comment item with a verified target snapshot. A separate conversation monitor watches only verified published messages, follows direct responses with stable parent IDs, and creates a deduplicated incoming item. It never initiates repeated follow-up nudges. Store the parent publication receipt, account, target text and parent identity. Verify those again before sending. Support scoped comment composers on Reddit, DEV and configured Discourse; missing/ambiguous parent metadata or controls stops the operation. Continue to keep Indie Hackers inactive unless its existing readiness requirements are satisfied.

Replies use the person's actual constraints and verified product claims. Initial recommendations remain direct; follow-ups answer the new question without repeating links or a sales pitch. No invented personal experience or unverified product capabilities. Opt-outs, deleted content, own-account messages and non-actionable acknowledgments stop a conversation. Existing shared publication limits still apply; a verified direct response can bypass only the initial-outreach author cooldown.

The dashboard has Overview, Opportunities, Conversations, MeDo posts, Platforms and Activity views. Show real database totals separately from the latest 200 item list, explicit 24-hour versus lifetime windows, source coverage, decision evidence, drafts/publication receipts, attention alerts, model usage and scheduler state. Keep read-only company access and authenticated local controls. Growth conversion metrics are marked not connected, never fabricated zeroes. Render external text as text, validate outbound link schemes and preserve filter/selection state during refresh.

## Acceptance

- New and changed discovery/answer logic has meaningful tests with positive, unsupported, resolved and opt-out cases.
- Conversation tests cover direct parent verification, wrong accounts, stale/edited messages, restart deduplication, nested composer targeting, one-click publication/readback and shared limits.
- Aggregate API tests use more than 200 records and check window boundaries and unknown model usage.
- Browser dashboard tests exercise filtering, details, alerts, read-only mode and mobile layout without live social publication.
- Run the full Chrome fixture suite, then stage and validate on Windows while preserving credentials, sessions, publication history and paused state.
- Live publication cannot be claimed proven while accounts remain unconnected. Document the remaining live verification step honestly.
