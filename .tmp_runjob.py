"""Drive one real signup through the CCE stack."""
import asyncio, os, sys
sys.path.insert(0, "/home/deploy/cce/server")
os.chdir("/home/deploy/cce")
os.environ.setdefault("CCE_DB_PATH", "data/cce.sqlite3")
os.environ.setdefault("CCE_TUNNEL_SECRETS", "data/cce.secrets")

from core import signup
from core.browser_pool import BrowserPool
from core.profile_manager import ProfileManager
from core.proxy_inspector import ProxyInspector
from db.database import Database

async def main():
    db = Database("data/cce.sqlite3"); await db.connect()
    prof = ProfileManager(db)
    pool = BrowserPool(db, prof, ProxyInspector(db))
    try:
        async with pool.acquire_context("px-test-1") as ctx:
            page = await ctx.new_page()
            out = await signup.run_signup(
                page, email="zzpxprobe@gmail.com", password="Probe#2026abc",
                gmail_app_password="", timeout=120,
            )
            print("STATUS:", out.status)
            print("MSG:", out.message[:300])
            print("URL:", out.url[:120])
            print("TITLE:", (await page.title())[:80])
            body = (await page.inner_text("body"))[:300].replace("\n", " ")
            print("BODY:", body[:240])
    finally:
        await db.close()

asyncio.run(main())
