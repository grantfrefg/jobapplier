"""Where applications really happen, and sign-in / sign-up pages."""

import os

import pytest

from applypilot.browser import launch_chromium
from applypilot.apply import local_engine as le
from applypilot.apply import sites
from test_local_engine import FORM_HTML, _engine_env, _fake_gemini, _serve

headed = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="headed browser needs a display (use xvfb-run)")


@pytest.mark.parametrize("url, password, site, account", [
    ("https://boards.greenhouse.io/acme/jobs/1", False, "greenhouse", "no"),
    ("https://jobs.lever.co/acme/abc/apply", False, "lever", "no"),
    ("https://acme.wd3.myworkdayjobs.com/en-US/Careers/job/Sydney/X_123", False, "workday", "yes"),
    ("https://careers-apac-atlassian.icims.com/jobs/1/job", False, "icims", "yes"),
    ("https://www.seek.com.au/job/81234567/apply", False, "seek", "board"),
    ("https://careers.acme.com.au/apply/1", False, "employer", "no"),
    ("https://careers.acme.com.au/login", True, "employer", "yes"),   # the page asks for a sign-in
])
def test_classify(url, password, site, account):
    found = sites.classify(url, password_field=password)
    assert (found["site"], found["account"]) == (site, account)


def test_easy_apply_is_a_board_login():
    assert sites.classify("https://www.linkedin.com/jobs/view/1", easy_apply=True) == {
        "site": "linkedin_easy", "name": "LinkedIn Easy Apply", "account": "board"}


SIGN_IN = """<!doctype html><html><head><title>Sign in</title></head><body><form onsubmit="return false">
<label for="user">Email address</label><input id="user" type="text">
<label for="pw">Password</label><input id="pw" type="password">
<button type="submit">Sign In</button> <button type="button" onclick="window.next = true">Continue</button>
<a href="/register.html">Create account</a></form></body></html>"""

SIGN_UP = """<!doctype html><html><head><title>Create Account</title></head><body><form>
<label for="em">Email</label><input id="em" type="email">
<label for="p1">Password</label><input id="p1" type="password">
<label for="p2">Verify New Password</label><input id="p2" type="password">
<button type="button">Create Account</button></form></body></html>"""


def test_locate_follows_the_ad_to_the_application(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    site = tmp_path / "site"
    site.mkdir()
    (site / "signin.html").write_text(SIGN_IN)
    (site / "form.html").write_text(FORM_HTML)
    (site / "ad.html").write_text('<a href="/signin.html">Apply on company site</a>')
    (site / "ad2.html").write_text('<a href="/form.html">Apply now</a>')
    (site / "ad3.html").write_text('<button>Easy Apply</button>')
    monkeypatch.setattr(le, "_is_job_listing", lambda url: "/ad" in url)
    server, base = _serve(site)
    try:
        with sync_playwright() as p:
            browser = launch_chromium(p)
            page = browser.new_page()
            found = sites.locate(page, f"{base}/ad.html")
            assert found["account"] == "yes" and found["url"].endswith("signin.html")
            found = sites.locate(page, f"{base}/ad2.html")
            assert found["account"] == "no" and found["url"].endswith("form.html")
            assert sites.locate(page, f"{base}/ad3.html")["site"] == "linkedin_easy"
            browser.close()
    finally:
        server.shutdown()


@headed
def test_sign_in_page_gets_email_only_and_no_ai(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    site = tmp_path / "site"
    site.mkdir()
    (site / "signin.html").write_text(SIGN_IN)
    (site / "form.html").write_text(FORM_HTML)
    server, base = _serve(site)
    resume = _engine_env(tmp_path, monkeypatch)
    prompts = []
    monkeypatch.setattr(le, "_call_ai", _fake_gemini(prompts))
    seen = []

    def gate(page, needs, dry_run):
        if not seen:
            seen.append(dict(needs=needs, **page.evaluate(
                "() => ({user: user.value, pw: pw.value, next: !!window.next})")))
            page.goto(f"{base}/form.html")   # the user signs in and lands on the form
            return "rescan"
        seen.append({"fn": page.evaluate("() => fn.value")})
        return "manual"

    job = {"title": "Engineer", "site": "x", "url": f"{base}/signin.html", "application_url": None,
           "tailored_resume_path": resume, "cover_letter_path": None, "fit_score": 9}
    try:
        assert le.run_job(job, gate=gate)[0] == "manual"
    finally:
        server.shutdown()
    first = seen[0]
    assert first["user"] == "jane.doe@example.com" and first["pw"] == "" and first["next"] is False
    assert "Sign in to" in first["needs"][0]
    assert seen[1]["fn"] == "Jane"
    assert all("Sign In" not in p for p in prompts) and len(prompts) <= 1  # the sign-in page cost no AI


@headed
def test_create_account_page_is_explained(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    site = tmp_path / "site"
    site.mkdir()
    (site / "signup.html").write_text(SIGN_UP)
    server, base = _serve(site)
    resume = _engine_env(tmp_path, monkeypatch)
    monkeypatch.setattr(le, "_call_ai", lambda *a: pytest.fail("sign-up pages aren't sent to the AI"))
    seen = {}

    def gate(page, needs, dry_run):
        seen.update(needs=needs, em=page.evaluate("() => em.value"), p1=page.evaluate("() => p1.value"))
        return "manual"

    job = {"title": "Engineer", "site": "x", "url": f"{base}/signup.html", "application_url": None,
           "tailored_resume_path": resume, "cover_letter_path": None, "fit_score": 9}
    try:
        le.run_job(job, gate=gate)
    finally:
        server.shutdown()
    assert "create an account" in seen["needs"][0] and seen["em"] == "jane.doe@example.com" and seen["p1"] == ""


@headed
def test_long_form_with_optional_password_is_filled_normally(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    site = tmp_path / "site"
    site.mkdir()
    extra = "".join(f'<label for="q{i}">Question {i}</label><input id="q{i}">' for i in range(6))
    (site / "form.html").write_text(FORM_HTML.replace(
        '<button type="button" id="cont"',
        extra + '<label for="pw">Create a password to track your application</label>'
                '<input id="pw" type="password"><button type="button" id="cont"'))
    server, base = _serve(site)
    resume = _engine_env(tmp_path, monkeypatch)
    seen = {}

    def gate(page, needs, dry_run):
        seen.update(fn=page.evaluate("() => fn.value"), pw=page.evaluate("() => pw.value"))
        return "manual"

    job = {"title": "Engineer", "site": "x", "url": f"{base}/form.html", "application_url": None,
           "tailored_resume_path": resume, "cover_letter_path": None, "fit_score": 9}
    try:
        le.run_job(job, gate=gate)
    finally:
        server.shutdown()
    assert seen["fn"] == "Jane" and seen["pw"] == ""
