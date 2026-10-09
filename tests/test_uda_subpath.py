"""Regression coverage for UDA's stripped-path reverse proxy contract.

The test client simulates Caddy's trusted forwarded headers. Live ingress
authorization, host firewall and Caddy rewrites require separate acceptance.
"""
from app import app


def test_uda_prefix_keeps_navigation_assets_and_api_on_app_path():
    client = app.test_client()
    headers = {
        "X-Forwarded-Prefix": "/apps/general-search",
        "X-Forwarded-Host": "tanyaanne.ddns.net",
        "X-Forwarded-Proto": "https",
    }
    response = client.get("/", headers=headers)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<base href="/apps/general-search/">' in html
    assert 'href="/apps/general-search/settings"' in html
    assert '/apps/general-search/static/app.js' in html
    assert 'href="/settings"' not in html
    assert 'href="/"' not in html

    response = client.get("/settings", headers=headers)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<base href="/apps/general-search/">' in html
    assert '/apps/general-search/static/settings.js' in html


def test_plain_lan_routes_still_work():
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert '<base href="/">' in response.get_data(as_text=True)
    assert client.get("/settings").status_code == 200
