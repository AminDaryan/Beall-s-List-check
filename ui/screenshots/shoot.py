"""Retake the screenshots in this folder from a live app.

Not part of the check. It exists so that the images in the READMEs can be
regenerated after a UI change instead of drifting out of date. Playwright is a
development-only dependency and is deliberately absent from requirements.txt::

    pip install playwright && playwright install chromium

Start the app on a port of its own, with a neutral model name so that a real
deployment name is not baked into a committed image, then point this at it::

    AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini \\
        python -m streamlit run ui/app.py --server.port 8505 --server.headless true
    python ui/screenshots/shoot.py http://localhost:8505 ui/screenshots

Captured at deviceScaleFactor=2, so a 1500-wide viewport yields a 3000-wide
file. Captured light: the app follows the browser's colour scheme, and a
screenshot has to pick one. Only the free checks are exercised -- the thorough
check costs tokens and is never run here.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8503"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "ui/screenshots").resolve()
RUN_TIMEOUT = 180_000          # a check with Crossref lookups is not fast
TOAST = 6_000                  # long enough for the "Check complete" toast to go


def settle(page, ms=1200):
    """Let Streamlit finish its rerun and the fonts/emoji paint."""
    page.wait_for_timeout(ms)


def frame(page, locator, margin=90):
    """Scroll so *locator* sits *margin* pixels below the top of the viewport."""
    page.mouse.move(1000, 500)                 # wheel over the main area, not the sidebar
    locator.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    box = locator.bounding_box()
    if box:
        page.mouse.wheel(0, box["y"] - margin)
    settle(page, 800)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Tall enough for the whole form -- the check table down to the input
        # data-quality panel -- which is what ui-main.png is captioned as showing.
        page = browser.new_page(viewport={"width": 1500, "height": 1750},
                                device_scale_factor=2, color_scheme="light")
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector("text=Choose a check", timeout=60_000)
        settle(page)

        # ── 1. The whole form: the check table, papers loaded, quality panel ──
        # The example corpus is not one of the input sources; it is a button that
        # fills the paste box, which is what the app then reads.
        page.get_by_role("button", name="Load the example corpus").click()
        page.wait_for_selector("text=Loaded 8 paper(s)", timeout=60_000)
        settle(page)
        page.screenshot(path=str(OUT / "ui-main.png"))
        print("wrote ui-main.png")

        # ── 2. A finished fast check, framed on the workbook preview ─────────
        page.set_viewport_size({"width": 1500, "height": 1150})
        # Step 4's summary is the one place that names the check a press of Run
        # would actually start, so it is the guard against running the wrong one.
        # It is scoped to that container: "Fast check" also appears in Step 1.
        page.wait_for_selector(".st-key-runsummary:has-text('Fast check')", timeout=60_000)
        page.get_by_role("button", name="▶ Run check").click()
        page.wait_for_selector("text=Your Excel is ready below", timeout=RUN_TIMEOUT)
        settle(page, TOAST)
        frame(page, page.get_by_text("Result", exact=True))
        page.screenshot(path=str(OUT / "ui-report.png"))
        print("wrote ui-report.png")

        # ── 3. Batch CSV, with the colour-coded statuses ─────────────────────
        page.set_viewport_size({"width": 1500, "height": 1000})
        page.mouse.move(1000, 500)
        page.mouse.wheel(0, -6000)
        settle(page)
        # exact=True matters: the default is substring matching, which would also
        # match "✓ Selected" and shift the index onto the thorough row - i.e. run
        # the LLM check by accident. The wait below is the guard against that:
        # never press Run until the summary confirms which check is selected.
        page.get_by_role("button", name="Select", exact=True).last.click()
        page.wait_for_selector(".st-key-runsummary:has-text('Batch CSV')", timeout=60_000)
        settle(page)
        page.get_by_role("button", name="▶ Run check").click()
        page.wait_for_selector("text=record(s) classified", timeout=RUN_TIMEOUT)
        settle(page, TOAST)
        frame(page, page.get_by_text("Result", exact=True))
        page.screenshot(path=str(OUT / "ui-batch.png"))
        print("wrote ui-batch.png")

        browser.close()


if __name__ == "__main__":
    main()
