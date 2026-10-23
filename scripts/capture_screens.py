"""Capture screenshots of every screen into docs/img (needs the dev requirements and Chromium).

Usage: python -m scripts.capture_screens
Runs the app in offline demo mode on a free port, so the images need no API key and are repeatable.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
DESKTOP = {"width": 1360, "height": 900}
PHONE = {"width": 390, "height": 844}
WAIT = 60_000


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start(tmp: Path, port: int) -> subprocess.Popen[bytes]:
    env = {**os.environ, "RFP_FAKE_LLM": "1", "RFP_DB_PATH": str(tmp / "shots.db"),
           "RFP_DB_MODE": "session", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false",
         "--server.address", "127.0.0.1"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2):
                return proc
        except OSError:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("Streamlit did not start")


def shot(page: Page, name: str) -> None:
    page.wait_for_timeout(900)  # let animations and charts settle
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"saved docs/img/{name}.png")


def button(page: Page, name: str):  # type: ignore[no-untyped-def]
    return page.get_by_role("button", name=name, exact=True)


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    with tempfile.TemporaryDirectory() as tmp:
        server = _start(Path(tmp), port)
        url = f"http://127.0.0.1:{port}"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport=DESKTOP)
                page.set_default_timeout(WAIT)
                page.goto(url)
                expect(button(page, "Start demo session")).to_be_visible()
                shot(page, "01-connect")

                button(page, "Start demo session").click()
                expect(page.get_by_role("heading", name="Evaluation criteria")).to_be_visible()
                shot(page, "02-criteria")

                button(page, "Continue to suppliers").click()
                expect(page.get_by_role("heading", name="Supplier proposals")).to_be_visible()
                button(page, "Load demo data").click()
                expect(page.get_by_text("apex_systems.pdf")).to_be_visible()
                shot(page, "03-suppliers")

                button(page, "Evaluate suppliers").click()
                expect(page.get_by_role("tab", name="Leaderboard")).to_be_visible()
                expect(page.locator("[class*='st-key-lb_row_']")).to_have_count(4)
                shot(page, "04-leaderboard")

                page.locator("[class*='st-key-open_']").first.get_by_role("button").click()
                dialog = page.get_by_role("dialog")
                expect(dialog.get_by_text("Evidence and reasoning")).to_be_visible()
                dialog.get_by_text("Technical Capability - ").first.click()
                page.wait_for_timeout(600)
                shot(page, "05-scorecard")
                page.keyboard.press("Escape")

                page.get_by_role("tab", name="Compare").click()
                expect(page.locator(".js-plotly-plot").first).to_be_visible()
                shot(page, "06-compare")

                page.get_by_role("tab", name="Run details").click()
                expect(page.get_by_text("RFP_RUN_ID")).to_be_visible()
                shot(page, "07-run-details")

                page.get_by_test_id("stExpandSidebarButton").click()
                page.get_by_text("Backup and restore").click()
                shot(page, "08-backup-sidebar")

                phone = browser.new_page(viewport=PHONE)
                phone.set_default_timeout(WAIT)
                phone.goto(url)
                expect(button(phone, "Start demo session")).to_be_visible()
                shot(phone, "09-mobile-connect")
                button(phone, "Start demo session").click()
                expect(phone.get_by_role("heading", name="Evaluation criteria")).to_be_visible()
                shot(phone, "10-mobile-criteria")
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    run()
