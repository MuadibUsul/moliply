"""Is the wall claude-specific or CF-wide for this IP?"""
import asyncio

TARGETS = [("https://chatgpt.com/", "chatgpt"), ("https://www.cloudflare.com/cdn-cgi/trace", "cf-trace")]

async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless="virtual", proxy={"server": "http://127.0.0.1:10103"},
                             humanize=True, i_know_what_im_doing=True) as ctx:
        page = await ctx.new_page()
        for url, label in TARGETS:
            try:
                r = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(8000)
                t = await page.title()
                print(f"{label:9} status={r.status if r else '?'} title={t[:50]!r}")
            except Exception as e:
                print(f"{label:9} ERR {type(e).__name__}")

asyncio.run(main())
