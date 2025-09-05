from typing import Any, Dict, List

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, PackageLoader, select_autoescape, FileSystemLoader
import yaml


app = FastAPI(title="Avisador Cripto - Web")


def load_config() -> Dict[str, Any]:
    with open("config.yaml", "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


async def fetch_alerts(cfg: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
    sb = cfg.get("supabase", {})
    if not sb.get("enabled"):
        return []
    url = f"{sb['url']}/rest/v1/{sb.get('table_alerts','alerts')}?select=*&order=ts.desc&limit={limit}"
    headers = {"apikey": sb["anon_key"], "Authorization": f"Bearer {sb['anon_key']}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()


def parse_reasons(reasons_text: str) -> List[Dict[str, str]]:
    if not reasons_text:
        return []
    parts = [p.strip() for p in reasons_text.split(",") if p.strip()]
    parsed: List[Dict[str, str]] = []
    for p in parts:
        label = p
        level = "tag-low"
        if p.startswith("V1m"):
            label = "Volumen 1m alto"
            level = "tag-med"
        elif p.startswith("Vol z-score"):
            label = "Volumen anómalo"
            level = "tag-med"
        elif p.startswith("Tx ratio"):
            label = "Compras/Ventas alto"
            level = "tag-med"
        elif p.startswith("Buy share"):
            label = "Dominio de compras"
            level = "tag-low"
        elif p.startswith("dP1m"):
            label = "+Precio 1m"
            level = "tag-low"
        elif p.startswith("dP5m"):
            label = "+Precio 5m"
            level = "tag-high"
        parsed.append({"label": label, "detail": p, "level": level})
    return parsed


env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape())


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = load_config()
    alerts = await fetch_alerts(cfg, limit=100)
    for a in alerts:
        a["reason_tags"] = parse_reasons(a.get("reasons") or "")
    watchlist = cfg.get("watchlist", [])
    template = env.get_template("index.html")
    return template.render(alerts=alerts, watchlist=watchlist)


@app.get("/api/alerts")
async def api_alerts(limit: int = 100):
    cfg = load_config()
    alerts = await fetch_alerts(cfg, limit=limit)
    return JSONResponse(alerts)


@app.get("/api/watchlist")
async def api_watchlist():
    cfg = load_config()
    return JSONResponse(cfg.get("watchlist", []))


