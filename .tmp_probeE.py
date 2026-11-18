"""Dump what the challenge page actually leaves behind (console + DOM + cookies)."""
import asyncio

async def main():
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless="virtual", proxy={"server": "http://127.0.0.1:10103"},
                             humanize=True, i_know_what_im_doing=True) as ctx:
        page = await ctx.new_page()
        logs = []
        page.on("console", lambda m: logs.append(f"{m.type}:{m.text}"[:110]))
        page.on("requestfailed", lambda r: logs.append(f"REQFAIL {r.url[:60]} {r.failure}"))
        await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(25000)
        print("title:", await page.title())
        print("html_len:", len(await page.content()))
        cookies = await page.context.cookies()
        print("cookies:", [(c["name"], c["domain"]) for c in cookies][:6])
        dom = await page.evaluate("""() => ({
            cf: !!document.querySelector('#challenge-form, #cf-challenge-running, .cf-browser-verification'),
            frames: [...document.querySelectorAll('iframe')].map(f => f.src.slice(0,70)),
            scripts: document.scripts.length,
            tx: (document.body.innerText||'').slice(0,150)
        })""")
        print("dom:", dom)
        print("logs:", logs[:8])

asyncio.run(main())
