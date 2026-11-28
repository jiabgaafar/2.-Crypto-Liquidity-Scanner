import os
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd
import mplfinance as mpf
import io

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
RAW_CHAT_IDS = os.getenv("CHAT_ID", "").strip()
CHAT_IDS = [cid.strip() for cid in RAW_CHAT_IDS.split(",") if cid.strip()]
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 WhaleSniperBot/5.0"})

TG_BASE      = "https://api.telegram.org/bot"
OKX_BASE     = "https://www.okx.com"
KUCOIN_BASE  = "https://api.kucoin.com"
BYBIT_BASE   = "https://api.bybit.com"
BINANCE_BASE = "https://data-api.binance.vision/api/v3"

# ⚙️ الإعدادات الشرسة (تم فتح الفلاتر)
ANOMALY_THRESHOLD = 2.5     # حساسية عالية جداً (أي حركة 0.8% من حجم اليوم سيرصدها)
COOLDOWN_MINUTES = 15       # يذكرك بالعملة كل 15 دقيقة
CACHE_FILE = "alert_cache.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w") as f: json.dump(cache, f)

def format_price(price):
    if price >= 1: return f"{price:,.3f}"
    elif price >= 0.01: return f"{price:.5f}"
    elif price >= 0.0001: return f"{price:.6f}"
    return f"{price:.8f}"

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_IDS: return False
    url = f"{TG_BASE}{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_IDS:
        try: session.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=15)
        except Exception: pass

def send_telegram_photo(photo_buf, caption, chat_id):
    photo_buf.seek(0)
    url = f"{TG_BASE}{BOT_TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', photo_buf, 'image/png')}
    try:
        if session.post(url, data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}, files=files, timeout=15).json().get("ok"):
            print(f"✅ تم إرسال الصورة إلى: {chat_id}")
    except Exception: pass

def generate_chart_image(exchange, symbol):
    try:
        df = None
        if exchange == "Binance":
            res = session.get(f"{BINANCE_BASE}/klines", params={"symbol": symbol, "interval": "5m", "limit": 50}, timeout=10).json()
            if not isinstance(res, list): return None
            df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
            df['time'] = pd.to_datetime(df['time'].astype(int), unit='ms')
            
        elif exchange == "KuCoin":
            res = session.get(f"{KUCOIN_BASE}/api/v1/market/candles", params={"type": "5min", "symbol": symbol}, timeout=10).json()
            data = res.get("data", [])
            if not isinstance(data, list): return None
            df = pd.DataFrame(data, columns=['time', 'open', 'close', 'high', 'low', 'volume', 'turnover'])
            df = df.iloc[::-1]
            df['time'] = pd.to_datetime(df['time'].astype(int), unit='s')
            
        elif exchange == "OKX":
            res = session.get(f"{OKX_BASE}/api/v5/market/candles", params={"instId": symbol, "bar": "5m", "limit": 50}, timeout=10).json()
            data = res.get("data", [])
            if not isinstance(data, list): return None
            df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'volCcy', 'volCcyQuote', 'confirm'])
            df = df.iloc[::-1] 
            df['volume'] = df['vol']
            df['time'] = pd.to_datetime(df['time'].astype(int), unit='ms')
            
        elif exchange == "Bybit":
            res = session.get(f"{BYBIT_BASE}/v5/market/kline", params={"category": "linear", "symbol": symbol, "interval": "5", "limit": 50}, timeout=10).json()
            data = res.get("result", {}).get("list", [])
            if not isinstance(data, list): return None
            df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            df = df.iloc[::-1]
            df['time'] = pd.to_datetime(df['time'].astype(int), unit='ms')

        if df is None or df.empty: return None
        df.set_index('time', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = df[col].astype(float)

        mc = mpf.make_marketcolors(up='#00ff88', down='#ff3333', edge='inherit', wick='inherit', volume='in')
        s  = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True, facecolor='#131722', edgecolor='#131722')
        buf = io.BytesIO()
        mpf.plot(df, type='candle', style=s, volume=True, savefig=dict(fname=buf, format='png', dpi=100, bbox_inches='tight'))
        buf.seek(0)
        return buf
    except Exception: return None

# ================== Binance ==================
def check_binance_single(symbol, vol_24h):
    try:
        # limit=1 يعطينا الشمعة الحالية المفتوحة (اللحظية)
        res = session.get(f"{BINANCE_BASE}/klines", params={"symbol": symbol, "interval": "5m", "limit": 1}, timeout=8).json()
        if not isinstance(res, list) or len(res) == 0: return None
        current = res[0] 
        open_p, close_p, quote_vol_5m = float(current[1]), float(current[4]), float(current[7])
        price_change = ((close_p - open_p) / open_p) * 100
        anomaly_percent = (quote_vol_5m / vol_24h) * 100

        if anomaly_percent >= ANOMALY_THRESHOLD:
            icon = "🟢" if price_change >= 0 else "🔴"
            activity = "Unusual buying activity" if price_change >= 0 else "Unusual selling activity"
            return {"exchange": "Binance", "symbol": symbol, "text": f"{icon} <b>Binance - USDT Market</b>\n#{symbol} - <b>{activity}</b>\n💵 {quote_vol_5m/1000:,.1f}K USDT (Live) (<b>{anomaly_percent:.1f}%</b>)\n📊 P: {format_price(close_p)} (<b>{price_change:+.2f}%</b>)\n📈 24H Vol: {vol_24h/1000000:,.2f}M USDT"}
    except Exception: pass
    return None

def scan_all_binance():
    alerts = []
    try:
        res = session.get(f"{BINANCE_BASE}/ticker/24hr", timeout=12).json()
        if not isinstance(res, list): return alerts
        # 🔴 تم خفض الفلتر إلى 100 ألف دولار لاصطياد العملات الصغيرة
        tickers = {item['symbol']: float(item['quoteVolume']) for item in res if item['symbol'].endswith('USDT') and float(item['quoteVolume']) > 100000}
    except Exception: return alerts
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_binance_single, sym, vol): sym for sym, vol in tickers.items()}
        for future in as_completed(futures):
            if future.result(): alerts.append(future.result())
    return alerts

# ================== KuCoin ==================
def check_kucoin_single(symbol, vol_24h):
    try:
        res = session.get(f"{KUCOIN_BASE}/api/v1/market/candles", params={"type": "5min", "symbol": symbol}, timeout=8).json()
        candles = res.get("data", [])
        if not isinstance(candles, list) or len(candles) == 0: return None
        current = candles[0]
        open_p, close_p, quote_vol_5m = float(current[1]), float(current[2]), float(current[6])
        price_change = ((close_p - open_p) / open_p) * 100
        anomaly_percent = (quote_vol_5m / vol_24h) * 100

        if anomaly_percent >= ANOMALY_THRESHOLD:
            icon = "🟢" if price_change >= 0 else "🔴"
            activity = "Unusual buying activity" if price_change >= 0 else "Unusual selling activity"
            return {"exchange": "KuCoin", "symbol": symbol, "text": f"{icon} <b>KuCoin - USDT Market</b>\n#{symbol.replace('-','')} - <b>{activity}</b>\n💵 {quote_vol_5m/1000:,.1f}K USDT (Live) (<b>{anomaly_percent:.1f}%</b>)\n📊 P: {format_price(close_p)} (<b>{price_change:+.2f}%</b>)\n📈 24H Vol: {vol_24h/1000000:,.2f}M USDT"}
    except Exception: pass
    return None

def scan_all_kucoin():
    alerts = []
    try:
        res = session.get(f"{KUCOIN_BASE}/api/v1/market/allTickers", timeout=12).json()
        # 🔴 فلتر 50 ألف دولار
        tickers = {item['symbol']: float(item['volValue']) for item in res.get("data", {}).get("ticker", []) if item['symbol'].endswith('-USDT') and float(item['volValue']) > 50000}
    except Exception: return alerts
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_kucoin_single, sym, vol): sym for sym, vol in tickers.items()}
        for future in as_completed(futures):
            if future.result(): alerts.append(future.result())
    return alerts

# ================== OKX ==================
def check_okx_single(symbol, vol_24h):
    try:
        res = session.get(f"{OKX_BASE}/api/v5/market/candles", params={"instId": symbol, "bar": "5m", "limit": 1}, timeout=8).json()
        candles = res.get("data", [])
        if not isinstance(candles, list) or len(candles) == 0: return None
        current = candles[0]
        open_p, close_p, quote_vol_5m = float(current[1]), float(current[4]), float(current[7])
        price_change = ((close_p - open_p) / open_p) * 100
        anomaly_percent = (quote_vol_5m / vol_24h) * 100
        ccy = symbol.split("-")[0]

        if anomaly_percent >= ANOMALY_THRESHOLD:
            icon = "🟢" if price_change >= 0 else "🔴"
            activity = "Unusual buying activity" if price_change >= 0 else "Unusual selling activity"
            return {"exchange": "OKX", "symbol": symbol, "text": f"{icon} <b>OKX - USDT Market</b>\n#{ccy} - <b>{activity}</b>\n💵 {quote_vol_5m/1000:,.1f}K USDT (Live) (<b>{anomaly_percent:.1f}%</b>)\n📊 P: {format_price(close_p)} (<b>{price_change:+.2f}%</b>)\n📈 24H Vol: {vol_24h/1000000:,.2f}M USDT"}
    except Exception: pass
    return None

def scan_all_okx():
    alerts = []
    try:
        res = session.get(f"{OKX_BASE}/api/v5/market/tickers", params={"instType": "SWAP"}, timeout=12).json()
        # 🔴 فلتر 100 ألف دولار
        tickers = {item.get("instId"): float(item.get("volCcy24h", 0)) * float(item.get("last", 0)) for item in res.get("data", []) if item.get("instId").endswith("-USDT-SWAP") and float(item.get("volCcy24h", 0)) * float(item.get("last", 0)) > 100000}
    except Exception: return alerts
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_okx_single, sym, vol): sym for sym, vol in tickers.items()}
        for future in as_completed(futures):
            if future.result(): alerts.append(future.result())
    return alerts

# ================== Bybit ==================
def check_bybit_single(symbol, vol_24h):
    try:
        response = session.get(f"{BYBIT_BASE}/v5/market/kline", params={"category": "linear", "symbol": symbol, "interval": "5", "limit": 1}, timeout=8)
        if response.status_code != 200: return None
        data = response.json().get("result", {}).get("list", [])
        if len(data) == 0: return None
        current = data[0] 
        open_p, close_p, quote_vol_5m = float(current[1]), float(current[4]), float(current[6])
        price_change = ((close_p - open_p) / open_p) * 100
        anomaly_percent = (quote_vol_5m / vol_24h) * 100

        if anomaly_percent >= ANOMALY_THRESHOLD:
            icon = "🟢" if price_change >= 0 else "🔴"
            activity = "Unusual buying activity" if price_change >= 0 else "Unusual selling activity"
            return {"exchange": "Bybit", "symbol": symbol, "text": f"{icon} <b>Bybit - USDT Market</b>\n#{symbol} - <b>{activity}</b>\n💵 {quote_vol_5m/1000:,.1f}K USDT (Live) (<b>{anomaly_percent:.1f}%</b>)\n📊 P: {format_price(close_p)} (<b>{price_change:+.2f}%</b>)\n📈 24H Vol: {vol_24h/1000000:,.2f}M USDT"}
    except Exception: pass
    return None

def scan_all_bybit():
    alerts = []
    try:
        response = session.get(f"{BYBIT_BASE}/v5/market/tickers", params={"category": "linear"}, timeout=12)
        if response.status_code != 200: return alerts
        # 🔴 فلتر 100 ألف دولار
        tickers = {item.get("symbol"): float(item.get("turnover24h", 0)) for item in response.json().get("result", {}).get("list", []) if item.get("symbol").endswith("USDT") and float(item.get("turnover24h", 0)) > 100000}
    except Exception: return alerts
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_bybit_single, sym, vol): sym for sym, vol in tickers.items()}
        for future in as_completed(futures):
            if future.result(): alerts.append(future.result())
    return alerts

# ================== Main ==================
def main():
    print("🐋 بدء صائد الحيتان الشامل (نسخة الشوارع - اللحظية)...")
    cache = load_cache()
    now = datetime.utcnow()
    
    all_alerts = scan_all_okx() + scan_all_kucoin() + scan_all_bybit() + scan_all_binance()
    
    filtered_alerts = []
    if all_alerts:
        for alert in all_alerts:
            key = f"{alert['exchange']}_{alert['symbol']}"
            if key in cache:
                last_time = datetime.fromisoformat(cache[key])
                if now - last_time < timedelta(minutes=COOLDOWN_MINUTES):
                    print(f"⏭️ تم تجاهل {key}.")
                    continue 
            
            filtered_alerts.append(alert)
            cache[key] = now.isoformat() 

    if filtered_alerts:
        print(f"🚨 تم رصد {len(filtered_alerts)} شذوذ جديد (لحظي). جاري إرسال الصور...")
        for alert in filtered_alerts:
            print(f"📈 توليد شارت لـ {alert['exchange']} | {alert['symbol']}...")
            chart_image = generate_chart_image(alert['exchange'], alert['symbol'])
            for chat_id in CHAT_IDS:
                if chart_image: send_telegram_photo(chart_image, alert['text'], chat_id)
                else: send_telegram(alert['text'])
        
        save_cache(cache)
    else:
        print("ℹ️ السوق هادئ أو التنبيهات مكررة.")
        if EVENT_NAME == "workflow_dispatch":
            send_telegram("✅ <b>صائد الحيتان يعمل!</b>\nالسوق هادئ ولا توجد تحركات سيولة جديدة حالياً.")

if __name__ == "__main__":
    main()
