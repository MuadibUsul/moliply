"""Give Cloudflare up to 75s to auto-solve, then report the landing page."""
import asyncio

async def one(port, label):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless=True, proxy={"server": f"http://127.0.0.1:{port}"},
                                 humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            cleared = False
            for i in range(25):
                await page.wait_for_timeout(3000)
                t = (await page.title()).lower()
                if "just a moment" not in t and "moment" not in t:
                    cleared = True
                    break
            title = await page.title()
            url = page.url
            body = (await page.inner_text("body"))[:200].replace("\n", " ")
            print(f"{label:18} cleared={cleared} waited={(i+1)*3}s url={url[:60]}")
            print(f"   title={title[:50]!r} body={body[:130]}")
    except Exception as e:
        print(f"{label:18} FAIL {type(e).__name__}: {str(e)[:80]}")

asyncio.run(one(10101, "45.38.107.97"))
