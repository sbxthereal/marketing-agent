# macOS setup

For Windows Server 2022, use [WINDOWS.md](WINDOWS.md).

Use the M2/16GB Mac with Chrome, power, and access to your authorized OneAPI network. Model inference runs remotely through OneAPI. Codex is not required on this Mac and this chat does not need to stay open. A stronger server is not required to begin this single-browser pilot.

1. Copy this folder onto the Mac. In Terminal, enter the folder and run `bash setup.sh`. This installs the pinned dependencies into `.venv` and creates configuration files without enabling collection or publishing.
2. Put the authorized gateway key into `.env`. The package contains no key. In `config.json`, choose the pilot communities, record the actual browser/AI access permission and community review under `permissions`, and set the expected Reddit username under `publishing`. Do not mark permission flags true before approval exists. The default community is a candidate, not an approval record.
3. Run `.venv/bin/python -m pilot login`. Sign into the intended account in the dedicated Chrome window, then press Enter in Terminal. It does not copy your everyday Chrome cookies.
4. Run `.venv/bin/python -m pilot doctor`, then `.venv/bin/python -m pilot serve`. Open http://127.0.0.1:8765. Start with discovery and drafts; publishing can remain disabled.
5. Once the permission covers browser replies, set `publishing.enabled`, `publishing.permission_confirmed` and its reference, and restart. Review a draft, approve it, then explicitly queue **Publish approved reply**. An approved draft is not an instruction to send until queued.
6. After a supervised run succeeds, stop the foreground worker and install the Mac launcher:

```sh
.venv/bin/python macos/launchagent.py --install
```

The launcher starts after user login, prevents idle sleep while running, and restarts after a process crash. It cannot unlock FileVault, log into macOS, restore an expired VPN, solve a challenge, or guarantee operation with a laptop lid closed. Keep the Mac logged in, plugged in and connected. Run a 24-hour soak test before treating it as unattended infrastructure.

To stop/remove the launcher:

```sh
.venv/bin/python macos/launchagent.py --uninstall
```

Do not share `.env`, `config.json`, or `runtime/`. The browser profile and SQLite database stay in `runtime/`. Source content and outbox drafts expire after the configured retention period; deduplication markers remain. Back up that runtime securely if you move the installation, since starting with an empty database loses its history of prior replies.

Reply limits and discovery settings are explained in UPGRADE.md. If upgrading an existing installation, merge the new settings from config.browser.example.json into your private config.json; setup.sh preserves existing configuration. Keep your runtime history when upgrading.
