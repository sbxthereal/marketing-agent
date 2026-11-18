# Growth Workflow Implementation Plan

**Goal:** Improve need discovery and natural answers, continue verified conversations, and make operations understandable in the dashboard.

**Architecture:** Reuse existing Store, Workflow and browser adapters. Add conversation scheduling/validation and aggregate insights as focused modules. Preserve all durable send and account boundaries.

**Tech stack:** Python, SQLite, existing Playwright/Chrome and OneAPI; vanilla HTML/CSS/JavaScript.

**Spec:** ../specs/2026-11-18-growth-workflow-design.md

## Global constraints

- User's approved scope is implemented directly in this session.
- Live AI runs through OneAPI. No Infoflow integration or conversion-event implementation.
- Fixed accounts; manual CAPTCHA handoff; no automatic resend after uncertainty.
- Shared publication limits and persistent history survive restarts/deployment.
- GitHub delivery; iCode updater stays inactive.

## Tasks

- [x] Discovery and response quality: extend `pilot/discovery.py`, `gateway.py`, `quality.py`, `core.py`; add resolved/constraint/short-reply tests. Preserve original evidence/claim validation.
- [x] Conversation lifecycle: add `pilot/conversations.py` with receipt validation, bounded monitoring and incoming comment ingestion; add comment identity/parent snapshots and scoped publishing adapters. Integrate in existing worker cycles and fixed ProfilePool routing.
- [x] Dashboard data: add `pilot/insights.py` and `/api/overview` for full SQL totals, recent scans, conversation watches and model usage; test more than 200 records and read-only behavior.
- [x] Dashboard interface: replace the dense card wall with navigation, overview, searchable queues, detail panel, platform setup and activity tables; retain existing local controls with CSRF checks. Add real Chrome interaction/layout tests.
- [x] Validate and deliver: run targeted and full browser tests, update documentation, push GitHub changes, stage Windows candidate and verify native tests before replacing source. Preserve and verify original settings/runtime.

## Verification evidence

- macOS: 215 tests with Chrome fixtures, all passed (114.684 seconds).
- Windows Server 2022: 215 tests with Chrome fixtures, all passed (76.202 seconds).
- Real OneAPI: three synthetic cases passed; five calls, 7,110 tokens, no social publication.
- Installed source and dashboard hashes checked; existing configuration, accounts, ten stored items and send ledger preserved. Worker remains paused with publication disabled.
- GitHub is the delivery source. Infoflow, conversions and unattended GitHub deployment are separate follow-up work.
