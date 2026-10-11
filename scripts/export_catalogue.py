"""Use only Kariz's authenticated read route; never import app.main/start workers."""
import asyncio
import json
import os
import sys
from pathlib import Path
import httpx
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend" / "main-backend-opencode"

async def main():
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    from app.api.admin.auth import router as auth_router
    from app.api.admin.catalog import list_products
    from app.core.config import get_settings
    from app.core.db import engine
    isolated = FastAPI()
    isolated.include_router(auth_router)
    isolated.add_api_route("/admin/products", list_products, methods=["GET"])
    settings = get_settings()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated, raise_app_exceptions=False), base_url="http://catalogue-export.local") as client:
        login = await client.post("/admin/auth/login", json={"username": settings.admin_username, "password": settings.admin_password})
        print(json.dumps({"login_http": login.status_code}))
        if login.status_code != 200:
            await engine.dispose()
            return
        token = login.json()["access_token"]
        response = await client.get("/admin/products", headers={"Authorization": "Bearer " + token})
        print(json.dumps({"catalogue_http": response.status_code, "items": len(response.json()) if response.status_code == 200 else None}))
        if response.status_code == 200:
            output = ROOT / ".local"
            output.mkdir(exist_ok=True)
            (output / "kariz-products.json").write_text(json.dumps(response.json(), ensure_ascii=False, indent=2), encoding="utf-8")
    await engine.dispose()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
