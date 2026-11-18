"""One signup with full page instrumentation at the failure point."""
import asyncio, os, sys
sys.path.insert(0, "/home/deploy/cce/server")
os.chdir("/home/deploy/cce")
os.environ.setdefault("CCE_DB_PATH", "data/cce.sqlite3")

from core import signup
from core.browser_pool import BrowserPool
from core.profile_manager import ProfileManager
from core.proxy_inspector import ProxyInspector
from db.database import Database

async def main():
    db = Database("data/cce.sqlite3"); await db.connect()
    prof = ProfileManager(db); pool = BrowserPool(db, prof, ProxyInspector(db))
    await db.set_status("px-test-1", "IDLE")
    try:
        async with pool.acquire_context("px-test-1") as ctx:
            page = await ctx.new_page()
            out = await signup.run_signup(page, email="pipe-test-1@gmail.com",
                                          password="TestPass#2026", gmail_app_password="fakeapppass12345", timeout=120)
            print("STATUS", out.status, "|", out.message[:100])
            print("URL", page.url[:100])
            print("TITLE", (await page.title())[:70])
            html = await page.content()
            print("EMAIL_INPUT", html.count('type="email"'), "BODY", (await page.inner_text("body"))[:200].replace("\n"," "))
    finally:
        await db.close()

asyncio.run(main())
