"""Does a cf_clearance ever appear, and does a second visit then pass?"""
import asyncio

async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless="virtual", proxy={"server": "http://127.0.0.1:10103"},
                             humanize=True, i_know_what_im_doing=True) as ctx:
        page = await ctx.new_page()
        for n in (1, 2):
            await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(30000)
            ck = {c["name"]: c["domain"] for c in await page.context.cookies()}
            print(f"visit{n}: title={(await page.title())[:38]!r} clearance={'cf_clearance' in ck} cf_bm={'__cf_bm' in ck}")
            if n == 1:
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(8000)
                print(f"  after reload: {(await page.title())[:38]!r}")

asyncio.run(main())
