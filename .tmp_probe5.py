"""Compare geoip-matched fingerprint vs default, on the US proxies only."""
import asyncio

CASES = [(10103, "198.23.243.226", "US-LAX"), (10104, "38.154.185.97", "US-EWR")]

async def one(port, label, geo):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless=True, proxy={"server": f"http://127.0.0.1:{port}"},
                                 geoip=True, humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            cleared = False
            for i in range(20):
                await page.wait_for_timeout(3000)
                if "moment" not in (await page.title()).lower():
                    cleared = True
                    break
            body = (await page.inner_text("body"))[:160].replace("\n", " ")
            print(f"{label:16} cleared={cleared} waited={(i+1)*3}s url={page.url[:55]}")
            print(f"   {body[:120]}")
    except Exception as e:
        print(f"{label:16} FAIL {type(e).__name__}: {str(e)[:90]}")

async def main():
    for c in CASES:
        await one(*c)

asyncio.run(main())
