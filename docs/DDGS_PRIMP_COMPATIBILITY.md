# DDGS/primp compatibility

The previous `ddgs==9.5.5` dependency could select browser impersonation profiles that were not supported by the installed HTTP client. Searches could consequently fail with errors such as `Invalid impersonate: "chrome_107"`, `Invalid impersonate: "chrome_123"`, `Invalid impersonate: "safari_17.2.1"`, or `Invalid impersonate: "safari_17.5"`.

The compatible search-client pair is pinned to:

- `ddgs==9.16.0`
- `primp==2.0.1`

Keep these versions aligned when updating the search client. The existing multi-engine fallback behaviour remains unchanged.
