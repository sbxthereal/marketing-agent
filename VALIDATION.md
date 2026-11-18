# Validation

The Windows port replaces Unix-only locking, detects Chrome across platforms, reads UTF-8 files with Windows BOMs, supports optional headless operation, and uses a Windows-specific dependency lockfile without macOS-only PyObjC packages.

The [CI workflow](.github/workflows/ci.yml) runs portable tests on Linux/macOS and installation/browser tests on **Windows Server 2022**. Consult GitHub Actions for the status of the current commit. The Windows job parses the PowerShell scripts, checks Browser Use imports, and constructs the scheduled task using `-WhatIf` without registering it.

Browser tests intercept test-page requests and never publish to Reddit. Coverage includes extraction, search/feed deduplication, full-thread reading, account checks, approved-text verification, permalink read-back, cooldowns, uncertain submissions, cross-process locking and an isolated production-browser launch. CI has no OneAPI key or Reddit login and makes no live model calls.

At runtime commit `23fb1e6ea053e1d38aa7f89f83c1b9aade5573bb`, 197 tests passed on the dedicated Windows server (63.775 seconds) and locally on macOS (82.761 seconds) on 2026-11-16. GitHub CI passed Windows Server 2022, macOS and Ubuntu. A real two-pass Comate OneAPI check produced a 530-word DEV tutorial without any social-site publication. These are historical results, not a claim that every later release or current account state is validated. Earlier bounded OneAPI development checks matched all ten expected recommend/skip/review decisions across four real discussions and six synthetic cases. This small evaluation is not a production accuracy estimate. Private runtime data and captured discussions are excluded from GitHub.

Destination-server validation still requires installation, OneAPI/network access, dedicated-browser login, actual Reddit permission, supervised discovery and one authorized reply. Then test RDP disconnect/reconnect and a 24-hour run. CI cannot establish Reddit approval, account health, destination connectivity, live publishing reliability or uptime after sign-out/reboot.

The GitHub Actions workflow is preserved for provenance; importing this repository to iCode does not create an iPipe build or deployment job. Run the documented checks on an approved runner. The last verified server was paused with no connected publication accounts; new platform adapters still require signed-in live validation.
