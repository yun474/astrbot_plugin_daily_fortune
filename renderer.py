import asyncio
import base64
from datetime import date
from html import escape
from pathlib import Path

from playwright.async_api import async_playwright

ASSETS = Path(__file__).parent / "assets"


def image_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def build_html(view: dict, background: bytes, avatar: bytes) -> str:
    def items(key):
        return "".join(
            f'<div class="event"><b>{escape(title)}</b><p>{escape(desc)}</p></div>'
            for title, desc in view[key]
        )

    weekday = "星期" + "一二三四五六日"[date.fromisoformat(view["date"]).weekday()]
    css = (ASSETS / "card.css").read_text("utf-8")
    # No remote resources execute or load in the screenshot page.
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>今日运势</title><style>{css}</style><article id="card">
<img class="hero" src="{image_uri(background)}" alt="二次元插画"><div class="art-space"></div>
<main class="body"><header class="top"><div class="identity">
<img class="avatar" src="{image_uri(avatar)}" alt="用户头像"><h1>今日运势</h1></div>
<div class="date">{escape(view['date'].replace('-', '.'))}<small>{weekday}</small></div></header>
<div class="fortune"><div class="seal">{escape(view['fortune'])}</div><div>
<div class="fortune-label">你 的 今 日 运 势</div><div class="tip">{escape(view['tip'])}</div></div></div>
<div class="advice"><section class="panel"><div class="panel-head"><span class="symbol">宜</span></div>{items('good')}</section>
<section class="panel bad"><div class="panel-head"><span class="symbol">忌</span></div>{items('bad')}</section></div>
<section class="quote"><div class="quote-title">— 今日一言 —</div>
<div class="quote-text">「 {escape(view['quote'])} 」</div><div class="source">—— {escape(view['quote_source'])}</div></section>
<footer class="footer"><span>语录 · {escape(view['quote_credit'])}　/　插画 · {escape(view['background_credit'])}</span>
<span>仅供娱乐</span></footer></main></article></html>'''


class CardRenderer:
    def __init__(self, executable: str = ""):
        self.executable = executable
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def render(self, html: str, target: Path):
        async with self._lock:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            if self._browser is None or not self._browser.is_connected():
                options = {"executable_path": self.executable} if self.executable else {}
                self._browser = await self._playwright.chromium.launch(**options)
            browser = self._browser
        page = await browser.new_page(viewport={"width": 800, "height": 1500}, device_scale_factor=1)
        try:
            await page.set_content(html, wait_until="load")
            await page.evaluate("async () => {await document.fonts.ready; await Promise.all([...document.images].map(i => i.decode()));}")
            await page.locator("#card").screenshot(path=str(target), timeout=15000)
        finally:
            await page.close()

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = self._playwright = None
