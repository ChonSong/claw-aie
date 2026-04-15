"""End-to-end test: browser review against a live target.

Tests the full workflow: Harness → browser tools → review report generation.
Uses httpbin or a lightweight local server as the target.
"""

import asyncio
import json
import pytest
from pathlib import Path

from aie_integration.browser_review import BrowserReviewer, run_review, _report_to_dict


# We use a well-known public endpoint that's reliable for testing
# httpbin is hosted by Postman and very stable
TEST_URL = "https://httpbin.org"


@pytest.fixture
def review_workspace(tmp_path):
    """Temporary workspace for review output."""
    ws = tmp_path / "review"
    ws.mkdir()
    return ws


@pytest.mark.asyncio
async def test_e2e_navigate_and_screenshot(review_workspace):
    """Navigate to a page and capture screenshot."""
    reviewer = BrowserReviewer(
        url=TEST_URL,
        routes=["/html"],
        workspace=str(review_workspace),
    )

    report = await reviewer.run()

    assert report.target_url == TEST_URL
    assert len(report.pages) == 1
    page = report.pages[0]
    assert page.url == f"{TEST_URL}/html"
    assert page.load_status == 200
    assert page.duration_ms > 0
    # Screenshot should be saved
    assert page.screenshot_path != ""

    await _cleanup()


@pytest.mark.asyncio
async def test_e2e_a11y_scan(review_workspace):
    """Accessibility scan runs and returns issues."""
    reviewer = BrowserReviewer(
        url=TEST_URL,
        routes=["/html"],
        workspace=str(review_workspace),
    )

    report = await reviewer.run()
    page = report.pages[0]
    # httpbin/html is simple — might have 0 or few a11y issues
    assert isinstance(page.a11y_issues, list)

    await _cleanup()


@pytest.mark.asyncio
async def test_e2e_multiple_routes(review_workspace):
    """Review multiple routes in sequence."""
    reviewer = BrowserReviewer(
        url=TEST_URL,
        routes=["/html", "/forms/post"],
        workspace=str(review_workspace),
    )

    report = await reviewer.run()

    assert len(report.pages) == 2
    assert report.pages[0].url == f"{TEST_URL}/html"
    assert report.pages[1].url == f"{TEST_URL}/forms/post"
    assert report.total_duration_ms > 0

    await _cleanup()


@pytest.mark.asyncio
async def test_e2e_verdict_pass_on_clean_page(review_workspace):
    """Clean page should get 'pass' verdict."""
    reviewer = BrowserReviewer(
        url=TEST_URL,
        routes=["/html"],
        workspace=str(review_workspace),
    )

    report = await reviewer.run()
    # httpbin/html is simple, should pass or at worst warn
    assert report.verdict in ("pass", "warn")

    await _cleanup()


@pytest.mark.asyncio
async def test_e2e_report_markdown(review_workspace):
    """Markdown report is generated correctly."""
    reviewer = BrowserReviewer(
        url=TEST_URL,
        routes=["/html"],
        workspace=str(review_workspace),
    )

    report = await reviewer.run()
    md = reviewer.report_to_markdown(report)

    assert "# Browser Review Report" in md
    assert TEST_URL in md
    assert "## Summary" in md
    assert report.pages[0].url in md

    await _cleanup()


@pytest.mark.asyncio
async def test_e2e_report_json(review_workspace):
    """JSON report serializes correctly."""
    reviewer = BrowserReviewer(
        url=TEST_URL,
        routes=["/html"],
        workspace=str(review_workspace),
    )

    report = await reviewer.run()
    data = _report_to_dict(report)

    assert data["target_url"] == TEST_URL
    assert "pages" in data
    assert len(data["pages"]) == 1
    assert data["pages"][0]["url"] == f"{TEST_URL}/html"
    # Should be JSON-serializable
    json_str = json.dumps(data, default=str)
    assert json_str

    await _cleanup()


@pytest.mark.asyncio
async def test_e2e_run_review_saves_files(review_workspace):
    """run_review saves markdown and JSON reports to disk."""
    report = await run_review(
        url=TEST_URL,
        routes=["/html"],
        workspace=str(review_workspace),
    )

    assert (review_workspace / "review-report.md").exists()
    assert (review_workspace / "review-report.json").exists()

    md_content = (review_workspace / "review-report.md").read_text()
    assert "Browser Review Report" in md_content

    json_content = (review_workspace / "review-report.json").read_text()
    data = json.loads(json_content)
    assert data["target_url"] == TEST_URL

    await _cleanup()


@pytest.mark.asyncio
async def test_e2e_screenshots_saved(review_workspace):
    """Screenshots are actually saved to disk."""
    reviewer = BrowserReviewer(
        url=TEST_URL,
        routes=["/html"],
        workspace=str(review_workspace),
    )

    report = await reviewer.run()
    page = report.pages[0]

    if page.screenshot_path:
        screenshot = Path(page.screenshot_path)
        assert screenshot.exists()
        assert screenshot.stat().st_size > 1000  # PNG should be >1KB

    await _cleanup()


@pytest.mark.asyncio
async def test_e2e_404_page(review_workspace):
    """404 page is handled gracefully."""
    reviewer = BrowserReviewer(
        url=TEST_URL,
        routes=["/status/404"],
        workspace=str(review_workspace),
    )

    report = await reviewer.run()
    page = report.pages[0]
    # Should still work — 404 is a valid response
    assert page.load_status == 404 or page.error is not None

    await _cleanup()


async def _cleanup():
    """Ensure browser is cleaned up between tests."""
    from aie_integration.browser_tools import cleanup_browser
    await cleanup_browser()
