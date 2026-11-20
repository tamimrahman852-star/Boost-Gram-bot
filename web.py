from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

fastapi_app = FastAPI(title="BoostGram")

import api as _api  # noqa: E402  (Mini App REST API)
_api.install(fastapi_app)


@fastapi_app.get("/", response_class=HTMLResponse)
async def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return """<!DOCTYPE html><html><body style="background:#0f172a;color:#fff;text-align:center;padding:100px;font-family:Arial"><h1>Boost Gram 🚀</h1><p>Bot is running</p></body></html>"""


@fastapi_app.get("/sitemap.xml", response_class=HTMLResponse)
async def sitemap():
    try:
        with open("sitemap.xml", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'


@fastapi_app.get("/google8c808883d07580e1.html", response_class=HTMLResponse)
async def gverify():
    try:
        with open("google8c808883d07580e1.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "google-site-verification: google8c808883d07580e1.html"


@fastapi_app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------------------------
# Channel-ad click tracker + legal pages
# ---------------------------------------------------------------------------------------------
import hashlib
import os
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from config import BOT_USERNAME
from database import AdCampaign, AdPlacement, AsyncSessionLocal, redis_client

_BOT_UA = ("bot", "crawler", "spider", "preview", "facebookexternalhit", "slurp")


@fastapi_app.get("/r/{pid}")
async def ad_click(pid: str, request: Request):
    """Every ad button points here: count one click per visitor per day, then redirect to the advertiser."""
    fallback = f"https://t.me/{BOT_USERNAME}"
    if len(pid) != 36:
        return RedirectResponse(fallback, status_code=302)
    async with AsyncSessionLocal() as s:
        row = (await s.execute(select(AdPlacement.status, AdCampaign.button_url).join(
            AdCampaign, AdCampaign.id == AdPlacement.campaign_id).where(AdPlacement.id == pid))).first()
        if not row:
            return RedirectResponse(fallback, status_code=302)
        status, url = row
        ua = request.headers.get("user-agent", "")
        ip = (request.headers.get("x-forwarded-for", "") or (request.client.host if request.client else "")).split(",")[0].strip()
        if status == "posted" and not any(x in ua.lower() for x in _BOT_UA):
            who = hashlib.sha1(f"{ip}|{ua}".encode()).hexdigest()[:16]
            if await redis_client.set(f"clk:{pid}:{who}", "1", nx=True, ex=86400):
                await s.execute(update(AdPlacement).where(AdPlacement.id == pid).values(clicks=AdPlacement.clicks + 1))
                await s.commit()
    return RedirectResponse(url, status_code=302)


def _page(name: str) -> HTMLResponse:
    path = os.path.join("webapp", f"{name}.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse(f"<h1>{name}</h1>", status_code=404)


@fastapi_app.get("/terms", response_class=HTMLResponse)
async def terms_page():
    return _page("terms")


@fastapi_app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return _page("privacy")
