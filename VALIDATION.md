# Validation

The Windows port replaces Unix-only locking, detects Chrome across platforms, reads UTF-8 files with Windows BOMs, supports optional headless operation, and uses a Windows-specific dependency lockfile without macOS-only PyObjC packages.

The [CI workflow](.github/workflows/ci.yml) runs portable tests on Linux/macOS and installation/browser tests on **Windows Server 2022**. Consult GitHub Actions for the status of the current commit. The Windows job parses the PowerShell scripts, checks Browser Use imports, and constructs the scheduled task using `-WhatIf` without registering it.

Browser tests intercept test-page requests and never publish to Reddit. Coverage includes extraction, search/feed deduplication, full-thread reading, account checks, approved-text verification, permalink read-back, cooldowns, uncertain submissions, cross-process locking and an isolated production-browser launch. CI has no OneAPI key or Reddit login and makes no live model calls.

The port's 75 automated tests passed locally on macOS. Earlier bounded OneAPI development checks matched all ten expected recommend/skip/review decisions across four real discussions and six synthetic cases. This small evaluation is not a production accuracy estimate. Private runtime data and captured discussions are excluded from GitHub.

Destination-server validation still requires installation, OneAPI/network access, dedicated-browser login, actual Reddit permission, supervised discovery and one authorized reply. Then test RDP disconnect/reconnect and a 24-hour run. CI cannot establish Reddit approval, account health, destination connectivity, live publishing reliability or uptime after sign-out/reboot.
