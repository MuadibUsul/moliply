"""Retry-on-challenge strategy across all 10, 2 rounds max."""
import asyncio

PORTS = [(10101,"GB"),(10102,"ES"),(10103,"US-LAX"),(10104,"US-EWR"),(10105,"PT"),
         (10106,"US-LAX2"),(10107,"US-LAX3"),(10108,"DE"),(10109,"US-SJC"),(10110,"US-SLC")]

async def attempt(port):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless="virtual", proxy={"server": f"http://127.0.0.1:{port}"},
                                 humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            for rnd in range(3):
                await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
                for i in range(12):
                    await page.wait_for_timeout(2500)
                    if "moment" not in (await page.title()).lower():
                        break
                if "claude" in (await page.title()).lower():
                    return rnd + 1
            return 0
    except Exception:
        return -1

async def main():
    for port, label in PORTS:
        r = await attempt(port)
        print(f"{label:9} port={port} {'PASS round '+str(r) if r>0 else 'FAIL'}")

asyncio.run(main())
