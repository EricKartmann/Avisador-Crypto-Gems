import argparse
import asyncio
import math
import statistics
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yaml
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table
from tenacity import retry, stop_after_attempt, wait_fixed


console = Console()


class PairWatch(BaseModel):
    network: str
    tokenAddress: Optional[str] = None
    pairAddress: Optional[str] = None
    note: Optional[str] = None


class Thresholds(BaseModel):
    min_liquidity_usd: float = 200000
    min_txns_m5: int = 20
    volume_spike_multiplier: float = 4.0
    volume_zscore: float = 3.0
    buy_sell_tx_ratio: float = 1.8
    buy_share_m5: float = 0.65
    dprice_1m_pct: float = 2.5
    dprice_5m_pct: float = 6.0
    whale_trade_usd: float = 20000


class TelegramCfg(BaseModel):
    enabled: bool = False
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None


class DiscoveryCfg(BaseModel):
    enabled: bool = False
    networks: List[str] = ["bsc"]
    top_n: int = 50
    min_liquidity_usd: float = 100000
    min_volume_m5_usd: float = 10000
    min_txns_m5: int = 20
    exclude_symbols: List[str] = ["USDT", "USDC", "BUSD", "DAI", "FDUSD", "TUSD"]
    refresh_seconds: int = 120


class AppConfig(BaseModel):
    watchlist: List[PairWatch]
    poll_seconds: int = 20
    cooldown_minutes: int = 10
    thresholds: Thresholds = Thresholds()
    telegram: TelegramCfg = TelegramCfg()
    discovery: DiscoveryCfg = DiscoveryCfg()
    supabase: Dict[str, Any] = {"enabled": False}
    logging: Dict[str, Any] = {"level": "INFO"}


@dataclass
class RollingStats:
    prices: List[float]
    volumes_1m: List[float]

    def median_vol_30m(self) -> float:
        if not self.volumes_1m:
            return 0.0
        window = self.volumes_1m[-30:]
        return statistics.median(window) if window else 0.0

    def zscore_last_vol(self) -> float:
        if len(self.volumes_1m) < 10:
            return 0.0
        window = self.volumes_1m[-30:] or self.volumes_1m
        mu = statistics.mean(window)
        sd = statistics.pstdev(window) or 1e-9
        return (self.volumes_1m[-1] - mu) / sd


class CooldownStore:
    def __init__(self, minutes: int) -> None:
        self.seconds = minutes * 60
        self._last: Dict[str, float] = {}

    def should_alert(self, key: str) -> bool:
        now = time.time()
        last = self._last.get(key, 0)
        if now - last >= self.seconds:
            self._last[key] = now
            return True
        return False


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return AppConfig(**data)


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
async def fetch_json(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    resp = await client.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def build_dexscreener_url(w: PairWatch) -> str:
    if w.pairAddress:
        return f"https://api.dexscreener.com/latest/dex/pairs/{w.network}/{w.pairAddress}"
    if w.tokenAddress:
        return f"https://api.dexscreener.com/latest/dex/tokens/{w.tokenAddress}"
    raise ValueError("watch entry must have pairAddress or tokenAddress")


def compute_score(pair: Dict[str, Any], stats: RollingStats, t: Thresholds) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    liq = (pair.get("liquidity") or {}).get("usd") or 0
    if liq < t.min_liquidity_usd:
        return 0, [f"Liquidity {liq:.0f} < min {t.min_liquidity_usd:.0f}"]

    price = float(pair.get("priceUsd") or 0)
    change = pair.get("priceChange") or {}
    d1m = float(change.get("m5", 0)) / 5.0  # we don't have 1m; approximate
    d5m = float(change.get("m5", 0) or 0)

    tx = pair.get("txns", {})
    buys_m5 = int((tx.get("m5") or {}).get("buys") or 0)
    sells_m5 = int((tx.get("m5") or {}).get("sells") or 0)
    vol_m5 = float((pair.get("volume") or {}).get("m5") or 0)

    # Synthetic 1m volume: assume evenly spread within los últimos 5m
    v1m = vol_m5 / 5.0
    stats.volumes_1m.append(v1m)
    stats.prices.append(price)

    # Volume spike
    med30 = stats.median_vol_30m()
    if med30 > 0 and v1m > t.volume_spike_multiplier * med30:
        score += 3
        reasons.append(f"V1m {v1m:.0f} > {t.volume_spike_multiplier}×med30 {med30:.0f}")

    z = stats.zscore_last_vol()
    if z > t.volume_zscore:
        score += 2
        reasons.append(f"Vol z-score {z:.1f} > {t.volume_zscore}")

    # Imbalance and momentum
    if (buys_m5 + sells_m5) >= t.min_txns_m5:
        ratio = buys_m5 / max(sells_m5, 1)
        # Dexscreener no siempre expone buyVol/sellVol en la API; usamos proporción de transacciones
        buy_share_pct = buys_m5 / max(buys_m5 + sells_m5, 1)
        if ratio > t.buy_sell_tx_ratio:
            score += 2
            reasons.append(f"Tx ratio {ratio:.2f} > {t.buy_sell_tx_ratio}")
        if buy_share_pct > t.buy_share_m5:
            score += 1
            reasons.append(f"Buy share {buy_share_pct:.2f} > {t.buy_share_m5}")

    if d1m > t.dprice_1m_pct:
        score += 1
        reasons.append(f"dP1m {d1m:.1f}% > {t.dprice_1m_pct}%")
    if d5m > t.dprice_5m_pct:
        score += 2
        reasons.append(f"dP5m {d5m:.1f}% > {t.dprice_5m_pct}%")

    return score, reasons


async def send_telegram(cfg: TelegramCfg, text: str) -> None:
    if not cfg.enabled or not cfg.bot_token or not cfg.chat_id:
        console.log(text)
        return
    import asyncio as _asyncio
    from telegram import Bot

    bot = Bot(token=cfg.bot_token)
    await bot.send_message(chat_id=cfg.chat_id, text=text, disable_web_page_preview=True)


async def discover_telegram_chat_id(bot_token: str) -> Optional[str]:
    if not bot_token:
        return None
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            results = data.get("result", [])
            if not results:
                return None
            # toma el último update
            upd = results[-1]
            chat = ((upd.get("message") or {}).get("chat")) or ((upd.get("channel_post") or {}).get("chat"))
            if chat and chat.get("id") is not None:
                return str(chat["id"])
        except Exception as exc:
            console.log({"telegram": "discover_chat_id_failed", "error": str(exc)})
    return None

async def send_supabase(cfg: AppConfig, payload: Dict[str, Any]) -> None:
    sb = cfg.supabase or {}
    if not sb.get("enabled"):
        return
    url = sb.get("url")
    key = sb.get("anon_key")
    table = sb.get("table_alerts", "alerts")
    if not url or not key:
        return
    endpoint = f"{url}/rest/v1/{table}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(endpoint, headers=headers, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            console.log({"supabase": "failed", "error": str(exc)})

async def discover_pairs(client: httpx.AsyncClient, cfg: AppConfig) -> List[PairWatch]:
    if not cfg.discovery.enabled:
        return []
    url = "https://api.dexscreener.com/latest/dex/trending"
    try:
        data = await fetch_json(client, url)
    except Exception as exc:
        console.log({"discovery": "failed", "error": str(exc)})
        return []
    pairs = data.get("pairs") or data.get("trendingPairs") or []
    found: List[PairWatch] = []
    for p in pairs:
        chain = p.get("chainId") or p.get("chain") or ""
        if cfg.discovery.networks and chain not in cfg.discovery.networks:
            continue
        liq = (p.get("liquidity") or {}).get("usd") or 0
        vol_m5 = float((p.get("volume") or {}).get("m5") or 0)
        tx_m5 = p.get("txns", {}).get("m5") or {}
        tx_count = int((tx_m5.get("buys") or 0) + (tx_m5.get("sells") or 0))
        base = (p.get("baseToken") or {}).get("symbol") or ""
        quote = (p.get("quoteToken") or {}).get("symbol") or ""
        if base in cfg.discovery.exclude_symbols or quote in cfg.discovery.exclude_symbols:
            continue
        if liq < cfg.discovery.min_liquidity_usd:
            continue
        if vol_m5 < cfg.discovery.min_volume_m5_usd:
            continue
        if tx_count < cfg.discovery.min_txns_m5:
            continue
        pair_addr = p.get("pairAddress")
        if not pair_addr:
            continue
        found.append(PairWatch(network=chain, pairAddress=pair_addr, note=base))
        if len(found) >= cfg.discovery.top_n:
            break
    return found


async def evaluate_watch(client: httpx.AsyncClient, w: PairWatch, cfg: AppConfig, stats_store: Dict[str, RollingStats], cooldown: CooldownStore) -> None:
    url = build_dexscreener_url(w)
    data = await fetch_json(client, url)
    pairs = data.get("pairs") or []
    if not pairs:
        return
    # Selecciona el par con mayor liquidez
    pair = max(pairs, key=lambda p: ((p.get("liquidity") or {}).get("usd") or 0))
    key = pair.get("pairAddress") or pair.get("url") or w.tokenAddress or "unknown"
    stats = stats_store.setdefault(key, RollingStats(prices=[], volumes_1m=[]))
    score, reasons = compute_score(pair, stats, cfg.thresholds)
    if score >= 5 and cooldown.should_alert(key):
        name = pair.get("baseToken", {}).get("symbol") + "/" + pair.get("quoteToken", {}).get("symbol")
        link = pair.get("url") or f"https://dexscreener.com/{w.network}/{pair.get('pairAddress')}"
        msg = (
            f"ALERTA ({'PRIO' if score>=7 else 'ACT'}) {name}\n"
            f"Precio: ${float(pair.get('priceUsd') or 0):.6f} | Liquidez: ${(pair.get('liquidity') or {}).get('usd') or 0:.0f}\n"
            f"Score: {score} | Razones: {', '.join(reasons)}\n"
            f"DexScreener: {link}"
        )
        await send_telegram(cfg.telegram, msg)
        await send_supabase(cfg, {
            "pair_address": pair.get("pairAddress"),
            "network": w.network,
            "symbol": name,
            "price_usd": float(pair.get("priceUsd") or 0),
            "liquidity_usd": (pair.get("liquidity") or {}).get("usd") or 0,
            "score": score,
            "reasons": ", ".join(reasons),
            "link": link,
            "ts": int(time.time()),
        })
    else:
        console.log({"pair": key, "score": score, "reasons": reasons[:3]})


async def main_loop(cfg: AppConfig, once: bool) -> None:
    cooldown = CooldownStore(cfg.cooldown_minutes)
    stats_store: Dict[str, RollingStats] = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AlertBot/1.0)"}
    async with httpx.AsyncClient(headers=headers) as client:
        dynamic_watch: List[PairWatch] = []
        last_discovery = 0.0
        while True:
            now = time.time()
            if cfg.discovery.enabled and (now - last_discovery >= cfg.discovery.refresh_seconds):
                dynamic_watch = await discover_pairs(client, cfg)
                last_discovery = now
            watches = cfg.watchlist + dynamic_watch
            tasks = [evaluate_watch(client, w, cfg, stats_store, cooldown) for w in watches]
            await asyncio.gather(*tasks)
            if once:
                break
            await asyncio.sleep(cfg.poll_seconds)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Avisador Oportunidades Cripto (MVP)")
    p.add_argument("--config", default="config.yaml", help="Ruta a config.yaml")
    p.add_argument("--once", action="store_true", help="Ejecuta una sola iteración")
    p.add_argument("--test-supabase", action="store_true", help="Inserta una fila de prueba en Supabase")
    p.add_argument("--test-telegram", nargs="?", const="Prueba de Telegram", help="Envía un mensaje de prueba por Telegram")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        console.print("[yellow]No se encontró config.yaml, usando config.example.yaml[/yellow]")
        cfg = load_config("config.example.yaml")
    if args.__dict__.get("--test-supabase") or getattr(args, "test_supabase", False):
        # Inserción de prueba
        payload = {
            "pair_address": "test",
            "network": "testnet",
            "symbol": "TEST/USDT",
            "price_usd": 1.0,
            "liquidity_usd": 0,
            "score": 1,
            "reasons": "cli-test",
            "link": "https://example.com",
            "ts": int(time.time()),
        }
        asyncio.run(send_supabase(cfg, payload))
        console.print("[green]Supabase prueba enviada (si está habilitado).[/green]")
        return
    if getattr(args, "test_telegram", None) is not None:
        text = getattr(args, "test_telegram") or "Prueba de Telegram"
        tg = cfg.telegram
        if tg.enabled and tg.bot_token and not tg.chat_id:
            # intenta autodescubrir el chat id si el usuario ya habló con el bot
            discovered = asyncio.run(discover_telegram_chat_id(tg.bot_token))
            if discovered:
                tg.chat_id = discovered
                console.print(f"[yellow]Descubierto chat_id: {discovered}. (Añádelo a tu config.yaml)[/yellow]")
        asyncio.run(send_telegram(tg, text))
        console.print("[green]Telegram prueba enviada (si está habilitado).[/green]")
        return
    asyncio.run(main_loop(cfg, args.once))


if __name__ == "__main__":
    main()


