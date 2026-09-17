from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

fastapi_app = FastAPI(title="BoostGram")


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
