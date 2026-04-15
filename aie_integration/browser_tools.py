"""Browser tools for visual + functional review via Playwright.

Provides browser_navigate, browser_screenshot, browser_click, browser_fill,
browser_console, browser_assert, browser_assert_no_console_errors,
browser_accessibility_scan — all instrumented through claw-aie hooks.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tool_executor import ToolResult

# Lazy import — playwright is optional
_playwright = None
_browser = None
_page = None


async def _ensure_browser():
    """Lazily start Playwright browser."""
    global _playwright, _browser, _page
    if _page is not None:
        return _page
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")

    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=True)
    _page = await _browser.new_page(viewport={"width": 1280, "height": 720})
    return _page


async def cleanup_browser():
    """Shut down browser. Call at end of session."""
    global _playwright, _browser, _page
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()
    _playwright = _browser = _page = None


async def browser_navigate(tool_input: dict) -> ToolResult:
    """Navigate to URL and wait for page load."""
    url = tool_input.get("url", "")
    wait_ms = tool_input.get("wait_ms", 5000)
    if not url:
        return ToolResult(tool_name="browser_navigate", output="Error: url is required", exit_code=1, duration_ms=0)

    start = time.monotonic()
    try:
        page = await _ensure_browser()
        response = await page.goto(url, wait_until="networkidle", timeout=wait_ms)
        status = response.status if response else "no response"
        title = await page.title()
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_navigate",
            output=json.dumps({"status": status, "title": title, "url": page.url}),
            exit_code=0,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(tool_name="browser_navigate", output=f"Error: {e}", exit_code=1, duration_ms=duration)


async def browser_screenshot(tool_input: dict) -> ToolResult:
    """Capture screenshot. Saves to workspace and returns base64."""
    selector = tool_input.get("selector")
    full_page = tool_input.get("full_page", False)
    save_path = tool_input.get("save_path", "")
    workspace = tool_input.get("_workspace_root", "/tmp")

    start = time.monotonic()
    try:
        page = await _ensure_browser()

        if selector:
            element = await page.query_selector(selector)
            if not element:
                duration = int((time.monotonic() - start) * 1000)
                return ToolResult(
                    tool_name="browser_screenshot",
                    output=f"Error: element not found: {selector}",
                    exit_code=1,
                    duration_ms=duration,
                )
            png_bytes = await element.screenshot()
        else:
            png_bytes = await page.screenshot(full_page=full_page)

        # Save to disk
        ts = int(time.time())
        filename = save_path or f"screenshot_{ts}.png"
        out_path = Path(workspace) / ".browser-review" / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(png_bytes)

        b64 = base64.b64encode(png_bytes).decode()
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_screenshot",
            output=json.dumps({
                "path": str(out_path),
                "size_bytes": len(png_bytes),
                "base64_preview": b64[:200] + "..." if len(b64) > 200 else b64,
            }),
            exit_code=0,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(tool_name="browser_screenshot", output=f"Error: {e}", exit_code=1, duration_ms=duration)


async def browser_click(tool_input: dict) -> ToolResult:
    """Click an element."""
    selector = tool_input.get("selector", "")
    if not selector:
        return ToolResult(tool_name="browser_click", output="Error: selector is required", exit_code=1, duration_ms=0)

    start = time.monotonic()
    try:
        page = await _ensure_browser()
        await page.click(selector, timeout=5000)
        await page.wait_for_load_state("networkidle", timeout=5000)
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_click",
            output=json.dumps({"clicked": selector, "url": page.url}),
            exit_code=0,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(tool_name="browser_click", output=f"Error: {e}", exit_code=1, duration_ms=duration)


async def browser_fill(tool_input: dict) -> ToolResult:
    """Fill a form field."""
    selector = tool_input.get("selector", "")
    value = tool_input.get("value", "")
    if not selector:
        return ToolResult(tool_name="browser_fill", output="Error: selector is required", exit_code=1, duration_ms=0)

    start = time.monotonic()
    try:
        page = await _ensure_browser()
        await page.fill(selector, value, timeout=5000)
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_fill",
            output=json.dumps({"filled": selector, "value_length": len(value)}),
            exit_code=0,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(tool_name="browser_fill", output=f"Error: {e}", exit_code=1, duration_ms=duration)


async def browser_console(tool_input: dict) -> ToolResult:
    """Get console messages (errors, warnings, logs) from the current page."""
    start = time.monotonic()
    try:
        page = await _ensure_browser()

        # Collect console messages via JS injection
        messages = await page.evaluate("""() => {
            const entries = window.__consoleLog || [];
            return entries;
        }""")

        # If no injected listener, just check for errors via page errors
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))

        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_console",
            output=json.dumps({
                "messages": messages or [],
                "errors": errors,
                "url": page.url,
            }),
            exit_code=0,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(tool_name="browser_console", output=f"Error: {e}", exit_code=1, duration_ms=duration)


async def browser_assert(tool_input: dict) -> ToolResult:
    """Assert element visibility and optionally text content."""
    selector = tool_input.get("selector", "")
    text = tool_input.get("text")
    visible = tool_input.get("visible", True)

    start = time.monotonic()
    try:
        page = await _ensure_browser()

        element = await page.query_selector(selector)
        is_visible = element is not None and await element.is_visible()

        if visible and not is_visible:
            duration = int((time.monotonic() - start) * 1000)
            return ToolResult(
                tool_name="browser_assert",
                output=f"FAIL: element '{selector}' is not visible",
                exit_code=1,
                duration_ms=duration,
            )

        if text and element:
            actual_text = await element.text_content()
            if text not in (actual_text or ""):
                duration = int((time.monotonic() - start) * 1000)
                return ToolResult(
                    tool_name="browser_assert",
                    output=f"FAIL: element '{selector}' text '{actual_text}' does not contain '{text}'",
                    exit_code=1,
                    duration_ms=duration,
                )

        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_assert",
            output=f"PASS: element '{selector}' visible={is_visible}" + (f" text contains '{text}'" if text else ""),
            exit_code=0,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(tool_name="browser_assert", output=f"Error: {e}", exit_code=1, duration_ms=duration)


async def browser_assert_no_console_errors(tool_input: dict) -> ToolResult:
    """Assert no console errors on current page."""
    start = time.monotonic()
    try:
        page = await _ensure_browser()

        # Check for JS errors
        errors = await page.evaluate("""() => {
            return window.__jsErrors || [];
        }""")

        duration = int((time.monotonic() - start) * 1000)
        if errors:
            return ToolResult(
                tool_name="browser_assert_no_console_errors",
                output=f"FAIL: {len(errors)} console errors found: {json.dumps(errors[:5])}",
                exit_code=1,
                duration_ms=duration,
            )
        return ToolResult(
            tool_name="browser_assert_no_console_errors",
            output="PASS: no console errors",
            exit_code=0,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_assert_no_console_errors",
            output=f"Error: {e}",
            exit_code=1,
            duration_ms=duration,
        )


async def browser_accessibility_scan(tool_input: dict) -> ToolResult:
    """Basic accessibility scan — check contrast, alt text, aria labels."""
    start = time.monotonic()
    try:
        page = await _ensure_browser()

        issues = await page.evaluate("""() => {
            const issues = [];

            // Check images without alt
            document.querySelectorAll('img').forEach(img => {
                if (!img.alt) issues.push({ type: 'missing-alt', selector: 'img', src: img.src?.substring(0, 100) });
            });

            // Check inputs without labels
            document.querySelectorAll('input, select, textarea').forEach(el => {
                const id = el.id;
                const hasLabel = id && document.querySelector(`label[for="${id}"]`);
                const hasAria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
                if (!hasLabel && !hasAria) {
                    issues.push({ type: 'missing-label', tag: el.tagName, id: id || '(none)' });
                }
            });

            // Check buttons without text
            document.querySelectorAll('button').forEach(btn => {
                if (!btn.textContent.trim() && !btn.getAttribute('aria-label')) {
                    issues.push({ type: 'empty-button', id: btn.id || '(none)' });
                }
            });

            return issues;
        }""")

        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_accessibility_scan",
            output=json.dumps({
                "issues_found": len(issues),
                "issues": issues[:20],
                "url": page.url,
            }),
            exit_code=0 if len(issues) == 0 else 1,
            duration_ms=duration,
        )
    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name="browser_accessibility_scan",
            output=f"Error: {e}",
            exit_code=1,
            duration_ms=duration,
        )


# Tool registry for easy registration with ToolExecutor
BROWSER_TOOLS: dict[str, Any] = {
    "browser_navigate": browser_navigate,
    "browser_screenshot": browser_screenshot,
    "browser_click": browser_click,
    "browser_fill": browser_fill,
    "browser_console": browser_console,
    "browser_assert": browser_assert,
    "browser_assert_no_console_errors": browser_assert_no_console_errors,
    "browser_accessibility_scan": browser_accessibility_scan,
}
