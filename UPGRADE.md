# Discovery and reply behavior

All runtime AI uses OneAPI. Routine browsing uses local Playwright, with a bounded Browser Use/OneAPI fallback for unfamiliar layouts.

Discovery combines community new feeds and rotating problem searches. It deduplicates IDs before model work, prioritizes help requests, retains unusual wording, and increases waiting items' priority. Idle scans back off from five to thirty minutes. Coverage remains limited to selected communities and bounded pages.

One model call qualifies the full discussion and drafts where appropriate. Only proposed recommendations get a second metered editing/claim-check call. Replies name MeDo early, use specific request details, provide one starting step, and disclose affiliation. Generic sales language, unsupported claims, no-promotion requests, solved problems and similar recent replies are rejected or held.

Default ceilings: three replies per rolling 24 hours globally, two hours apart; two per community per rolling 24 hours, six hours apart; no repeat pitch to the same author for seven days. With one community, the effective ceiling is two replies/day. These are maxima, not quotas or Reddit-approved allowances.

Visible existing MeDo mentions, prior account participation, changed/locked posts, wrong accounts and uncertain submissions prevent another pitch. An uncertain send pauses the worker and is never automatically retried.

Useful next improvements: tested MeDo examples, approval/rejection feedback, registration attribution, and reviewed follow-ups within an existing conversation. Optimize for useful qualified conversations rather than reply counts.

Reddit's [spam rules](https://support.reddithelp.com/hc/en-us/articles/360043504051-Spam) apply; spacing irrelevant promotion does not make it acceptable.
