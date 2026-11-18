"""Run the real pipeline for one imported row, reporting each stage transition."""
import asyncio, os, sys
sys.path.insert(0, "/home/deploy/cce/server")
os.chdir("/home/deploy/cce")
os.environ.setdefault("CCE_DB_PATH", "data/cce.sqlite3")

from core import pipeline
from core.browser_pool import BrowserPool
from core.profile_manager import ProfileManager
from core.proxy_inspector import ProxyInspector
from db.database import Database

ROW = "pipe-test-1@gmail.com,fakeapppass12345,45.38.107.97,socks5://127.0.0.1:10101,pro,TestPass#2026"

async def main():
    db = Database("data/cce.sqlite3"); await db.connect()
    prof = ProfileManager(db)
    pool = BrowserPool(db, prof, ProxyInspector(db))
    try:
        rows, errs = pipeline.parse_import(ROW)
        print("parse:", len(rows), errs)
        job = await pipeline.create_job(db, prof, rows[0])
        print("job:", job["profile_id"], job["stage"])
        out = await pipeline.run_job(db, pool, job["profile_id"])
        print("FINAL stage:", out.get("stage"), "note:", (out.get("human_note") or "")[:160])
        print("error:", (out.get("last_error") or "")[:160])
    finally:
        await db.close()

asyncio.run(main())
