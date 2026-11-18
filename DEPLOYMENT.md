# Windows deployment runbook

This runbook deploys the MeDo marketing **pilot** on Windows Server 2022. It covers a new installation, an update to the existing installation, verification and rollback. It does not require Codex to run after installation.

## Source and release ownership

- Active source: `https://github.com/sbxthereal/marketing-agent`, branch `main`.
- The personal iCode copy is retained for handover. Its server route is unavailable; no iCode updater is installed.
- GitHub CI validates pushes. Windows installation remains a pinned, validated release transfer; pushing code alone does not deploy it.
- Deploy an exact commit. Keep the previous release, dependency environment and local state until the replacement has been verified.

## Requirements

| Component | Requirement |
| --- | --- |
| Host | Windows Server 2022 x64, Desktop Experience, an interactive user session |
| Browser | Google Chrome; one dedicated profile/account per configured channel |
| Runtime | Python 3.14, provisioned by `uv`; pinned dependencies in `requirements-windows.lock` |
| Model | Authorized Comate OneAPI endpoint/key and permitted text/browser models |
| Network | Access to OneAPI, selected communities and package sources through the company's approved network path |
| Disk | Room for the active installation, a complete previous release and browser profiles |
| Operator | Same Windows user for setup, browser sign-in and Task Scheduler |

The current scheduler starts **at user logon**, not before login after a reboot. Disconnect RDP rather than signing out. Session timeout policies can stop the worker. There is no automatic Windows logon configured.

## 1. Prepare a release on your development computer

Use your existing authorized GitHub login. Never put credentials in the clone URL.

```sh
git clone https://github.com/sbxthereal/marketing-agent.git medo-marketing-agent
cd medo-marketing-agent
git switch main
git status --short
```

If `git status` is not empty, resolve or preserve those changes before packaging. Inspect the intended commit and export only tracked code:

```sh
PILOT_REVISION=$(git rev-parse HEAD)
git archive --format=zip --output="../marketing-agent-${PILOT_REVISION}.zip" "$PILOT_REVISION"
printf '%s\n' "$PILOT_REVISION" > "../marketing-agent-${PILOT_REVISION}.commit"
```

Calculate the ZIP SHA-256 (`shasum -a 256` on macOS or `Get-FileHash -Algorithm SHA256` on Windows). Transfer the ZIP, commit identifier and checksum to the server using the approved file-transfer mechanism. Keep `.env`, local configuration, browser profiles and SQLite data out of release archives.

A ZIP deployment deliberately has no `.git` directory and cannot be updated with `git pull`. Use the update procedure below. A server with its own authorized Git access may clone instead; never embed a token in its clone URL.

## 2. Install on a new server

These commands run in PowerShell **on the Windows server**. Replace the example ZIP filename with your pinned release. Verify its checksum against the development computer before extraction.

```powershell
$ErrorActionPreference = 'Stop'
$PilotRoot = 'C:\MeDo\marketing-agent'
$PilotZip = 'C:\MeDo\incoming\marketing-agent-EXACT_COMMIT.zip'
Get-FileHash -Algorithm SHA256 -LiteralPath $PilotZip
if (Test-Path -LiteralPath $PilotRoot) { throw 'Existing installation: use the update procedure.' }
Expand-Archive -LiteralPath $PilotZip -DestinationPath $PilotRoot
Set-Location -LiteralPath $PilotRoot
```

Install `uv` from its [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/) if absent. The setup script finds `uv` on PATH or under `%USERPROFILE%\.local\bin`. Then:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\setup.ps1 -InstallChrome -RunTests
if ($LASTEXITCODE -ne 0) { throw 'Installation or tests failed; do not start the worker.' }
```

Setup installs the pinned Python environment, creates missing `.env` and `config.json`, applies NTFS restrictions to them and `runtime/`, and optionally installs Chrome. It preserves existing configuration. `-ExecutionPolicy Bypass` applies to this command's process; setup does not change machine policy. Respect any organization-enforced script policy.

## 3. Configure autonomous operation

Enter credentials directly on the server:

```powershell
notepad .env
notepad config.json
```

`.env` contains `ONEAPI_BASE_URL` and `ONEAPI_API_KEY`. The hostname in `.env.example` is a placeholder. Choose gateway-supported `model`, `review_model` and, if using browser fallback, `browser_model`. Never paste keys, proxy passwords or cookies into tickets, Git commits or screenshots.

Enable the autonomous **mode**, keeping live publication disabled during setup:

```powershell
$PilotConfig = Get-Content -Raw -LiteralPath .\config.json | ConvertFrom-Json
$PilotConfig.publishing | Add-Member -NotePropertyName mode -NotePropertyValue automatic -Force
$PilotConfig.publishing.enabled = $false
$PilotConfig | ConvertTo-Json -Depth 30 | Set-Content -Encoding UTF8 -LiteralPath .\config.json
if (-not (Test-Path -LiteralPath .\channels.json)) {
    Copy-Item -LiteralPath .\channels.example.json -Destination .\channels.json
}
notepad channels.json
```

Keep `review_recommendations` and `reply_quality_checks` enabled: these mean automated model/local checks, **not human approval**. `channels.json` stores the non-Reddit channels; the root config owns Reddit and shared budgets. Restrict this private configuration file to the same operator/admins as `config.json`.

Before activating a destination:

- Record the actual collection/AI permission, community rules, references and review date. The examples are candidates, not blanket site authorization. Do not simply refresh an expired timestamp without rechecking rules.
- For Reddit, set `permissions` and `publishing.permission_confirmed` / `permission_reference`, the selected `subreddits` and their `community_policies`. See [AUTOMATIC.md](AUTOMATIC.md) for the exact policy keys. Only allow standalone promotional posts where that destination permits them.
- DEV has one profile and three finite editorial briefs. Confirm the current policy and preserve the **Fully Autonomous** disclosure setting.
- Indie Hackers stays inactive unless a specific arrangement permits its automated collection and responses. Its adapter needs signed-in UI validation.
- Leave the extra Discourse slot disabled until a real forum origin, feeds, rules and account are selected.

Start with one genuine account per destination. All channels share one publication allowance; adding accounts does not multiply it. See [MULTIPLATFORM.md](MULTIPLATFORM.md) for the channel configuration.

## 4. Validate and connect accounts

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pilot doctor
if ($LASTEXITCODE -ne 0) { throw 'Fix local configuration first.' }
.\.venv\Scripts\python.exe -X utf8 -m pilot serve
```

`doctor` validates local configuration and installed components. It does **not** authenticate OneAPI, validate the current website UI or prove live publishing works. The server dashboard is **http://127.0.0.1:8765/**.

Use **Pause all channels** while connecting accounts. For each eligible channel, select **Open account login**, sign in yourself in that exact server browser, then **Connect signed-in account**. Account verification binds the identity. Managed Reddit profiles must be signed in through their existing manager; do not reassign old attempts to a new account. Close the manual `pilot login` browser before starting a worker if you used that legacy command.

Confirm `/api/channels` reports the expected accounts and explicit readiness blockers. Perform a bounded discovery/draft check, inspect representative drafts, and validate the actual signed-in composer before leaving publication unattended. Initial commissioning is a deployment check, not a permanent per-post review queue. A fixture test or a generated draft is not evidence of a successful real post.

For an optional metered OneAPI/browser-fixture probe, stop the worker first:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\agent_probe.py --env-file .env --output runtime\oneapi-probe.json
```

This probe incurs model use but does not contact Reddit. Keep the result private. Use **Resume all channels** only when the configured destinations and accounts are ready.

## 5. Register unattended execution

Stop the foreground worker with Ctrl+C before installing the task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\install-task.ps1 -StartNow
Get-ScheduledTask -TaskName MeDo-Reddit-Agent
Get-ScheduledTaskInfo -TaskName MeDo-Reddit-Agent
Invoke-RestMethod http://127.0.0.1:8765/api/status
Invoke-RestMethod http://127.0.0.1:8765/api/channels
```

The task name retains the original Reddit name but runs all configured channels. It runs as the current interactive user, ignores overlapping task starts and retries crashes up to three times. A persisted pause survives restart. Keep only one installation active for these accounts.

The company view is optional: set `network_dashboard_host` to this server's explicit private IPv4 address and allow TCP 8765 only from approved company/VPN ranges. The resulting `http://SERVER_PRIVATE_IP:8765/` is **read-only**. Account, CAPTCHA, pause and publication controls stay on loopback. No public listener or port forwarding is needed.

## 6. Update an existing installation

Do not overwrite a live directory blindly. The existing server may be a ZIP deployment with local-only files.

1. On the local dashboard, **Pause all channels**. Allow an in-flight browser operation to finish; inspect unresolved/uncertain sends. Capture status and current release identifier. Do not treat pausing as cancellation of a click already in flight.
2. Stop only `MeDo-Reddit-Agent`. Close this agent's dedicated browsers after the worker stops. Leave unrelated services and browser sessions alone. Confirm the dashboard listener is no longer serving this worker. If files remain locked, resolve that before continuing.
3. Verify the candidate ZIP checksum. Move the entire stopped installation to a timestamped backup. Never delete `runtime/` to resolve a problem.

```powershell
$ErrorActionPreference = 'Stop'
$PilotRoot = 'C:\MeDo\marketing-agent'
$PilotZip = 'C:\MeDo\incoming\marketing-agent-EXACT_COMMIT.zip'
$PilotBackup = 'C:\MeDo\marketing-agent-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Get-FileHash -Algorithm SHA256 -LiteralPath $PilotZip
Stop-ScheduledTask -TaskName MeDo-Reddit-Agent
# Verify the worker is stopped and dedicated browsers are closed before moving it.
Move-Item -LiteralPath $PilotRoot -Destination $PilotBackup
Expand-Archive -LiteralPath $PilotZip -DestinationPath $PilotRoot
```

4. Copy `.env`, `config.json`, optional `channels.json`, the configured private catalog (if any), and **the complete `runtime/` directory** from the backup into the new root. Use the same Windows host and user. Runtime contains SQLite/WAL files, account bindings, deduplication, cooldowns, outbox receipts and browser profiles. Do not copy a macOS environment or profile to Windows. Keep backup data protected with the same access restrictions as the active installation.

```powershell
foreach ($PilotName in @('.env', 'config.json', 'runtime')) {
    $PilotSource = Join-Path $PilotBackup $PilotName
    if (-not (Test-Path -LiteralPath $PilotSource)) { throw "Missing required state: $PilotName" }
    Copy-Item -LiteralPath $PilotSource -Destination (Join-Path $PilotRoot $PilotName) -Recurse
}
if (Test-Path -LiteralPath (Join-Path $PilotBackup 'channels.json')) {
    Copy-Item -LiteralPath (Join-Path $PilotBackup 'channels.json') -Destination $PilotRoot
}
# Copy any configured private catalog separately, preserving its configured path.
```

5. Do **not** copy `.venv`; run `windows\setup.ps1 -RunTests` in the new root to create the release's dependency environment. Review new configuration fields and any state-schema compatibility notes. On failure, leave the task stopped and roll back.
6. Run `pilot doctor`, then start the existing task. The task still points to the same active root. Do not register a duplicate task. Verify both status endpoints, the expected accounts, paused state, logs and code commit before resuming.

```powershell
Set-Location -LiteralPath $PilotRoot
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\setup.ps1 -RunTests
if ($LASTEXITCODE -ne 0) { throw 'Candidate validation failed; leave task stopped.' }
.\.venv\Scripts\python.exe -X utf8 -m pilot doctor
if ($LASTEXITCODE -ne 0) { throw 'Configuration validation failed; leave task stopped.' }
Start-ScheduledTask -TaskName MeDo-Reddit-Agent
Invoke-RestMethod http://127.0.0.1:8765/api/status
Invoke-RestMethod http://127.0.0.1:8765/api/channels
```

Record release SHA, archive checksum, test results, backup path and operator. Keep a deliberate observation period before retiring the backup. A successful restart is not proof of 24-hour availability.

## 7. Roll back without losing publication history

Pause and stop the new worker and close its dedicated browsers. Preserve the failed installation under a new path; it contains the **latest** runtime state. Restore the old code/environment to the original active path.

**Keep the newest `runtime/` when rolling code back.** Restoring an older database after discovery or publication can forget a send, reset limits and produce duplicate outreach. Preserve both snapshots, move the current runtime into the restored installation, and check that the old code supports its schema. If it does not, keep the worker stopped for a forward fix; do not discard the new outbox.

Restore only a reviewed configuration compatible with the older code. Run `pilot doctor`, start the existing scheduled task, verify status/account bindings and inspect unresolved submissions before resuming. A pre-upgrade runtime snapshot may be used for full disaster recovery only after reconciling all activity since that snapshot. Never replay an uncertain send automatically.

## Acceptance record and known limits

The runtime at `23fb1e6ea053e1d38aa7f89f83c1b9aade5573bb` passed 197 tests on the dedicated Windows server on 2026-11-16, and a real two-pass OneAPI check produced a DEV tutorial without publishing. This is a historical baseline, not a claim about a new release or today's account/network state.

The last verified installation had no connected publication accounts and was paused. Indie Hackers and additional forums were not live-validated; conversion attribution was not connected. New deployment documentation does not change those facts. See [OPERATIONS.md](OPERATIONS.md) for routine checks and incidents and [VALIDATION.md](VALIDATION.md) for the test boundary.
