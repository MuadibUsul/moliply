"""Two attempts on one proxy, longer patience, report whether session survives."""
import asyncio

async def attempt(port, n):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless="virtual", proxy={"server": f"http://127.0.0.1:{port}"},
                                 humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            for i in range(30):
                await page.wait_for_timeout(3000)
                if "moment" not in (await page.title()).lower():
                    break
            t = await page.title()
            print(f"  attempt{n}: waited={(i+1)*3}s title={t[:44]!r}")
            return "claude" in t.lower()
    except Exception as e:
        print(f"  attempt{n}: ERROR {type(e).__name__}: {str(e)[:60]}")
        return False

async def main():
    for n in (1, 2):
        if await attempt(10103, n):
            print("  => US-LAX works")
            return
    print("  => US-LAX failed both")

asyncio.run(main())
