"""End-to-end browser review workflow.

Runs a full visual + functional review against a target URL using the
claw-aie harness with browser tools. Generates a structured review report.

Usage:
    PYTHONPATH=. python3 -m aie_integration.browser_review \
        --url https://example.com \
        --workspace /tmp/review-output \
        --routes / /about /contact

Or programmatically:
    from aie_integration.browser_review import BrowserReviewer
    reviewer = BrowserReviewer(url="https://example.com")
    report = await reviewer.run()
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .harness import Harness, HarnessResult
from .hooks.aie_emitter import AIEEventEmitter
from .browser_tools import cleanup_browser


@dataclass
class PageReview:
    """Review result for a single page/route."""
    url: str
    title: str = ""
    screenshot_path: str = ""
    console_errors: list[str] = field(default_factory=list)
    a11y_issues: list[dict] = field(default_factory=list)
    visible_assertions: list[dict] = field(default_factory=list)
    load_status: int = 0
    duration_ms: int = 0
    error: str | None = None


@dataclass
class ReviewReport:
    """Complete browser review report."""
    target_url: str
    verdict: str = "pass"  # pass | fail | warn
    pages: list[PageReview] = field(default_factory=list)
    total_console_errors: int = 0
    total_a11y_issues: int = 0
    total_duration_ms: int = 0
    summary: str = ""
    screenshots: list[str] = field(default_factory=list)
    timestamp: str = ""


class BrowserReviewer:
    """Orchestrates a full browser review using claw-aie harness.

    Workflow:
        1. Navigate to target URL
        2. Capture full-page screenshot
        3. Check for console errors
        4. Run accessibility scan
        5. Visit additional routes (if specified)
        6. Repeat steps 2-4 per route
        7. Generate structured report
    """

    def __init__(
        self,
        url: str,
        routes: list[str] | None = None,
        workspace: str | None = None,
        session_id: str | None = None,
        emit_aie: bool = False,
    ):
        self.url = url.rstrip("/")
        self.routes = routes or ["/"]
        self.workspace = Path(workspace or Path.cwd() / ".browser-review")
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or f"review-{int(time.time())}"
        self.emit_aie = emit_aie

        # Build harness
        self.harness = Harness(
            workspace_root=str(self.workspace),
            session_id=self.session_id,
            agent_id="browser-reviewer",
        )
        self.harness.register_browser_tools()

        if emit_aie:
            self.harness.register_hook(AIEEventEmitter(session_id=self.session_id))

    async def run(self) -> ReviewReport:
        """Execute the full browser review workflow."""
        start = time.monotonic()
        pages: list[PageReview] = []

        for route in self.routes:
            full_url = self.url + route
            page_review = await self._review_page(full_url, route)
            pages.append(page_review)

        # Cleanup browser
        await cleanup_browser()

        total_ms = int((time.monotonic() - start) * 1000)

        # Build report
        total_errors = sum(len(p.console_errors) for p in pages)
        total_a11y = sum(len(p.a11y_issues) for p in pages)
        screenshots = [p.screenshot_path for p in pages if p.screenshot_path]

        # Determine verdict
        if any(p.error for p in pages):
            verdict = "fail"
        elif total_errors > 0 or total_a11y > 5:
            verdict = "warn"
        else:
            verdict = "pass"

        # Generate summary
        summary_parts = []
        summary_parts.append(f"Reviewed {len(pages)} page(s) at {self.url}")
        if total_errors:
            summary_parts.append(f"Found {total_errors} console error(s)")
        if total_a11y:
            summary_parts.append(f"Found {total_a11y} accessibility issue(s)")
        if verdict == "pass":
            summary_parts.append("Overall: PASS ✓")
        elif verdict == "warn":
            summary_parts.append("Overall: WARN ⚠ (issues found but not critical)")
        else:
            summary_parts.append("Overall: FAIL ✗ (errors detected)")

        from datetime import datetime, timezone
        return ReviewReport(
            target_url=self.url,
            verdict=verdict,
            pages=pages,
            total_console_errors=total_errors,
            total_a11y_issues=total_a11y,
            total_duration_ms=total_ms,
            summary=". ".join(summary_parts),
            screenshots=screenshots,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def _review_page(self, url: str, route: str) -> PageReview:
        """Review a single page — navigate, screenshot, console check, a11y scan."""
        page = PageReview(url=url)
        page_start = time.monotonic()

        # 1. Navigate
        nav_result = await self.harness.run_tool_directly("browser_navigate", {"url": url, "wait_ms": 10000})
        if nav_result.exit_code != 0:
            page.error = nav_result.output
            page.duration_ms = int((time.monotonic() - page_start) * 1000)
            return page

        try:
            nav_data = json.loads(nav_result.output)
            page.load_status = nav_data.get("status", 0)
            page.title = nav_data.get("title", "")
        except (json.JSONDecodeError, AttributeError):
            page.load_status = nav_result.exit_code

        # 2. Screenshot
        safe_route = route.replace("/", "_").strip("_") or "home"
        screenshot_result = await self.harness.run_tool_directly("browser_screenshot", {
            "full_page": True,
            "save_path": f"{safe_route}.png",
            "_workspace_root": str(self.workspace),
        })
        if screenshot_result.exit_code == 0:
            try:
                ss_data = json.loads(screenshot_result.output)
                page.screenshot_path = ss_data.get("path", "")
            except (json.JSONDecodeError, AttributeError):
                pass

        # 3. Console errors
        console_result = await self.harness.run_tool_directly("browser_console", {})
        if console_result.exit_code == 0:
            try:
                con_data = json.loads(console_result.output)
                errors = con_data.get("errors", [])
                if isinstance(errors, list):
                    page.console_errors = [str(e) for e in errors]
            except (json.JSONDecodeError, AttributeError):
                pass

        # 4. Accessibility scan
        a11y_result = await self.harness.run_tool_directly("browser_accessibility_scan", {})
        if a11y_result.exit_code == 0 or a11y_result.exit_code == 1:
            try:
                a11y_data = json.loads(a11y_result.output)
                page.a11y_issues = a11y_data.get("issues", [])
            except (json.JSONDecodeError, AttributeError):
                pass

        page.duration_ms = int((time.monotonic() - page_start) * 1000)
        return page

    def report_to_markdown(self, report: ReviewReport) -> str:
        """Convert review report to Markdown."""
        lines = [
            f"# Browser Review Report",
            f"",
            f"**Target:** {report.target_url}",
            f"**Verdict:** {report.verdict.upper()}",
            f"**Timestamp:** {report.timestamp}",
            f"**Duration:** {report.total_duration_ms}ms",
            f"**Pages reviewed:** {len(report.pages)}",
            f"",
            f"## Summary",
            f"",
            report.summary,
            f"",
        ]

        for page in report.pages:
            lines.append(f"## {page.url}")
            lines.append(f"")
            lines.append(f"- **Title:** {page.title}")
            lines.append(f"- **Load status:** {page.load_status}")
            lines.append(f"- **Duration:** {page.duration_ms}ms")
            if page.error:
                lines.append(f"- **Error:** {page.error}")
            if page.screenshot_path:
                lines.append(f"- **Screenshot:** `{page.screenshot_path}`")
            if page.console_errors:
                lines.append(f"- **Console errors:** {len(page.console_errors)}")
                for err in page.console_errors[:5]:
                    lines.append(f"  - {err}")
            if page.a11y_issues:
                lines.append(f"- **A11y issues:** {len(page.a11y_issues)}")
                for issue in page.a11y_issues[:10]:
                    lines.append(f"  - `{issue.get('type', 'unknown')}`: {json.dumps(issue)}")
            lines.append("")

        # Overall stats
        lines.append("## Stats")
        lines.append("")
        lines.append(f"- Total console errors: {report.total_console_errors}")
        lines.append(f"- Total a11y issues: {report.total_a11y_issues}")
        lines.append(f"- Total duration: {report.total_duration_ms}ms")
        lines.append(f"- Screenshots: {len(report.screenshots)}")

        return "\n".join(lines)


async def run_review(
    url: str,
    routes: list[str] | None = None,
    workspace: str | None = None,
    emit_aie: bool = False,
    save_report: bool = True,
) -> ReviewReport:
    """Run a browser review and optionally save the report."""
    reviewer = BrowserReviewer(
        url=url,
        routes=routes,
        workspace=workspace,
        emit_aie=emit_aie,
    )
    report = await reviewer.run()

    if save_report and workspace:
        report_path = Path(workspace) / "review-report.md"
        report_path.write_text(reviewer.report_to_markdown(report))
        json_path = Path(workspace) / "review-report.json"
        json_path.write_text(json.dumps(_report_to_dict(report), indent=2, default=str))

    return report


def _report_to_dict(report: ReviewReport) -> dict:
    return {
        "target_url": report.target_url,
        "verdict": report.verdict,
        "pages": [
            {
                "url": p.url,
                "title": p.title,
                "screenshot_path": p.screenshot_path,
                "console_errors": p.console_errors,
                "a11y_issues": p.a11y_issues,
                "load_status": p.load_status,
                "duration_ms": p.duration_ms,
                "error": p.error,
            }
            for p in report.pages
        ],
        "total_console_errors": report.total_console_errors,
        "total_a11y_issues": report.total_a11y_issues,
        "total_duration_ms": report.total_duration_ms,
        "summary": report.summary,
        "screenshots": report.screenshots,
        "timestamp": report.timestamp,
    }


def main():
    parser = argparse.ArgumentParser(description="Browser review via claw-aie")
    parser.add_argument("--url", required=True, help="Target URL to review")
    parser.add_argument("--routes", nargs="*", default=["/"], help="Routes to visit")
    parser.add_argument("--workspace", default=None, help="Output directory")
    parser.add_argument("--aie", action="store_true", help="Emit AIE events")
    args = parser.parse_args()

    workspace = args.workspace or str(Path.cwd() / ".browser-review")
    report = asyncio.run(run_review(
        url=args.url,
        routes=args.routes,
        workspace=workspace,
        emit_aie=args.aie,
    ))

    reviewer = BrowserReviewer.__new__(BrowserReviewer)
    print(reviewer.report_to_markdown(report))
    print(f"\nReport saved to {workspace}/")


if __name__ == "__main__":
    main()
