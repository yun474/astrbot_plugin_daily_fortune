import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from daily_fortune_test.renderer import CardRenderer


def fake_renderer():
    page = NS(
        set_content=AsyncMock(),
        evaluate=AsyncMock(),
        locator=MagicMock(return_value=NS(screenshot=AsyncMock())),
        close=AsyncMock(),
    )
    browser = NS(new_page=AsyncMock(return_value=page), is_connected=lambda: True, close=AsyncMock())
    playwright = NS(stop=AsyncMock())
    renderer = CardRenderer()
    renderer.IDLE_TIMEOUT = 0.05
    renderer._browser, renderer._playwright = browser, playwright
    return renderer, browser, playwright


@pytest.mark.asyncio
async def test_idle_browser_exits_and_reuse_postpones_it(tmp_path):
    renderer, browser, playwright = fake_renderer()
    await renderer.render("<html/>", tmp_path / "a.png")
    await asyncio.sleep(0.03)
    await renderer.render("<html/>", tmp_path / "b.png")
    await asyncio.sleep(0.03)
    browser.close.assert_not_called()

    await asyncio.sleep(0.1)
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert renderer._browser is None and renderer._playwright is None


@pytest.mark.asyncio
async def test_close_stops_playwright_even_if_browser_close_fails():
    renderer, browser, playwright = fake_renderer()
    browser.close.side_effect = RuntimeError("browser disconnected")
    with pytest.raises(RuntimeError):
        await renderer.close()
    playwright.stop.assert_awaited_once()
    assert renderer._browser is None and renderer._playwright is None
