# Windows Server 2022 setup

Use **Windows Server 2022 x64 with Desktop Experience**, Google Chrome, and access to your authorized OneAPI network. All AI runs through OneAPI; Codex and this chat do not need to stay open.

## Install

Open PowerShell as the account that will run the agent. Use a local folder owned by that account. With Git installed:

```powershell
git clone https://github.com/sbxthereal/marketing-agent.git
cd marketing-agent
```

Alternatively, download the ZIP from GitHub, extract it, and open PowerShell in that folder. If uv is not installed, use its official installer:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
```

Install dependencies and run tests:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\setup.ps1 -InstallChrome -RunTests
```

Setup creates a Python 3.14 environment, installs the Windows lockfile, protects local credentials/runtime with NTFS permissions, and creates configuration files. Chrome is installed only if missing. The browser tests use intercepted local pages, not live Reddit. The execution-policy option applies only to these processes; the scripts do not change machine policy.

## Configure and sign in

```powershell
notepad .env
notepad config.json
```

Put your authorized OneAPI URL and key in `.env`; the example hostname is a placeholder. Do not put credentials in GitHub or screenshots. The server needs access to that endpoint, including company network/VPN where required.

In `config.json`, choose models available through that gateway, select one or two communities, record actual collection/AI permission and community review, and set `publishing.reddit_username`. Keep publishing disabled initially. The supplied community is a candidate, not an approval record.

Stop the worker before opening its dedicated login profile:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pilot login
```

Sign in through the visible Chrome window, then press Enter in PowerShell. The login command always opens visible Chrome; it does not copy everyday browser cookies.

## First supervised run

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pilot doctor
.\.venv\Scripts\python.exe -X utf8 -m pilot serve
```

Open **http://127.0.0.1:8765/** on the server. `doctor` checks local configuration/components; it does not authenticate OneAPI. To test the actual gateway and Browser Use on a local fixture, stop the worker and run this bounded probe, which incurs model usage:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\agent_probe.py --env-file .env --output runtime\oneapi-probe.json
```

The probe makes no Reddit requests. For live work, record actual permission first. Enable the separate publishing settings only when permission covers replies. Approve a draft and explicitly queue **Publish approved reply**; approval alone never sends.

## Start after login

After the supervised run succeeds, stop it with Ctrl+C and register the scheduled task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\install-task.ps1 -StartNow
```

It runs as the current user with limited privileges, starts at that user's login, prevents duplicate starts, has no execution time limit, and retries crashes up to three times at one-minute intervals. A paused worker stays paused. No Windows password is stored in the repository.

```powershell
Get-ScheduledTask -TaskName MeDo-Reddit-Agent
Get-ScheduledTaskInfo -TaskName MeDo-Reddit-Agent
Get-Content .\runtime\worker.log -Tail 50
```

Logs rotate at 5 MB with three backups.

**Disconnecting RDP is different from signing out.** This task requires the user's session to exist. Closing RDP can leave it running, but server policies may log disconnected sessions off. Signing out stops the session; after reboot you must sign in again. The installer does not enable automatic Windows login or change session policies.

To reduce reliance on desktop rendering, stop the worker, set `browser_headless` to true in `config.json`, then restart. Validate Reddit access and login in that mode on your server. Headless mode does not change this task's login requirement or bypass restrictions. Test disconnect/reconnect and a 24-hour run before relying on unattended operation. Running before login would require a separate service deployment.

Stop/remove the scheduled task before manual login or maintenance:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\install-task.ps1 -Uninstall
```

Stopping during a send may leave an uncertain outcome. Reconciliation reads an existing comment without resending:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pilot reconcile --item t3_POST_ID --url 'https://www.reddit.com/r/COMMUNITY/comments/POST_ID/slug/COMMENT_ID/'
```

## Updates

Stop the task, run `git pull --ff-only`, and rerun setup. Existing configuration is preserved. Keep the database containing deduplication and cooldown history. Do not copy a macOS virtual environment or Chrome profile to Windows; install and log in again. Stop the old worker before transferring its database, and use only one active installation per Reddit account.

## Company network view

The review desk stays at `http://127.0.0.1:8765/` inside the server. To share a **read-only** view with colleagues, set `network_dashboard_host` in `config.json` to the server's explicit RFC1918 IPv4 address and restart. The worker opens a second listener at `http://SERVER_PRIVATE_IP:8765/`; wildcard/public addresses are rejected. It accepts private IPv4 clients only, never includes a write token, and rejects every POST. Review, pause/resume, and publishing remain local to the server.

Scope any Windows firewall/network rule to that private address, TCP 8765, and your actual company/VPN ranges. Do not add public port forwarding. Everyone allowed onto that view can see the captured public discussions, draft recommendations, and experiment metrics; the OneAPI key is never returned. Network routing and company access policies still apply.

References: [Playwright requirements](https://playwright.dev/python/docs/intro), [uv installer](https://docs.astral.sh/uv/getting-started/installation/), [Windows task security contexts](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks), [restart settings](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset).
