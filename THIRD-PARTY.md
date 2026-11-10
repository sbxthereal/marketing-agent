# Open-source components

Sources cloned and inspected on 2026-11-09:

| Project | Inspected revision | Use |
| --- | --- | --- |
| https://github.com/browser-use/browser-use | b49a68e4060acb226da8f784194fef301c53a6d7 | Browser agent runtime; installed PyPI version 0.13.14, MIT |
| https://github.com/tanzeeldevAi/reddit-lead-generation-ai-agent | d71299cb269df28b97826ef4e0fbfb49d169ffd5 | Design review/reference only; not installed, MIT |
| https://github.com/niihhhall/subreach | 03ab3d0f7c90c0ec4f361d4d33595706363882a0 | Design review/reference only; not installed, MIT |

Playwright 1.64.1 provides routine browser actions (Apache-2.0). The macOS environment is pinned in requirements.lock; the Windows environment is resolved separately in requirements-windows.lock. Packages were obtained from PyPI; downloaded wheels were checked against PyPI SHA-256 metadata during this build. Upstream source checkouts are research material, not vendored into this package. No other project's campaign personas, account credentials, warm-up tactics, or automatic promotional campaigns are used.

Browser Use runs locally against a dedicated Chrome profile. Its paid cloud service and profile sync are not used. Telemetry is disabled before Browser Use is imported. Model calls use the configured Comate OneAPI endpoint.
# ixBrowser integration

The optional adapter uses the desktop client's [documented local API](https://www.ixbrowser.com/doc/v2/local-api/en) and Playwright CDP attachment. No ixBrowser binaries or SDK code are bundled. The desktop client and API access must be obtained from ixBrowser separately.
