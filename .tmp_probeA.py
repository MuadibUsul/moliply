"""Control: same browser, no proxy. Isolates 'browser broken' from 'IP bad'."""
import asyncio

async def one(kwargs, label):
    from camoufox.async_api import AsyncCamoufox
    try:
        async with AsyncCamoufox(headless="virtual", humanize=True, i_know_what_im_doing=True, **kwargs) as ctx:
            page = await ctx.new_page()
            await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
            for i in range(20):
                await page.wait_for_timeout(3000)
                if "moment" not in (await page.title()).lower():
                    break
            print(f"{label:22} title={(await page.title())[:46]!r} url={page.url[:50]}")
            print(f"   {(await page.inner_text('body'))[:130].replace(chr(10),' ')}")
    except Exception as e:
        print(f"{label:22} FAIL {type(e).__name__}: {str(e)[:80]}")

async def main():
    await one({}, "direct-no-proxy")

asyncio.run(main())
