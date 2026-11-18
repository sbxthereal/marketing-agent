# Operator runbook

Routine content passes through OneAPI qualification/editing and local checks, then publishes automatically when the account and destination are ready. No per-post human approval is required. The operator handles account sign-in, CAPTCHA, exceptional failures and periodic result checks.

## Daily inspection

Open the company read-only dashboard or the writable dashboard on the server at `http://127.0.0.1:8765/`.

- Check each platform's account, readiness, alert and last-scan time. A running scheduled task does not mean the browser is healthy or the channel is active.
- Inspect errors/held drafts, verified publication links, model calls and token usage. Empty discovery is not proof there is no demand; confirm scan coverage.
- Look at relevance and reader response. Fetch counts are not qualified leads, and publication counts are not registrations.
- Check actual signup/activation/payment attribution before increasing volume. These analytics are not connected in the current release.

The browser dashboard refreshes every 15 seconds. CAPTCHA alerts are persistent in-page alerts and tab-title indicators; there is no configured email, SMS or operating-system push alert.

## Commands on Windows

```powershell
Set-Location C:\MeDo\marketing-agent
Get-ScheduledTask -TaskName MeDo-Reddit-Agent
Get-ScheduledTaskInfo -TaskName MeDo-Reddit-Agent
Get-Content -LiteralPath .\runtime\worker.log -Tail 80
Invoke-RestMethod http://127.0.0.1:8765/api/status
Invoke-RestMethod http://127.0.0.1:8765/api/channels
```

`runtime/worker.log` rotates at 5 MB with three backups. Do not publish logs or probe reports without removing private data. The read-only HTTP endpoints expose operational/public-discussion data, never gateway credentials. POST controls require the server-local dashboard and its origin/CSRF checks.

## Stop or resume

Use **Pause all channels** for a normal pause and **Pause channel** to isolate a platform. Wait for any in-flight operation to settle. Use Task Scheduler to stop the task for maintenance or an emergency; interrupting a send may create an uncertain result. On restart, the persisted pause remains.

```powershell
Stop-ScheduledTask -TaskName MeDo-Reddit-Agent
# After maintenance and validation:
Start-ScheduledTask -TaskName MeDo-Reddit-Agent
```

Do not close the Python console's window as a substitute for a controlled pause. Do not start a second manual worker while the task is active. Keep the Windows session logged in; RDP disconnect and Windows sign-out have different consequences.

## CAPTCHA, expired login or changed account

1. Read the alert to identify the exact channel/profile and source page.
2. Through RDP, solve the challenge or sign in manually in that same profile. Return to the relevant site page. Do not switch accounts or rotate profiles to work around the challenge.
3. On the server dashboard, select **Check and resume** (the legacy Reddit-only view may say **I solved it — check and resume**).
4. The agent verifies access and the expected identity. An unresolved challenge, different username or uncertain send keeps the channel stopped. Inspect the new status rather than assuming a successful click resumed work.

No automatic CAPTCHA solver is included. An account identifier is bound in the local database after verification; credentials are entered through the browser and are not stored as plaintext by the agent.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| All channels paused | Check the global pause; resume only after setup/incident recovery is complete. |
| Account not connected | Use Open account login, sign in, then Connect signed-in account on the local dashboard. |
| Rules expired or missing | Re-read that destination's actual rules; update its policy reference, notes and review date. Records expire after 30 days. |
| OneAPI 401/403 | Verify the server-local key, endpoint and model permissions with the gateway owner. Do not print the key. |
| OneAPI timeout | Check the authorized company route/proxy and TLS trust from the Windows server. A connected Mac VPN does not establish server connectivity. |
| Site blocked/429 | Pause and respect the access restriction. Do not rotate accounts, proxies or fingerprints to continue. |
| Browser selector/composer changed | Keep the affected channel paused; update its adapter and run fixture plus signed-in validation. |
| Daily budget exhausted | Model-call/token budgets reset at 00:00 UTC (08:00 China time); publication caps use a rolling 24-hour window. Inspect the specific limit and never reset the database to clear it. |
| Repeated/no new posts | Inspect source coverage, feed URLs, timestamps, duplicate counts and errors before adjusting intervals. |
| Draft held | Read the specific relevance, grounding, style or budget reason. Fix catalog evidence/briefs if needed; do not force publication. |
| No new promotional posts | The editorial plan is finite; each stable ID runs once. Add a distinct, evidence-backed brief when justified. |
| Dashboard unavailable | Check task/session, `runtime/worker.log`, port 8765 and the configured private interface. |
| Multiple workers/profile locked | Stop the duplicate process through its own task/session. Never delete the lock/database while a worker may be running. |

## An uncertain publication

An uncertain result means the submission might have reached the site. Leave the attempt unresolved until the actual source/permalink is inspected. Do not click publish again, delete the row, clear history or mark it successful based only on a screenshot of an editor.

The built-in CLI reconciliation path currently uses the **Reddit** adapter. With the worker stopped, verify an existing Reddit permalink:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pilot reconcile --item t3_POST_ID --url 'https://www.reddit.com/r/COMMUNITY/comments/POST_ID/slug/COMMENT_ID/'
```

Campaign reconciliation uses its `campaign_...` item ID and the real post permalink. This checks a prior publication; it does not send another one. For DEV/IH/Discourse uncertain sends, keep the channel paused and have a maintainer reconcile with that adapter. Do not use the Reddit CLI against another platform.

## Data and backups

| Local file/directory | Why it must be preserved |
| --- | --- |
| `.env` | OneAPI credentials; restricted to the operator/admins |
| `config.json`, `channels.json` | Destination policy, shared limits and channel configuration |
| Configured private catalog | Approved claim evidence, if replacing the tracked example |
| `runtime/pilot.sqlite3` and SQLite sidecars | Deduplication, budgets, account bindings, outbox, quality receipts and publication history |
| `runtime/chrome-profile`, `runtime/channel-profiles/` | Dedicated browser sessions; sensitive cookie data |
| `runtime/worker.log*` | Recent operational evidence |

Take a coherent backup with the worker stopped and dedicated browsers closed, or use a maintainer-approved SQLite backup process. Do not copy just the main database while a live WAL transaction may exist. Store backups on an approved protected volume and restrict access. A browser profile is host/user-specific; re-login when moving machines instead of treating copied cookies as portable credentials.

Routine updates and rollbacks preserve the newest outbox/history. Retention and diagnostic metrics are not a backup strategy. Never put runtime data, secrets or browser profiles into Git.

## Content and growth controls

Default shared ceilings: three publications per rolling day, two hours apart globally, two per destination per day, six hours between publications in a destination and seven days between pitches to one author. These are caps, not quotas or platform permission. One scheduled campaign per week is the default; replies and campaigns share the same allowance.

Keep MeDo affiliation visible. DEV articles require the Fully Autonomous label. Recommend a template or MeDo only where the source need and catalog evidence support it; never invent customer experience or successful demos. The channel list and allowed formats come from current destination rules, not account count.

Review model cost, held-draft reasons and attributable conversion together before expanding channels. Additional platforms require their own adapter validation and policy; a new browser profile alone is not a platform integration.
