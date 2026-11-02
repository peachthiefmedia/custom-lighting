"""Render the editable SVG brand source to HA's standard PNG sizes."""

from pathlib import Path

from playwright.sync_api import sync_playwright


if __name__ == "__main__":
    brand = Path(__file__).resolve().parents[1] / "custom_components/custom_lighting/brand"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(brand.joinpath("icon.svg").read_text())
            page.add_style_tag(content="body { margin: 0; } svg { width: 100vw; height: 100vh; display: block; }")
            page.screenshot(path=str(brand / name), omit_background=True)
            page.close()
        browser.close()
