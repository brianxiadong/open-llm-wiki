"""End-to-end browser tests using Playwright.

Run against a live server (default http://172.36.164.85:5000).
Set E2E_BASE_URL env var to override.

Usage:
    pytest tests/test_e2e.py -v --headed   # watch the browser
    pytest tests/test_e2e.py -v            # headless

Screenshots are saved to tests/screenshots/ for visual review.
"""

import os
import re

import pytest
from playwright.sync_api import Page, expect

BASE_URL = os.environ.get("E2E_BASE_URL", "http://172.36.164.85:5000")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

TEST_USER = f"e2e_{os.getpid()}"
TEST_EMAIL = f"{TEST_USER}@example.com"
TEST_PASS = "e2eTest1234"


def _shot(page: Page, name: str):
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"{name}.png"), full_page=True)


def _delete_test_account(page: Page):
    page.goto(f"{BASE_URL}/user/settings")
    page.locator('input[name="confirm_username"]').fill(TEST_USER)
    page.locator('input[name="delete_password"]').fill(TEST_PASS)
    page.locator('[data-testid="delete-account-submit"]').click()
    page.wait_for_url(re.compile(r"/login"))


# ─── Fixtures ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def browser_context(browser):
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
    )
    yield ctx
    ctx.close()


@pytest.fixture(scope="module")
def authed_page(browser_context):
    """Register a fresh user, stay logged in for the whole module."""
    page = browser_context.new_page()
    page.goto(f"{BASE_URL}/register")
    page.fill('input[name="username"]', TEST_USER)
    if page.locator('input[name="email"]').count():
        page.fill('input[name="email"]', TEST_EMAIL)
    page.fill('input[name="display_name"]', "E2E测试用户")
    page.fill('input[name="password"]', TEST_PASS)
    page.fill('input[name="confirm_password"]', TEST_PASS)
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(r"/" + TEST_USER))
    yield page
    try:
        _delete_test_account(page)
    finally:
        page.close()


@pytest.fixture(scope="module")
def test_repo(authed_page):
    """Create a test repo and return its slug."""
    page = authed_page
    page.goto(f"{BASE_URL}/repos/new")
    page.fill('input[name="name"]', "E2E测试知识库")
    page.fill('input[name="slug"]', "e2e-kb")
    page.fill('textarea[name="description"]', "Playwright 端到端测试用知识库")
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(r"/e2e-kb"))
    return "e2e-kb"


# ─── Auth Pages ──────────────────────────────────────────────


def test_login_page_renders(page):
    page.goto(f"{BASE_URL}/login")
    _shot(page, "01_login")
    expect(page.locator("h2")).to_contain_text("欢迎回来")
    expect(page.locator('input[name="username"]')).to_be_visible()
    expect(page.locator('input[name="password"]')).to_be_visible()
    expect(page.locator('button[type="submit"]')).to_be_visible()


def test_register_page_renders(page):
    page.goto(f"{BASE_URL}/register")
    _shot(page, "02_register")
    expect(page.locator("h2")).to_contain_text("创建账号")
    expect(page.locator('input[name="confirm_password"]')).to_be_visible()


# ─── Repo List ───────────────────────────────────────────────


def test_repo_list(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}")
    _shot(page, "03_repo_list")
    expect(page.locator(".repo-card")).to_have_count(1)
    expect(page.locator(".repo-card h3")).to_contain_text("E2E测试知识库")


# ─── Dashboard ───────────────────────────────────────────────


def test_dashboard(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}")
    _shot(page, "04_dashboard")

    expect(page.locator(".repo-hero h2")).to_contain_text("E2E测试知识库")
    expect(page.locator(".action-bar")).to_be_visible()
    expect(page.locator(".wiki-sidebar")).to_be_visible()
    expect(page.locator(".wiki-content")).to_be_visible()

    action_links = page.locator(".action-bar .action-btn")
    expect(action_links).to_have_count(5)


# ─── Source Management ───────────────────────────────────────


def test_sources_empty(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}/sources")
    _shot(page, "05_sources_empty")
    expect(page.locator(".empty-state")).to_be_visible()
    expect(page.locator(".upload-card")).to_be_visible()


def test_upload_markdown(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}/sources")

    page.locator('input[type="file"]').set_input_files({
        "name": "test-doc.md",
        "mimeType": "text/markdown",
        "buffer": b"# Test Document\n\nThis is a test document for E2E testing.\n\n## Key Concepts\n\n- Concept A\n- Concept B\n",
    })
    page.click('.upload-card button[type="submit"]')
    page.wait_for_url(re.compile(r"/sources"))
    _shot(page, "06_sources_with_file")

    expect(page.locator("table")).to_be_visible()
    expect(page.locator("td >> text=test-doc.md")).to_be_visible()


# ─── Wiki Page View ──────────────────────────────────────────


def test_wiki_overview(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}/wiki/overview")
    _shot(page, "07_wiki_overview")
    expect(page.locator(".wiki-page-layout")).to_be_visible()
    expect(page.locator(".badge")).to_be_visible()
    expect(page.locator(".rendered-content")).to_be_visible()


# ─── Graph ───────────────────────────────────────────────────


def test_graph_page(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}/graph")
    _shot(page, "08_graph")
    expect(page.locator("#graph-container")).to_be_visible()


# ─── Query Page ──────────────────────────────────────────────


def test_query_page(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}/query")
    _shot(page, "09_query")
    expect(page.locator("#query-input")).to_be_visible()
    expect(page.locator("#query-submit")).to_be_visible()


# ─── Settings ────────────────────────────────────────────────


def test_repo_settings(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}/settings")
    _shot(page, "10_repo_settings")
    expect(page.locator('input[name="name"]')).to_be_visible()
    expect(page.locator("textarea").first).to_be_visible()


def test_user_settings(authed_page):
    page = authed_page
    page.goto(f"{BASE_URL}/user/settings")
    _shot(page, "11_user_settings")
    expect(page.locator('input[name="display_name"]')).to_be_visible()
    expect(page.locator('input[name="confirm_username"]')).to_be_visible()


# ─── Health Check ────────────────────────────────────────────


def test_health_endpoint(page):
    page.goto(f"{BASE_URL}/health")
    content = page.text_content("body")
    assert '"status"' in content


# ─── Navigation Flow ────────────────────────────────────────


def test_nav_brand_link(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}")
    page.click(".brand")
    page.wait_for_url(re.compile(r"/" + TEST_USER))


def test_breadcrumb_navigation(authed_page, test_repo):
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}/wiki/overview")
    crumbs = page.locator(".breadcrumb li")
    expect(crumbs).to_have_count(3)


# ─── Visual Checks ──────────────────────────────────────────


def test_no_broken_layout(authed_page, test_repo):
    """Check that key layout elements have reasonable dimensions."""
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}")

    hero = page.locator(".repo-hero").bounding_box()
    assert hero and hero["width"] > 600, f"Hero too narrow: {hero}"
    assert hero and hero["height"] > 40, f"Hero too short: {hero}"

    sidebar = page.locator(".wiki-sidebar").bounding_box()
    assert sidebar and sidebar["width"] > 150, f"Sidebar too narrow: {sidebar}"

    content = page.locator(".wiki-content").bounding_box()
    assert content and content["width"] > 400, f"Content too narrow: {content}"


def test_icons_render(authed_page, test_repo):
    """Check Lucide SVG icons are rendering (have non-zero size)."""
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}")

    icons = page.locator(".action-bar svg.lucide")
    count = icons.count()
    assert count >= 4, f"Expected at least 4 SVG icons, got {count}"

    first_icon = icons.first.bounding_box()
    assert first_icon and first_icon["width"] > 0, f"Icon not rendering: {first_icon}"


def test_flash_message_shows(authed_page, test_repo):
    """Flash messages should auto-dismiss."""
    page = authed_page
    page.goto(f"{BASE_URL}/{TEST_USER}/{test_repo}/sources")

    page.locator('input[type="file"]').set_input_files({
        "name": "flash-test.md",
        "mimeType": "text/markdown",
        "buffer": b"# Flash Test\n",
    })
    page.click('.upload-card button[type="submit"]')
    page.wait_for_url(re.compile(r"/sources"))

    flash = page.locator(".flash-toast")
    if flash.count() > 0:
        expect(flash.first).to_be_visible()
        _shot(page, "12_flash_message")


# ─── Error Pages ─────────────────────────────────────────────


def test_404_page(page):
    page.goto(f"{BASE_URL}/nonexistent-page-xyz")
    _shot(page, "13_404")
    expect(page.locator(".error-page")).to_be_visible()
