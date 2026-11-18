"""Same probe, but through a local sing-box port (no proxy auth in the browser)."""
import asyncio

CASES = [
    (10001, "45.38.107.97", 6014),
    (10002, "64.137.96.74", 6641),
    (10003, "198.23.243.226", 6361),
]

async def one(port, host, pport):
    from camoufox.async_api import AsyncCamoufox
    tag = f"{host}:{pport}"
    try:
        async with AsyncCamoufox(headless=True, proxy={"server": f"http://127.0.0.1:{port}"},
                                 humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            resp = await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            title = await page.title()
            body = (await page.inner_text("body"))[:300].replace("\n", " ")
            print(f"{tag:22} status={resp.status if resp else '?'} title={title[:44]!r}")
            print(f"   {body[:200]}")
    except Exception as e:
        print(f"{tag:22} FAIL {type(e).__name__}: {str(e)[:100]}")

async def main():
    for c in CASES:
        await one(*c)

asyncio.run(main())
