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

# --- إرسال النصوص ---
def send_telegram(message):
    if not BOT_TOKEN or not CHAT_IDS:
        return False
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
                print(f"❌ فشل إرسال النص إلى {chat_id}: {response.json().get('description')}")
        except Exception as e:
            print(f"❌ خطأ اتصال بالتيليجرام (نص): {e}")
    return success

# --- توليد الشارت ---
def generate_chart_image(symbol):
    try:
        res = session.get(
            f"{BINANCE_BASE}/klines",
            params={"symbol": symbol, "interval": "5m", "limit": 50},
            timeout=10
        ).json()
        
        if not isinstance(res, list) or len(res) == 0:
            return None

        df = pd.DataFrame(res, columns=[
            'time', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'
        ])
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        df.set_index('time', inplace=True)
        
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        mc = mpf.make_marketcolors(up='#00ff88', down='#ff3333', edge='inherit', wick='inherit', volume='in')
        s  = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True, facecolor='#131722', edgecolor='#131722')

        buf = io.BytesIO()
        mpf.plot(df, type='candle', style=s, volume=True, 
                 savefig=dict(fname=buf, format='png', dpi=100, bbox_inches='tight'))
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"فشل توليد الشارت لـ {symbol}: {e}")
        return None

# --- إرسال الصور (مع إصلاح المؤشر للقناة العامة) ---
def send_telegram_photo(photo_buf, caption, chat_id):
    photo_buf.seek(0) # 🔴 إعادة المؤشر لبداية الصورة لكل جهة إرسال
    url = f"{TG_BASE}{BOT_TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', photo_buf, 'image/png')}
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    try:
        response = session.post(url, data=data, files=files, timeout=15)
        res_json = response.json()
        if res_json.get("ok"):
            print(f"✅ تم إرسال الصورة بنجاح إلى: {chat_id}")
            return True
        else:
            print(f"❌ فشل إرسال الصورة إلى {chat_id}: {res_json.get('description')}")
    except Exception as e:
        print(f"❌ خطأ اتصال عند إرسال الصورة إلى {chat_id}: {e}")
    return False

# ================== OKX ==================
def check_okx_oi_single(coin_name):
    try:
        oi_res = session.get(
            f"{OKX_BASE}/api/v5/rubik/stat/contracts/open-interest-volume",
            params={"ccy": coin_name, "period": "5m"},
            timeout=8,
        ).json()
        data_list = oi_res.get("data", [])
        if len(data_list) < 2: return None

        new_oi = float(data_list[0][1])
        old_oi = float(data_list[1][1])
        if old_oi <= 0 or new_oi < 300000: return None

        oi_change = ((new_oi - old_oi) / old_oi) * 100
        if oi_change >= 0.8:
            return f"🟢 <b>تدفق سيولة عقود (OI) — OKX</b>\n🪙 العملة: <b>#{coin_name}USDT</b>\n📈 ارتفاع OI (5د): <b>+{oi_change:.2f}%</b>\n💰 العقود المفتوحة: <b>${new_oi:,.0f}</b>"
        elif oi_change <= -0.8:
            return f"🔴 <b>خروج سيولة عقود (OI) — OKX</b>\n🪙 العملة: <b>#{coin_name}USDT</b>\n📉 انخفاض OI (5د): <b>{oi_change:.2f}%</b>\n💰 المتبقي في العقود: <b>${new_oi:,.0f}</b>"
    except Exception:
        pass
    return None

def scan_all_okx():
    alerts = []
    try:
        res = session.get(f"{OKX_BASE}/api/v5/market/tickers", params={"instType": "SWAP"}, timeout=12).json()
        swaps = res.get("data", [])
    except Exception as e:
        print(f"خطأ في OKX: {e}")
        return alerts

    coins = []
    for item in swaps:
        inst_id = item.get("instId", "")
        if inst_id.endswith("-USDT-SWAP"):
            try:
                vol_24h = float(item.get("volCcy24h", 0)) * float(item.get("last", 0))
                if vol_24h >= 500000:
                    coins.append(inst_id.split("-")[0])
            except ValueError:
                continue

    coins = list(dict.fromkeys(coins))
    print(f"🔍 فحص {len(coins)} عملة في OKX...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_okx_oi_single, c) for c in coins]):
            if res.result(): alerts.append(res.result())
    return alerts

# ================== KuCoin ==================
def check_kucoin_single(symbol):
    try:
        res = session.get(f"{KUCOIN_BASE}/api/v1/market/candles", params={"type": "5min", "symbol": symbol}, timeout=8).json()
        candles = res.get("data", [])
        if not isinstance(candles, list) or len(candles) < 7: return None

        current  = candles[0]
        previous = candles[1:7]
        open_p  = float(current[1])
        close_p = float(current[2])
        vol_5m  = float(current[5])

        avg_vol = sum(float(c[5]) for c in previous) / len(previous)
        if open_p <= 0 or avg_vol <= 0 or vol_5m < 10000: return None

        price_change = ((close_p - open_p) / open_p) * 100
        vol_mult = vol_5m / avg_vol
        clean_name = symbol.replace("-", "")

        if vol_mult >= 1.8:
            if price_change >= 0.30:
                return f"🟢 <b>دخول سيولة شرائية — KuCoin</b>\n🪙 العملة: <b>#{clean_name}</b>\n💵 السعر: <code>${format_price(close_p)}</code> (<b>+{price_change:.2f}%</b>)\n🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
            elif price_change <= -0.30:
                return f"🔴 <b>خروج سيولة بيعية — KuCoin</b>\n🪙 العملة: <b>#{clean_name}</b>\n💵 السعر: <code>${format_price(close_p)}</code> (<b>{price_change:.2f}%</b>)\n💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
    except Exception:
        pass
    return None

def scan_all_kucoin():
    alerts = []
    try:
        res = session.get(f"{KUCOIN_BASE}/api/v1/market/allTickers", timeout=12).json()
        tickers = res.get("data", {}).get("ticker", [])
    except Exception as e:
        print(f"خطأ في KuCoin: {e}")
        return alerts

    symbols = []
    for item in tickers:
        sym = item.get("symbol", "")
        if sym.endswith("-USDT"):
            try:
                if float(item.get("volValue", 0)) >= 300000: symbols.append(sym)
            except ValueError:
                continue

    print(f"🔍 فحص {len(symbols)} عملة في KuCoin...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_kucoin_single, s) for s in symbols]):
            if res.result(): alerts.append(res.result())
    return alerts

# ================== Bybit (محصن ضد الحظر) ==================
def check_bybit_single(symbol):
    try:
        response = session.get(
            f"{BYBIT_BASE}/v5/market/open-interest", 
            params={"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 2}, 
            timeout=8
        )
        if response.status_code != 200:
            return None 
            
        res = response.json()
        data_list = res.get("result", {}).get("list", [])
        if len(data_list) < 2: return None

        new_oi = float(data_list[0]["openInterest"])
        old_oi = float(data_list[1]["openInterest"])
        if old_oi <= 0: return None

        oi_change = ((new_oi - old_oi) / old_oi) * 100
        if oi_change >= 0.8:
            return f"🟢 <b>تدفق سيولة عقود (OI) — Bybit</b>\n🪙 العملة: <b>#{symbol}</b>\n📈 ارتفاع OI (5د): <b>+{oi_change:.2f}%</b>"
        elif oi_change <= -0.8:
            return f"🔴 <b>خروج سيولة عقود (OI) — Bybit</b>\n🪙 العملة: <b>#{symbol}</b>\n📉 انخفاض OI (5د): <b>{oi_change:.2f}%</b>"
    except Exception:
        pass
    return None

def scan_all_bybit():
    alerts = []
    try:
        response = session.get(f"{BYBIT_BASE}/v5/market/tickers", params={"category": "linear"}, timeout=12)
        if response.status_code != 200:
            print("⚠️ منصة Bybit رفضت الاتصال مؤقتاً.")
            return alerts
            
        res = response.json()
        tickers = res.get("result", {}).get("list", [])
    except Exception as e:
        print(f"خطأ في Bybit: {e}")
        return alerts

    symbols = []
    for item in tickers:
        sym = item.get("symbol", "")
        if sym.endswith("USDT"):
            try:
                if float(item.get("turnover24h", 0)) >= 1000000: symbols.append(sym)
            except ValueError:
                continue

    print(f"🔍 فحص {len(symbols)} عملة في Bybit...")
    with ThreadPoolExecutor(max_workers=5) as executor: # خفضناها لـ 5 لتجنب الحظر
        for res in as_completed([executor.submit(check_bybit_single, s) for s in symbols]):
            if res.result(): alerts.append(res.result())
    return alerts

# ================== Binance (مع الشارت) ==================
def check_binance_single(symbol):
    try:
        res = session.get(f"{BINANCE_BASE}/klines", params={"symbol": symbol, "interval": "5m", "limit": 7}, timeout=8).json()
        if not isinstance(res, list) or len(res) < 7: return None

        current = res[-2] # الشمعة المغلقة الأخيرة
        previous = res[:-2]
        open_p = float(current[1])
        close_p = float(current[4])
        vol_5m = float(current[5])

        avg_vol = sum(float(c[5]) for c in previous) / len(previous)
        if open_p <= 0 or avg_vol <= 0 or vol_5m < 50000: return None

        price_change = ((close_p - open_p) / open_p) * 100
        vol_mult = vol_5m / avg_vol

        if vol_mult >= 2.0:
            if price_change >= 0.35:
                msg = f"🟢 <b>دخول سيولة شرائية — Binance</b>\n🪙 العملة: <b>#{symbol}</b>\n💵 السعر: <code>${format_price(close_p)}</code> (<b>+{price_change:.2f}%</b>)\n🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                return {"symbol": symbol, "text": msg}
            elif price_change <= -0.35:
                msg = f"🔴 <b>خروج سيولة بيعية — Binance</b>\n🪙 العملة: <b>#{symbol}</b>\n💵 السعر: <code>${format_price(close_p)}</code> (<b>{price_change:.2f}%</b>)\n💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                return {"symbol": symbol, "text": msg}
    except Exception:
        pass
    return None

def scan_all_binance():
    alerts = []
    try:
        res = session.get(f"{BINANCE_BASE}/ticker/24hr", timeout=12).json()
        if not isinstance(res, list): return alerts
    except Exception as e:
        print(f"خطأ في Binance: {e}")
        return alerts

    symbols = []
    for item in res:
        sym = item.get("symbol", "")
        if sym.endswith("USDT"):
            try:
                if float(item.get("quoteVolume", 0)) >= 2000000: symbols.append(sym)
            except ValueError:
                continue

    print(f"🔍 فحص {len(symbols)} عملة في Binance...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_binance_single, s) for s in symbols]):
            if res.result(): alerts.append(res.result())
    return alerts

# ================== Main ==================
def main():
    print("🚀 بدء فحص السيولة الشامل...")
    
    okx_alerts = scan_all_okx()
    kucoin_alerts = scan_all_kucoin()
    bybit_alerts = scan_all_bybit()
    binance_alerts_dicts = scan_all_binance()
    
    other_platforms_alerts = okx_alerts + kucoin_alerts + bybit_alerts

    # إرسال إشعارات Binance (مع الصور)
    if binance_alerts_dicts:
        for alert in binance_alerts_dicts:
            print(f"📊 توليد شارت لـ {alert['symbol']}...")
            chart_image = generate_chart_image(alert['symbol'])
            if chart_image:
                for chat_id in CHAT_IDS:
                    send_telegram_photo(chart_image, alert['text'], chat_id)
            else:
                send_telegram(alert['text'])

    # إرسال إشعارات المنصات الأخرى (نصوص فقط)
    if other_platforms_alerts:
        now_str = datetime.utcnow().strftime("%H:%M UTC")
        header = f"📡 <b>رادار السيولة (OKX | KuCoin | Bybit)</b> ({now_str})\n" + ("—" * 18) + "\n\n"
        chunk = header
        for alert in other_platforms_alerts:
            if len(chunk) + len(alert) + 4 > 3800:
                send_telegram(chunk)
                chunk = header + alert + "\n\n"
            else:
                chunk += alert + "\n\n"
        if chunk.strip() != header.strip():
            send_telegram(chunk)

    if not other_platforms_alerts and not binance_alerts_dicts:
        print("ℹ️ لا توجد تحركات سيولة قوية حالياً.")
        if EVENT_NAME == "workflow_dispatch":
            send_telegram("✅ <b>فحص الرادار ناجح!</b>\nتم فحص جميع المنصات، ولا توجد تحركات غير طبيعية حالياً.")

if __name__ == "__main__":
    main()
