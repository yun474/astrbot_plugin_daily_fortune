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


def build_wife_html(item: dict, picture: bytes, avatar: bytes) -> str:
    css = (ASSETS / "wife.css").read_text("utf-8")
    source = image_uri(picture)
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>今日老婆</title><style>{css}</style><article id="card">
<img class="art" src="{source}" alt="角色插画">
<section class="info"><img class="wash" src="{source}" alt="">
<img class="avatar" src="{image_uri(avatar)}" alt="用户头像">
<div class="copy"><div class="label">今日老婆</div>
<h1>{escape(item['name'])}</h1><p class="work">作品 · {escape(item['work'])}</p></div>
</section></article></html>'''


class CardRenderer:
    IDLE_TIMEOUT = 120  # 浏览器空闲这么多秒就关掉，常驻的 Chromium 很吃内存

    def __init__(self, executable: str = ""):
        self.executable = executable
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._active = 0
        self._idle_task = None

    async def render(self, html: str, target: Path):
        self._active += 1
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None
        try:
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
        finally:
            self._active -= 1
            if not self._active and self._browser:
                self._idle_task = asyncio.create_task(self._close_when_idle())

    async def _close_when_idle(self):
        await asyncio.sleep(self.IDLE_TIMEOUT)
        async with self._lock:
            if self._active:
                return
            # 已经开始关了就别被新请求打断，新请求会等锁再重新启动浏览器
            self._idle_task = None
            await self._shutdown()

    async def close(self):
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None
        async with self._lock:
            await self._shutdown()

    async def _shutdown(self):
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = None
        try:
            if browser:
                await browser.close()
        finally:
            if playwright:
                await playwright.stop()
