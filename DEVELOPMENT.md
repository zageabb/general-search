# Development Status

## UDA-001 — Reverse-proxy subpath compatibility

Status: IN PROGRESS
Scope: UDA registry entry **General Search** only
Branch: `feat/uda-subpath-compatibility`

Requirement: Preserve LAN-root access while working below a UDA-authenticated path such as `/apps/general-search/`.

Implementation on branch:
- Apply Werkzeug `ProxyFix` to respect forwarded host, HTTPS scheme and path prefix from a single trusted proxy. Backend must be reachable only through trusted ingress or the local network, not arbitrary internet clients with forgeable headers.
- Replace hardcoded navigation hrefs with Flask `url_for`.
- Use an HTML base URL derived from Flask for JavaScript's API endpoint resolution, including deeply nested page URLs.
- Preserve existing server endpoints, workflows, storage, upload limits and root-hosted LAN operation.
- Add Flask test-client regression tests for proxy and nonproxy navigation.

Evidence:
- Files: `app.py`, `templates/index.html`, `templates/settings.html`, `static/app.js`, `static/settings.js`, `tests/test_uda_subpath.py`.
- Test execution: NOT VERIFIED in this environment.
- CI: NOT VERIFIED.
- Live UDA/Caddy route and authorization: NOT VERIFIED.
- Production publication: NOT REQUESTED; do not change UDA registry entry or enable public proxy yet.
- User acceptance: pending.

Remaining:
- [ ] Execute regression tests and complete CI.
- [ ] Confirm browser requests (API/search/status/export/settings) remain within the prefix.
- [ ] Test authenticated and unauthorized UDA traffic through Caddy, including hostile forged proxy headers.
- [ ] Test live root-mode LAN access, redirects and any session/cookie behaviour.
- [ ] Review and merge branch after checks.
