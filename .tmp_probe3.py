import asyncio

CASES = [(10101, "45.38.107.97"), (10102, "64.137.96.74"), (10103, "198.23.243.226"),
         (10104, "38.154.185.97"), (10105, "84.247.60.125")]

async def one(port, label):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless=True, proxy={"server": f"http://127.0.0.1:{port}"},
                                 humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            resp = await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(7000)
            title = await page.title()
            body = (await page.inner_text("body"))[:260].replace("\n", " ")
            blocked = "just a moment" in body.lower() or "verify you are human" in body.lower()
            print(f"{label:18} port={port} status={resp.status if resp else '?'} wall={'YES' if blocked else 'no'} title={title[:40]!r}")
            print(f"   {body[:170]}")
    except Exception as e:
        print(f"{label:18} port={port} FAIL {type(e).__name__}: {str(e)[:90]}")

async def main():
    for c in CASES:
        await one(*c)

asyncio.run(main())
