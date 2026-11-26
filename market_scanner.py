import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
RAW_CHAT_IDS = os.getenv("CHAT_ID", "").strip()
CHAT_IDS = [cid.strip() for cid in RAW_CHAT_IDS.split(",") if cid.strip()]
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 CryptoScanner/2.0"})

P = "https" + "://"
TG_BASE = P + "api.telegram.org/bot"
OKX_BASE = P + "://okx.com"
KUCOIN_BASE = P + "://kucoin.com"
BYBIT_BASE = P + "://bybit.com"
BINANCE_BASE = P + "data-api.binance.vision/api/v3"


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_IDS:
        print("⚠️ تنبيه: لم يتم العثور على BOT_TOKEN أو CHAT_ID")
        return False

    url = f"{TG_BASE}{BOT_TOKEN}/sendMessage"
    success = False
    for chat_id in CHAT_IDS:
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        try:
            response = session.post(url, json=payload, timeout=15)
            data = response.json()
            if data.get("ok"):
                print(f"✅ تم الإرسال بنجاح إلى: {chat_id}")
                success = True
            else:
                print(f"❌ فشل الإرسال إلى {chat_id}: {data.get('description')}")
        except Exception as e:
            print(f"❌ خطأ اتصال بتيليجرام: {e}")
    return success


def check_okx_oi_single(coin_name):
    try:
        oi_res = session.get(
            f"{OKX_BASE}/rubik/stat/contracts/open-interest-volume",
            params={"ccy": coin_name, "period": "5m"},
            timeout=8,
        ).json()
        data_list = oi_res.get("data", [])
        if len(data_list) < 2:
            return None

        new_oi = float(data_list)
        old_oi = float(data_list)
        if old_oi <= 0 or new_oi < 300000:
            return None

        oi_change = ((new_oi - old_oi) / old_oi) * 100
        if oi_change >= 0.8:
            return (
                f"🟢 <b>تدفق سيولة عقود (OI) — OKX</b>\n"
                f"🪙 العملة: <b>#{coin_name}USDT</b>\n"
                f"📈 ارتفاع OI (5د): <b>+{oi_change:.2f}%</b>\n"
                f"💰 العقود المفتوحة: <b>${new_oi:,.0f}</b>"
            )
        elif oi_change <= -0.8:
            return (
                f"🔴 <b>خروج سيولة عقود (OI) — OKX</b>\n"
                f"🪙 العملة: <b>#{coin_name}USDT</b>\n"
                f"📉 انخفاض OI (5د): <b>{oi_change:.2f}%</b>\n"
                f"💰 المتبقي في العقود: <b>${new_oi:,.0f}</b>"
            )
    except Exception:
        pass
    return None


def scan_all_okx():
    alerts = []
    try:
        res = session.get(f"{OKX_BASE}/market/tickers", params={"instType": "SWAP"}, timeout=12).json()
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
                    coins.append(inst_id.split("-"))
            except ValueError:
                continue

    coins = list(dict.fromkeys(coins))
    print(f"🔍 فحص {len(coins)} عملة في OKX...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_okx_oi_single, c) for c in coins]):
            if res.result():
                alerts.append(res.result())
    return alerts


def check_kucoin_single(symbol):
    try:
        res = session.get(
            f"{KUCOIN_BASE}/market/candles",
            params={"type": "5min", "symbol": symbol},
            timeout=8,
        ).json()
        candles = res.get("data", [])
        if not isinstance(candles, list) or len(candles) < 7:
            return None

        current = candles
        previous = candles[2:7]
        open_p = float(current)
        close_p = float(current)
        vol_5m = float(current)

        avg_vol = sum(float(c) for c in previous) / len(previous)
        if open_p <= 0 or avg_vol <= 0 or vol_5m < 10000:
            return None

        price_change = ((close_p - open_p) / open_p) * 100
        vol_mult = vol_5m / avg_vol
        clean_name = symbol.replace("-", "")

        if vol_mult >= 1.8:
            if price_change >= 0.30:
                return (
                    f"🟢 <b>دخول سيولة شرائية — KuCoin</b>\n"
                    f"🪙 العملة: <b>#{clean_name}</b>\n"
                    f"💵 السعر: <code>${close_p}</code> (<b>+{price_change:.2f}%</b>)\n"
                    f"🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                )
            elif price_change <= -0.30:
                return (
                    f"🔴 <b>خروج سيولة بيعية — KuCoin</b>\n"
                    f"🪙 العملة: <b>#{clean_name}</b>\n"
                    f"💵 السعر: <code>${close_p}</code> (<b>{price_change:.2f}%</b>)\n"
                    f"💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                )
    except Exception:
        pass
    return None


def scan_all_kucoin():
    alerts = []
    try:
        res = session.get(f"{KUCOIN_BASE}/market/allTickers", timeout=12).json()
        tickers = res.get("data", {}).get("ticker", [])
    except Exception as e:
        print(f"خطأ في KuCoin: {e}")
        return alerts

    symbols = []
    for item in tickers:
        sym = item.get("symbol", "")
        if sym.endswith("-USDT"):
            try:
                if float(item.get("volValue", 0)) >= 300000:
                    symbols.append(sym)
            except ValueError:
                continue

    print(f"🔍 فحص {len(symbols)} عملة في KuCoin...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_kucoin_single, s) for s in symbols]):
            if res.result():
                alerts.append(res.result())
    return alerts
def check_bybit_single(symbol):
    try:
        res = session.get(
            f"{BYBIT_BASE}/market/open-interest",
            params={"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 2},
            timeout=8,
        ).json()
        data_list = res.get("result", {}).get("list", [])
        if len(data_list) < 2:
            return None

        new_oi = float(data_list["openInterest"])
        old_oi = float(data_list["openInterest"])
        if old_oi <= 0:
            return None

        oi_change = ((new_oi - old_oi) / old_oi) * 100
        if oi_change >= 0.8:
            return (
                f"🟢 <b>تدفق سيولة عقود (OI) — Bybit</b>\n"
                f"🪙 العملة: <b>#{symbol}</b>\n"
                f"📈 ارتفاع OI (5د): <b>+{oi_change:.2f}%</b>"
            )
        elif oi_change <= -0.8:
            return (
                f"🔴 <b>خروج سيولة عقود (OI) — Bybit</b>\n"
                f"🪙 العملة: <b>#{symbol}</b>\n"
                f"📉 انخفاض OI (5د): <b>{oi_change:.2f}%</b>"
            )
    except Exception:
        pass
    return None


def scan_all_bybit():
    alerts = []
    try:
        res = session.get(f"{BYBIT_BASE}/market/tickers", params={"category": "linear"}, timeout=12).json()
        tickers = res.get("result", {}).get("list", [])
    except Exception as e:
        print(f"خطأ في Bybit: {e}")
        return alerts

    symbols = []
    for item in tickers:
        sym = item.get("symbol", "")
        if sym.endswith("USDT"):
            try:
                if float(item.get("turnover24h", 0)) >= 1000000:
                    symbols.append(sym)
            except ValueError:
                continue

    print(f"🔍 فحص {len(symbols)} عملة في Bybit...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_bybit_single, s) for s in symbols]):
            if res.result():
                alerts.append(res.result())
    return alerts


def check_binance_single(symbol):
    try:
        res = session.get(
            f"{BINANCE_BASE}/klines",
            params={"symbol": symbol, "interval": "5m", "limit": 7},
            timeout=8,
        ).json()
        if not isinstance(res, list) or len(res) < 7:
            return None

        current = res[-2]
        previous = res[:-2]
        open_p = float(current)
        close_p = float(current)
        vol_5m = float(current)

        avg_vol = sum(float(c) for c in previous) / len(previous)
        if open_p <= 0 or avg_vol <= 0 or vol_5m < 50000:
            return None

        price_change = ((close_p - open_p) / open_p) * 100
        vol_mult = vol_5m / avg_vol

        if vol_mult >= 2.0:
            if price_change >= 0.35:
                return (
                    f"🟢 <b>دخول سيولة شرائية — Binance</b>\n"
                    f"🪙 العملة: <b>#{symbol}</b>\n"
                    f"💵 السعر: <code>${close_p}</code> (<b>+{price_change:.2f}%</b>)\n"
                    f"🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                )
            elif price_change <= -0.35:
                return (
                    f"🔴 <b>خروج سيولة بيعية — Binance</b>\n"
                    f"🪙 العملة: <b>#{symbol}</b>\n"
                    f"💵 السعر: <code>${close_p}</code> (<b>{price_change:.2f}%</b>)\n"
                    f"💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                )
    except Exception:
        pass
    return None


def scan_all_binance():
    alerts = []
    try:
        res = session.get(f"{BINANCE_BASE}/ticker/24hr", timeout=12).json()
        if not isinstance(res, list):
            return alerts
    except Exception as e:
        print(f"خطأ في Binance: {e}")
        return alerts

    symbols = []
    for item in res:
        sym = item.get("symbol", "")
        if sym.endswith("USDT"):
            try:
                if float(item.get("quoteVolume", 0)) >= 2000000:
                    symbols.append(sym)
            except ValueError:
                continue

    print(f"🔍 فحص {len(symbols)} عملة في Binance...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in as_completed([executor.submit(check_binance_single, s) for s in symbols]):
            if res.result():
                alerts.append(res.result())
    return alerts


def main():
    print("🚀 بدء فحص السيولة الشامل في 4 منصات...")
    all_alerts = (
        scan_all_okx()
        + scan_all_kucoin()
        + scan_all_bybit()
        + scan_all_binance()
    )

    if all_alerts:
        print(f"📊 تم العثور على {len(all_alerts)} تنبيه سيولة.")
        now_str = datetime.utcnow().strftime("%H:%M UTC")
        header = f"📡 <b>رادار السيولة اللحظي (4 منصات)</b> ({now_str})\n" + ("—" * 18) + "\n\n"
        
        chunk = header
        for alert in all_alerts:
            if len(chunk) + len(alert) + 4 > 3800:
                send_telegram(chunk)
                chunk = header + alert + "\n\n"
            else:
                chunk += alert + "\n\n"
        if chunk.strip():
            send_telegram(chunk)
    else:
        print("ℹ️ لا توجد تحركات سيولة قوية مطابقة للشروط حالياً.")
        if EVENT_NAME == "workflow_dispatch":
            send_telegram("✅ <b>اختبار الرادار الشامل (4 منصات) ناجح!</b>\nتم فحص OKX و KuCoin و Bybit و Binance بنجاح، ولا توجد تحركات سيولة غير طبيعية حالياً.")


if __name__ == "__main__":
    main()
                    
