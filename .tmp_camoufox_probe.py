"""Probe whether Camoufox (the CCE browser) clears claude.ai's Cloudflare wall."""
import asyncio, json, sys

PX = [("45.38.107.97",6014),("64.137.96.74",6641),("198.23.243.226",6361)]
U, P = "nvvctlro", "eabxdl4ee7nj"

async def one(host, port):
    from camoufox.async_api import AsyncCamoufox
    url = f"socks5://{U}:{P}@{host}:{port}"
    tag = f"{host}:{port}"
    try:
        async with AsyncCamoufox(headless=True, proxy={"server": f"socks5://{host}:{port}", "username": U, "password": P},
                                 geoip=False, humanize=True, i_know_what_im_doing=True) as ctx:
            page = await ctx.new_page()
            resp = await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            title = await page.title()
            body = (await page.inner_text("body"))[:400].replace("\n", " ")
            ip = ""
            try:
                ip = (await page.evaluate("fetch('https://who.1ms.dog/api/me').then(r=>r.json()).then(d=>d.ip)")) or ""
            except Exception:
                pass
            print(f"{tag:22} status={resp.status if resp else '?'} title={title[:40]!r} exit={ip}")
            print(f"   body: {body[:220]}")
    except Exception as e:
        print(f"{tag:22} FAIL {type(e).__name__}: {str(e)[:120]}")

async def main():
    for h, p in PX:
        await one(h, p)

asyncio.run(main())
