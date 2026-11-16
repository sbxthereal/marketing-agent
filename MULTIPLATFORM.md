# Autonomous Windows channels

The runtime stays on the Windows server. Comate OneAPI evaluates relevance,
drafts and performs the final editing pass. Browser actions use deterministic
Playwright adapters; Codex is not a runtime dependency. There is no per-post
human approval. Accounts, CAPTCHA and exceptional failures are operator handoffs.

## Initial allocation

| Destination | Profiles | Purpose | Current preparation |
| --- | ---: | --- | --- |
| Reddit | 1 | Relevant questions, useful replies, permitted tutorials/template posts | Existing adapter; account and community readiness remain required |
| DEV | 1 | Technical questions and original practical tutorials | New browser adapter and three finite tutorial/template briefs |
| Indie Hackers | 1 reserved | Founder discussions and useful build notes | Adapter scaffold; inactive pending a site-specific automation exception and live UI calibration |
| Additional Discourse forum | 0 until selected | Relevant discussions in a configured forum | Reusable adapter; needs an exact forum origin, feeds and its rules |

The other pre-existing AliasMode profiles are not started, deleted or reassigned.
New DEV/IH sessions use ordinary persistent Chrome profiles on Windows, at
`runtime/channel-profiles/<channel-id>`. Reddit retains its existing browser backend.
Browser profiles are sessions, not newly created platform accounts. Existing
managed Reddit configurations retain their original bindings; the in-dashboard
Reddit login helper is for the dedicated Chrome backend.

Start with one genuine team/maker account per platform. Add a different forum only
after useful conversations and attributable registrations justify the effort.
Do not create extra accounts to multiply posting limits or work around a challenge.

## Configuration and account connection

Copy `channels.example.json` to `channels.json` on the server. This private file is
loaded alongside `config.json`; it is excluded from Git. The example DEV policy was
reviewed on 2026-11-16. Policy records expire after 30 days and must accurately reflect
the destination's current rules. Enabled channels remain idle until their account is
connected and their readiness checks pass.

In the dashboard **inside the server**, open `http://127.0.0.1:8765`:

1. Choose **Open account login** on the platform card.
2. Sign in yourself in that exact Windows browser. Return to the platform after SSO.
3. Choose **Connect signed-in account**. The worker reads the profile menu and binds
   that username; it never asks for or stores a plaintext password.
4. Use **Resume all channels** when setup is complete. The company dashboard remains
   read-only at `http://10.224.102.19:8765`.

After a CAPTCHA, solve it in the same Windows profile and choose **Check and resume**.
The worker verifies the account again. A different account cannot replace the bound
one. Successful preflight recovery can resume an unchanged, fresh queued draft;
an uncertain submit is never resent automatically.

Changing a channel's profile/account requires deliberate configuration work. No
profile rotation, CAPTCHA solver or automated account registration is included.

## Autonomous workflow

Each enabled channel scans its configured feeds on its own interval. DEV starts at
30 minutes, with bounded pages; it does not claim to cover every conversation. Source
URLs and queue IDs are namespaced, so a Reddit worker cannot consume a DEV item.
The browser reads the full source and discussion; unknown age, solved problems,
existing MeDo mentions, own threads and unsupported requirements do not produce a
promotional reply. Failure to read a feed is an error, not a claim of zero demand.

OneAPI performs two metered passes. A successful final check binds the source,
draft and policy in a receipt. Natural language varies by platform: concise direct
replies, longer DEV tutorials, and practical founder build notes. Product claims
come from the verified catalog. Team affiliation is visible; new platforms also
include an automation disclosure. DEV articles require selecting **Fully Autonomous**
in the editor; a missing control blocks submission.

All channels share the existing SQLite outbox and budget. The starting ceiling is
three publications per rolling 24 hours, two hours apart globally, two per destination
per day, six hours apart in a destination, and seven days between replies to the same
author. These are caps, not targets. Similar copy is rejected across platforms.
Finite content plans run at most once per stable ID, one planned post per week
globally by default. The system does not endlessly recycle the three starter briefs.

Publication checks the account, source freshness and composer contents before
recording a submitting marker and clicking once. It then verifies the author, text
and permalink, including a reload. Ambiguity pauses the affected channel. Other
eligible channels can continue within the same shared limits. A global pause stops
all discovery/publication while still allowing account and CAPTCHA controls.

## Platform rules and testing limits

[DEV's disclosure announcement](https://dev.to/devteam/introducing-ai-disclosure-on-dev-tools-for-nuance-clarity-and-better-feeds-34mk)
allows some transparent autonomous content while retaining quality requirements.
That is not permission for generic promotional replies.
[Indie Hackers' terms](https://www.indiehackers.com/terms) restrict auto-responders and
scraping. Its channel stays inactive unless a specific arrangement permits the
intended automation; blanket internal marketing authorization does not establish
that exception. Additional forums are bound to an explicit HTTPS origin and rules.

The new DEV browser flow is covered by intercepted Chrome fixtures: discovery,
source enrichment, identity, article disclosure, one-time submission, permalink
readback, CAPTCHA, changed sources, existing drafts and uncertain outcomes. These
are not live publication tests. Signed-in site UI still needs validation with the
actual accounts; selectors fail closed if the site changes. IH and Discourse
selectors are prepared adapters, not claimed live-verified integrations.

## Results and operation

The dashboard separates platform readiness, discovered items, held drafts, verified
publications and errors. Registrations, activation and payments are not yet connected;
the UI labels conversions as unavailable. A fetched post is not a qualified lead,
and a published reply is not a registration. Connect product analytics before
deciding to scale the profile count.

Use the existing Windows scheduled task and Python environment. Keep the interactive
Windows user session signed in; disconnecting RDP is different from signing out.
No automatic Windows logon or weakened authentication is configured. A reboot,
expired login, changed site rules/UI or an uncertain send may require attention.
The Mac is only needed for remote administration, not runtime execution.
