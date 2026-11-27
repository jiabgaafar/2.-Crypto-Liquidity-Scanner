import os
from datetime import datetime
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
session.headers.update({"User-Agent": "Mozilla/5.0 CryptoScanner/2.0"})

TG_BASE      = "https://api.telegram.org/bot"
OKX_BASE     = "https://www.okx.com"
KUCOIN_BASE  = "https://api.kucoin.com"
BYBIT_BASE   = "https://api.bybit.com"
BINANCE_BASE = "https://data-api.binance.vision/api/v3"

# --- دوال مساعدة ---
def format_price(price):
    if price >= 1: return f"{price:,.3f}"
    elif price >= 0.01: return f"{price:.5f}"
    elif price >= 0.0001: return f"{price:.6f}"
    return f"{price:.8f}"

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_IDS: return False
    url = f"{TG_BASE}{BOT_TOKEN}/sendMessage"
    success = False
    for chat_id in CHAT_IDS:
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        try:
            response = session.post(url, json=payload, timeout=15)
            if response.json().get("ok"):
                print(f"✅ تم إرسال النص إلى: {chat_id}")
                success = True
            else:
                print(f"❌ فشل إرسال النص إلى {chat_id}")
        except Exception: pass
    return success

# --- توليد الشارت لجميع المنصات ---
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
            df = df.iloc[::-1] # الأقدم أولاً
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
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        mc = mpf.make_marketcolors(up='#00ff88', down='#ff3333', edge='inherit', wick='inherit', volume='in')
        s  = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True, facecolor='#131722', edgecolor='#131722')

        buf = io.BytesIO()
        mpf.plot(df, type='candle', style=s, volume=True, savefig=dict(fname=buf, format='png', dpi=100, bbox_inches='tight'))
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"فشل توليد الشارت لـ {exchange} {symbol}: {e}")
        return None

def send_telegram_photo(photo_buf, caption, chat_id):
    photo_buf.seek(0) # 🔴 إعادة المؤشر لبداية الصورة
    url = f"{TG_BASE}{BOT_TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', photo_buf, 'image/png')}
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    try:
        response = session.post(url, data=data, files=files, timeout=15)
        if response.json().get("ok"):
            print(f"✅ تم إرسال الصورة إلى: {chat_id}")
            return True
        else:
            print(f"❌ فشل إرسال الصورة إلى {chat_id}: {response.json().get('description')}")
    except Exception: pass
    return False

# ================== OKX ==================
def check_okx_oi_single(inst_id):
    ccy = inst_id.split("-")[0]
    try:
        oi_res = session.get(f"{OKX_BASE}/api/v5/rubik/stat/contracts/open-interest-volume", params={"ccy": ccy, "period": "5m"}, timeout=8).json()
        data_list = oi_res.get("data", [])
        if len(data_list) < 2: return None

        new_oi = float(data_list[0][1])
        old_oi = float(data_list[1][1])
        if old_oi <= 0 or new_oi < 300000: return None

        oi_change = ((new_oi - old_oi) / old_oi) * 100
        if oi_change >= 0.8:
            return {"exchange": "OKX", "symbol": inst_id, "text": f"🟢 <b>تدفق سيولة عقود (OI) — OKX</b>\n🪙 العملة: <b>#{ccy}USDT</b>\n📈 ارتفاع OI (5د): <b>+{oi_change:.2f}%</b>\n💰 العقود المفتوحة: <b>${new_oi:,.0f}</b>"}
        elif oi_change <= -0.8:
            return {"exchange": "OKX", "symbol": inst_id, "text": f"🔴 <b>خروج سيولة عقود (OI) — OKX</b>\n🪙 العملة: <b>#{ccy}USDT</b>\n📉 انخفاض OI (5د): <b>{oi_change:.2f}%</b>\n💰 المتبقي في العقود: <b>${new_oi:,.0f}</b>"}
    except Exception: pass
    return None

def scan_all_okx():
    alerts = []
    try:
        res = session.get(f"{OKX_BASE}/api/v5/market/tickers", params={"instType": "SWAP"}, timeout=12).json()
        swaps = res.get("data", [])
    except Exception: return alerts

    coins = []
    for item in swaps:
        inst_id = item.get("instId", "")
        if inst_id.endswith("-USDT-SWAP"):
            try:
                vol_24h = float(item.get("volCcy24h", 0)) * float(item.get("last", 0))
                if vol_24h >= 500000: coins.append(inst_id)
            except ValueError: continue

    coins = list(dict.fromkeys(coins))
    print(f"🔍 فحص {len(coins)} عملة في OKX...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(check_okx_oi_single, c): c for c in coins}
        for future in as_completed(futures):
            if future.result(): alerts.append(future.result())
    return alerts

# ================== KuCoin ==================
def check_kucoin_single(symbol):
    try:
        res = session.get(f"{KUCOIN_BASE}/api/v1/market/candles", params={"type": "5min", "symbol": symbol}, timeout=8).json()
        candles = res.get("data", [])
        if not isinstance(candles, list) or len(candles) < 7: return None

        current, previous = candles[0], candles[1:7]
        open_p, close_p, vol_5m = float(current[1]), float(current[2]), float(current[5])
        avg_vol = sum(float(c[5]) for c in previous) / len(previous)
        if open_p <= 0 or avg_vol <= 0 or vol_5m < 10000: return None

        price_change = ((close_p - open_p) / open_p) * 100
        vol_mult = vol_5m / avg_vol
        clean_name = symbol.replace("-", "")

        if vol_mult >= 1.8:
            if price_change >= 0.30:
                return {"exchange": "KuCoin", "symbol": symbol, "text": f"🟢 <b>دخول سيولة شرائية — KuCoin</b>\n🪙 العملة: <b>#{clean_name}</b>\n💵 السعر: <code>${format_price(close_p)}</code> (<b>+{price_change:.2f}%</b>)\n🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"}
            elif price_change <= -0.30:
                return {"exchange": "KuCoin", "symbol": symbol, "text": f"🔴 <b>خروج سيولة بيعية — KuCoin</b>\n🪙 العملة: <b>#{clean_name}</b>\n💵 السعر: <code>${format_price(close_p)}</code> (<b>{price_change:.2f}%</b>)\n💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"}
    except Exception: pass
    return None

def scan_all_kucoin():
    alerts = []
    try:
        res = session.get(f"{KUCOIN_BASE}/api/v1/market/allTickers", timeout=12).json()
        tickers = res.get("data", {}).get("ticker", [])
    except Exception: return alerts

    symbols = []
    for item in tickers:
        sym = item.get("symbol", "")
        if sym.endswith("-USDT"):
            try:
                if float(item.get("volValue", 0)) >= 300000: symbols.append(sym)
            except ValueError: continue

    print(f"🔍 فحص {len(symbols)} عملة في KuCoin...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_kucoin_single, s) for s in symbols]):
            if res.result(): alerts.append(res.result())
    return alerts

# ================== Bybit ==================
def check_bybit_single(symbol):
    try:
        response = session.get(f"{BYBIT_BASE}/v5/market/open-interest", params={"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 2}, timeout=8)
        if response.status_code != 200: return None 
        res = response.json()
        data_list = res.get("result", {}).get("list", [])
        if len(data_list) < 2: return None

        new_oi, old_oi = float(data_list[0]["openInterest"]), float(data_list[1]["openInterest"])
        if old_oi <= 0: return None

        oi_change = ((new_oi - old_oi) / old_oi) * 100
        if oi_change >= 0.8:
            return {"exchange": "Bybit", "symbol": symbol, "text": f"🟢 <b>تدفق سيولة عقود (OI) — Bybit</b>\n🪙 العملة: <b>#{symbol}</b>\n📈 ارتفاع OI (5د): <b>+{oi_change:.2f}%</b>"}
        elif oi_change <= -0.8:
            return {"exchange": "Bybit", "symbol": symbol, "text": f"🔴 <b>خروج سيولة عقود (OI) — Bybit</b>\n🪙 العملة: <b>#{symbol}</b>\n📉 انخفاض OI (5د): <b>{oi_change:.2f}%</b>"}
    except Exception: pass
    return None

def scan_all_bybit():
    alerts = []
    try:
        response = session.get(f"{BYBIT_BASE}/v5/market/tickers", params={"category": "linear"}, timeout=12)
        if response.status_code != 200:
            print("⚠️ منصة Bybit رفضت الاتصال مؤقتاً.")
            return alerts
        tickers = response.json().get("result", {}).get("list", [])
    except Exception: return alerts

    symbols = []
    for item in tickers:
        sym = item.get("symbol", "")
        if sym.endswith("USDT"):
            try:
                if float(item.get("turnover24h", 0)) >= 1000000: symbols.append(sym)
            except ValueError: continue

    print(f"🔍 فحص {len(symbols)} عملة في Bybit...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        for res in as_completed([executor.submit(check_bybit_single, s) for s in symbols]):
            if res.result(): alerts.append(res.result())
    return alerts

# ================== Binance ==================
def check_binance_single(symbol):
    try:
        res = session.get(f"{BINANCE_BASE}/klines", params={"symbol": symbol, "interval": "5m", "limit": 7}, timeout=8).json()
        if not isinstance(res, list) or len(res) < 7: return None

        current, previous = res[-2], res[:-2]
        open_p, close_p, vol_5m = float(current[1]), float(current[4]), float(current[5])
        avg_vol = sum(float(c[5]) for c in previous) / len(previous)
        if open_p <= 0 or avg_vol <= 0 or vol_5m < 50000: return None

        price_change = ((close_p - open_p) / open_p) * 100
        vol_mult = vol_5m / avg_vol

        if vol_mult >= 2.0:
            if price_change >= 0.35:
                return {"exchange": "Binance", "symbol": symbol, "text": f"🟢 <b>دخول سيولة شرائية — Binance</b>\n🪙 العملة: <b>#{symbol}</b>\n💵 السعر: <code>${format_price(close_p)}</code> (<b>+{price_change:.2f}%</b>)\n🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"}
            elif price_change <= -0.35:
                return {"exchange": "Binance", "symbol": symbol, "text": f"🔴 <b>خروج سيولة بيعية — Binance</b>\n🪙 العملة: <b>#{symbol}</b>\n💵 السعر: <code>${format_price(close_p)}</code> (<b>{price_change:.2f}%</b>)\n💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"}
    except Exception: pass
    return None

def scan_all_binance():
    alerts = []
    try:
        res = session.get(f"{BINANCE_BASE}/ticker/24hr", timeout=12).json()
        if not isinstance(res, list): return alerts
    except Exception: return alerts

    symbols = []
    for item in res:
        sym = item.get("symbol", "")
        if sym.endswith("USDT"):
            try:
                if float(item.get("quoteVolume", 0)) >= 2000000: symbols.append(sym)
            except ValueError: continue

    print(f"🔍 فحص {len(symbols)} عملة في Binance...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_binance_single, s) for s in symbols]):
            if res.result(): alerts.append(res.result())
    return alerts

# ================== Main ==================
def main():
    print("🚀 بدء فحص السيولة الشامل...")
    
    all_alerts = (
        scan_all_okx() + scan_all_kucoin() + scan_all_bybit() + scan_all_binance()
    )

    if all_alerts:
        print(f"📊 تم العثور على {len(all_alerts)} تنبيه. جاري توليد الصور...")
        for alert in all_alerts:
            print(f"📈 توليد شارت لـ {alert['exchange']} | {alert['symbol']}...")
            chart_image = generate_chart_image(alert['exchange'], alert['symbol'])
            
            for chat_id in CHAT_IDS:
                if chart_image:
                    send_telegram_photo(chart_image, alert['text'], chat_id)
                else:
                    # إذا فشل رسم الصورة، يرسل النص كاحتياط
                    send_telegram(alert['text'])
    else:
        print("ℹ️ لا توجد تحركات سيولة قوية حالياً.")
        if EVENT_NAME == "workflow_dispatch":
            send_telegram("✅ <b>فحص الرادار ناجح!</b>\nتم فحص جميع المنصات، ولا توجد تحركات غير طبيعية حالياً.")

if __name__ == "__main__":
    main()
