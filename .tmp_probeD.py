import asyncio

async def attempt(port):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless="virtual", proxy={"server": f"http://127.0.0.1:{port}"},
                                 humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            for i in range(16):
                await page.wait_for_timeout(2500)
                if "moment" not in (await page.title()).lower():
                    return "claude" in (await page.title()).lower()
            return False
    except Exception:
        return False

async def main():
    for port, label in [(10103, "US-LAX"), (10101, "GB"), (10109, "US-SJC")]:
        res = []
        for _ in range(3):
            res.append("P" if await attempt(port) else "F")
        print(f"{label:8} port={port} 3 tries -> {''.join(res)}")

asyncio.run(main())
