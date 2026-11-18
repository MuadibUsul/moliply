import asyncio
CASES = [(10101,"GB"),(10102,"ES"),(10104,"US-EWR"),(10105,"PT"),(10106,"US-LAX2"),
         (10107,"US-LAX3"),(10108,"DE"),(10109,"US-SJC"),(10110,"US-SLC")]

async def one(port, label):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless="virtual", proxy={"server": f"http://127.0.0.1:{port}"},
                                 humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            for i in range(20):
                await page.wait_for_timeout(2500)
                if "moment" not in (await page.title()).lower():
                    break
            t = await page.title()
            print(f"{label:9} port={port} {'PASS' if 'claude' in t.lower() else 'FAIL'} {(i+1)*2.5:.0f}s {t[:32]!r}")
    except Exception as e:
        print(f"{label:9} port={port} ERR {type(e).__name__}")

async def main():
    for c in CASES: await one(*c)

asyncio.run(main())
