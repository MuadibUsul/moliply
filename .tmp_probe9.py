"""Virtual display + each proxy. This is the mode CCE should use."""
import asyncio

CASES = [(10101, "GB-London"), (10102, "ES-Madrid"), (10103, "US-LAX"), (10104, "US-EWR"),
         (10105, "PT-Lisbon"), (10106, "US-LAX-2"), (10107, "US-LAX-3"), (10108, "DE-FRA"),
         (10109, "US-SJC"), (10110, "US-SLC")]

async def one(port, label):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless="virtual", proxy={"server": f"http://127.0.0.1:{port}"},
                                 humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            r = await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            ok = False
            for i in range(20):
                await page.wait_for_timeout(2500)
                if "moment" not in (await page.title()).lower():
                    ok = True
                    break
            title = await page.title()
            print(f"{label:12} port={port} {'PASS' if ok and 'claude' in title.lower() else 'FAIL'} waited={(i+1)*2.5:.0f}s title={title[:34]!r}")
    except Exception as e:
        print(f"{label:12} port={port} ERROR {type(e).__name__}: {str(e)[:70]}")

async def main():
    for c in CASES:
        await one(*c)

asyncio.run(main())
