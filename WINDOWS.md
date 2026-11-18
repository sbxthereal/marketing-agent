# Windows Server 2022

Use [DEPLOYMENT.md](DEPLOYMENT.md) for the complete current procedure: iCode checkout, pinned release packaging, fresh installation, OneAPI configuration, account connection, Task Scheduler, upgrade, verification and rollback.

Use [OPERATIONS.md](OPERATIONS.md) for daily checks, CAPTCHA/login recovery, logs, uncertain submissions and backups. [MULTIPLATFORM.md](MULTIPLATFORM.md) describes the platform allocation and channel settings; [AUTOMATIC.md](AUTOMATIC.md) describes autonomous replies and promotional posts.

The runtime uses Python, Playwright and Comate OneAPI on Windows. There is no per-post human approval in automatic mode. Credentials, account setup, destination rules and successful signed-in UI validation remain deployment prerequisites.

The writable dashboard runs on `http://127.0.0.1:8765/` inside the server. The optional company-IP dashboard is read-only. The scheduled task remains named `MeDo-Reddit-Agent` and requires the Windows user session to stay signed in. Disconnecting RDP does not mean signing out; after a reboot, sign in again.

Optional managed Reddit sessions retain their existing assignments. See [ALIASMODE.md](ALIASMODE.md) or the [ixBrowser local API documentation](https://www.ixbrowser.com/doc/v2/local-api/en) when using those existing backends. New non-Reddit channels use dedicated persistent Chrome profiles. Never start a second worker on the same runtime or account.
