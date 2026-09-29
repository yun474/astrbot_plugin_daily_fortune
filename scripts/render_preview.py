"""Render the actual Python template with local assets, without AstrBot/network."""
import argparse
import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("daily_fortune_preview")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package

from daily_fortune_preview.fortune import draw
from daily_fortune_preview.renderer import CardRenderer, build_html, build_wife_html
from daily_fortune_preview.service import default_avatar, normalize_image


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default="")
    parser.add_argument("--avatar", type=Path)
    parser.add_argument("--wife-image", type=Path, help="渲染今日老婆合成图，传入本地角色图片")
    parser.add_argument("--wife-name", default="芙宁娜")
    parser.add_argument("--wife-work", default="原神")
    parser.add_argument("--output", type=Path, default=ROOT / ".test-output/python-preview.png")
    args = parser.parse_args()
    view = draw("github:yun474", "2026-09-29")
    view.update(
        quote="如果你一个人把什么都做完了，那我做什么？",
        quote_source="《元气少女缘结神》", quote_credit="一言", background_credit="妖狐图库",
    )
    avatar = normalize_image(args.avatar.read_bytes(), (256, 256)) if args.avatar else default_avatar()
    if args.wife_image:
        picture = normalize_image(args.wife_image.read_bytes())
        html = build_wife_html({"name": args.wife_name, "work": args.wife_work}, picture, avatar)
    else:
        background = normalize_image((ROOT / "assets/default_background.jpg").read_bytes())
        html = build_html(view, background, avatar)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    renderer = CardRenderer(args.browser)
    try:
        await renderer.render(html, args.output)
        print(args.output.resolve())
    finally:
        await renderer.close()


if __name__ == "__main__":
    asyncio.run(main())
