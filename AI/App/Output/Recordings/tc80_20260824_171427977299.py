import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://sit-app-dgto.psw.gov.pk/")
    page.get_by_role("button", name="Sign In to Portal").click()
    page.get_by_role("textbox", name="Username *").click()
    page.get_by_role("textbox", name="Username *").fill("admin@dgto.gov.pk")
    page.locator("div:nth-child(2) > .flex.w-full.items-center.rounded-\\[10px\\]").click()
    page.get_by_role("textbox", name="Password *").fill("Admin@12345")
    page.get_by_role("button", name="Sign In").click()
    page.get_by_role("button", name="Show password").click()
    page.get_by_role("textbox", name="Username *").click()
    page.get_by_role("textbox", name="Username *").click()
    page.get_by_role("textbox", name="Username *").fill("sg@devto.example.pk")
    page.get_by_role("textbox", name="Password *").click()
    page.get_by_role("textbox", name="Password *").fill("DevSg@12345")
    page.get_by_role("button", name="Sign In").click()
    page.close()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
