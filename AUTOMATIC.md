# Automatic replies and MeDo posts

Set `publishing.mode` to `automatic` to remove per-post human approval. `manual` remains the default for existing installations. This does not remove the separate `publishing.enabled` switch, account verification, destination rules, or the worker pause.

## Workflow

1. Collect new discussions using the existing feed/search discovery and deduplication.
2. Read the original question and thread context. Skip solved requests, previous MeDo mentions, unsupported requirements and poor matches.
3. Comate OneAPI qualifies the request and drafts a specific answer. A second metered OneAPI call checks and edits it against the verified catalog.
4. Local validation records a receipt binding the exact source, final result and current community policy. Only a matching, fresh receipt may enter the automatic outbox.
5. The browser checks the expected account and destination, applies shared limits, sends once and verifies the resulting permalink and author.

MeDo posts use the same final check and outbox. A configured content plan creates one tutorial, template explanation or build note at a time. Each stable plan ID runs once, including across restarts; changing a brief under the same ID does not republish it. Add a new ID only for a genuinely new topic. The default interval is one planned post per seven days across all profiles. This is a finite editorial plan, not an endless product-promotion loop.

There is no per-item approval button in automatic mode. `review_recommendations: true` means a **OneAPI editing pass**, not a human reviewer. Both quality flags must remain enabled. Legacy drafts without receipts are not swept into automatic publication. Failed checks and exhausted editor budgets hold drafts without repeated rewrite spending.

## Configuration

Merge these settings into an existing live configuration; do not replace credentials or profile assignments:

```json
{
  "publishing": {
    "mode": "automatic",
    "enabled": true
  },
  "automation": {
    "campaign_interval_hours": 168,
    "campaign_plan": [
      {
        "id": "portal-status-tutorial-01",
        "subreddit": "YOUR_CONFIGURED_COMMUNITY",
        "format": "tutorial",
        "template_id": "YOUR_VERIFIED_CATALOG_ID",
        "brief": "Explain a proposed client portal with project statuses and an update screen. Give a specific starting prompt and checks to try. Do not claim a completed app."
      }
    ]
  }
}
```

Plan formats: `tutorial`, `template`, `build_note`. Optional `not_before` is a Unix timestamp. At most 30 entries; global plan interval 24–720 hours. Publication cooldowns can delay a drafted post further. Unknown catalog entries never schedule.

Existing platform/AI-processing permission and expected Reddit usernames are still required. Each community policy needs `automated_replies: true` and/or `automated_posts: true` for its permitted uses, plus its actual rule/permission `reference`, `notes`, and `reviewed_at` within 30 days. Standalone posts additionally require `standalone_posts: true`. Record `links_allowed` accurately. A reply omits its generated catalog link where links are not permitted; affiliation stays visible. Do not enable flags contrary to known community rules.

The default shared allowance remains 3 publications per rolling 24 hours, 2 hours apart globally, at most 2 per community with 6 hours between them, and 7 days between pitches to the same author. These are maxima, never quotas. Replies and posts consume the same allowance. Existing fixed profile assignments do not rotate accounts around failures.

## Dashboard and recovery

`/api/status` exposes `automation.mode`, readiness blockers, destination eligibility, pending publications and started plan IDs. `/api/items` reports why a draft is held. The company dashboard stays read-only; worker controls stay on the server.

CAPTCHA, login requirements and account mismatches pause browser work and produce the existing dashboard alert. Solve a challenge in the same server profile, then use **Check and resume**. If submission may have happened, reconciliation is required; the agent never retries the click blindly. Receipt expiry, policy changes or edited source text also block sending.

## Validation and limits

The test suite covers the complete automatic worker cycle using a fake OneAPI provider and intercepted browser fixtures, plus quality failures, budget exhaustion, altered drafts, missing identity, policy changes, restart uncertainty and campaign scheduling. These tests do not publish to Reddit and do not establish that an authenticated live composer works.

The dashboard currently measures discovery, model use and verified publication. Fetched posts are not leads; verified posts are not registrations. Registration, activation and paid conversion attribution still requires a connected product analytics source. Prioritize that measurement before expanding profiles or platforms.
