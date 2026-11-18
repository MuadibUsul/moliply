"""Measure claude.ai load success over repeated virtual-mode visits."""
import asyncio

PORTS = list(range(10201, 10211))

async def one(port):
    from camoufox.async_api import AsyncCamoufox
    ok = 0
    for _ in range(2):
        try:
            async with AsyncCamoufox(headless="virtual", proxy={"server": f"http://127.0.0.1:{port}"},
                                     humanize=True, i_know_what_im_doing=True) as ctx:
                page = await ctx.new_page()
                for rnd in range(3):
                    await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=45000)
                    for _i in range(10):
                        await page.wait_for_timeout(2500)
                        t = (await page.title()).lower()
                        if "moment" not in t and "can" not in t:
                            break
                    t = await page.title()
                    if "sign in" in t.lower():
                        ok += 1
                        break
        except Exception:
            pass
    return port, ok

async def main():
    for p, ok in await asyncio.gather(*(one(x) for x in PORTS)):
        print(f"port {p}: {ok}/2")

asyncio.run(main())
