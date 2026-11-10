# AliasMode browser profiles

AliasMode is an optional local browser backend. Comate OneAPI remains the AI
provider. The integration uses the documented Local API and attaches Playwright
to Chromium profiles; Firefox profiles do not provide the required CDP endpoint.

Install AliasMode from its official release, verify its published checksum, and
select **Local mode**. The API must remain on `127.0.0.1` (default port `50400`).
Do not expose this unauthenticated browser-control API through the dashboard,
firewall, a proxy, or a tunnel.

Create a separate, persistent Chromium profile for each intended account. Keep
the account assigned to that profile. Sign in manually, then close setup and
unrelated tabs before using the profile with the worker. Profile separation does
not guarantee a website will accept automated access.

Add these settings to a copy of `config.browser.example.json`, replacing the IDs
and expected usernames with the actual values:

```json
{
  "browser_backend": "aliasmode",
  "subreddits": ["nocode", "webdev"],
  "aliasmode": {
    "port": 50400,
    "profiles": [
      {"id": "actual-profile-one", "reddit_username": "actual_account_one", "subreddits": ["nocode"]},
      {"id": "actual-profile-two", "reddit_username": "actual_account_two", "subreddits": ["webdev"]}
    ]
  }
}
```

The pilot connects at most two active profiles. Additional profiles may exist in
AliasMode without being selected by the worker. Each selected community belongs
to exactly one profile; IDs and expected usernames must be unique. All profiles
share the same discovery ledger, model budget, review queue and publishing
cooldowns. An account failure never rotates work to another account.

Existing collection permissions, community review, manual publishing approval,
and account verification still apply. Selecting a backend does not enable live
collection or publishing. Keep the worker paused while setting up accounts.

The worker closes its own tab and disconnects when stopped; it leaves the managed
profile open for the operator. Local API failures and invalid debugging addresses
stop the action instead of selecting another profile.

Validation should cover creating two profiles, opening and closing them via the
API, isolated cookies and local storage, persistence after reopening, and a
synthetic OneAPI-driven browser task before using real account sessions.

References: [official source](https://github.com/aliasmode/aliasmode),
[Local API](https://aliasmode.com/docs/local-api/),
[official installer](https://aliasmode.com/download/).
