import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
RAW_CHAT_IDS = os.getenv("CHAT_ID", "").strip()
CHAT_IDS = [cid.strip() for cid in RAW_CHAT_IDS.split(",") if cid.strip()]

session = requests.Session()
session.headers.update({"User-Agent": "CryptoWhalesRadar/4.0"})


def send_telegram(message):
    url = f"https://telegram.org{BOT_TOKEN}/sendMessage"
    success = False
    for chat_id in CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            response = session.post(url, json=payload, timeout=15)
            data = response.json()
            if data.get("ok"):
                success = True
            else:
                print(f"فشل الإرسال إلى {chat_id}:", data.get("description", "خطأ غير معروف"))
        except Exception:
            print(f"تعذّر الاتصال بتيليجرام للإرسال إلى {chat_id}.")
    return success


def check_okx_oi_single(coin_name):
    try:
        oi_res = session.get(
            "https://okx.com",
            params={"ccy": coin_name, "period": "5m"},
            timeout=8,
        ).json()
        data_list = oi_res.get("data", [])
        if len(data_list) < 2:
            return None

        new_oi = float(data_list[0][1])
        old_oi = float(data_list[1][1])
        if old_oi <= 0 or new_oi < 500000:
            return None

        oi_change = ((new_oi - old_oi) / old_oi) * 100
        if oi_change >= 1.6:
            return (
                f"🟢 <b>تدفق سيولة عقود (OI) — OKX</b>\n"
                f"🪙 العملة: <b>#{coin_name}USDT</b>\n"
                f"📈 ارتفاع OI (5د): <b>+{oi_change:.2f}%</b>\n"
                f"💰 العقود المفتوحة: <b>${new_oi:,.0f}</b>"
            )
        elif oi_change <= -1.6:
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
        res = session.get(
            "https://okx.com",
            params={"instType": "SWAP"},
            timeout=12,
        ).json()
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
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(check_okx_oi_single, coin) for coin in coins]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alerts.append(res)
    return alerts


def check_kucoin_single(symbol):
    try:
        res = session.get(
            "https://kucoin.com",
            params={"type": "5min", "symbol": symbol},
            timeout=8,
        ).json()
        candles = res.get("data", [])
        if not isinstance(candles, list) or len(candles) < 7:
            return None

        current = candles[1]
        previous = candles[2:7]

        open_p = float(current[1])
        close_p = float(current[2])
        vol_5m = float(current[6])

        avg_vol = sum(float(c[6]) for c in previous) / len(previous)
        if open_p <= 0 or avg_vol <= 0 or vol_5m < 15000:
            return None

        price_change = ((close_p - open_p) / open_p) * 100
        vol_mult = vol_5m / avg_vol
        clean_name = symbol.replace("-", "")

        if vol_mult >= 2.5:
            if price_change >= 0.5:
                return (
                    f"🟢 <b>دخول سيولة شرائية — KuCoin</b>\n"
                    f"🪙 العملة: <b>#{clean_name}</b>\n"
                    f"💵 السعر: <code>${close_p}</code> (<b>+{price_change:.2f}%</b>)\n"
                    f"🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                )
            elif price_change <= -0.5:
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
        res = session.get("https://kucoin.com", timeout=12).json()
        tickers = res.get("data", {}).get("ticker", [])
    except Exception as e:
        print(f"خطأ في KuCoin: {e}")
        return alerts

    symbols = []
    for t in tickers:
        sym = t.get("symbol", "")
        if sym.endswith("-USDT") and not any(x in sym for x in ["USDC", "DAI", "TUSD", "3L", "3S", "UP", "DOWN"]):
            try:
                vol_val = float(t.get("volValue", 0))
                if vol_val >= 100000:
                    symbols.append(sym)
            except ValueError:
                continue

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(check_kucoin_single, sym) for sym in symbols]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alerts.append(res)
    return alerts


def check_kraken_single(pair):
    try:
        res = session.get(
            "https://kraken.com",
            params={"pair": pair, "interval": 5},
            timeout=8,
        ).json()
        result = res.get("result", {})
        pair_key = next((k for k in result if k != "last"), None)
        if not pair_key:
            return None

        candles = result[pair_key]
        if len(candles) < 7:
            return None

        current = candles[-2]
        previous = candles[-7:-2]

        open_p = float(current[1])
        close_p = float(current[4])
        vol_5m = float(current[6]) * close_p
        trades_5m = int(current[7])

        avg_vol = sum(float(c[6]) * float(c[4]) for c in previous) / len(previous)
        avg_trades = sum(int(c[7]) for c in previous) / len(previous)

        if open_p <= 0 or avg_vol <= 0 or avg_trades <= 0 or vol_5m < 15000:
            return None

        price_change = ((close_p - open_p) / open_p) * 100
        vol_mult = vol_5m / avg_vol
        trades_mult = trades_5m / avg_trades

        if vol_mult >= 2.3 and trades_mult >= 1.8:
            if price_change >= 0.45:
                return (
                    f"🟢 <b>دخول سيولة شرائية — Kraken</b>\n"
                    f"🪙 العملة: <b>#{pair}</b>\n"
                    f"💵 السعر: <code>${close_p}</code> (<b>+{price_change:.2f}%</b>)\n"
                    f"🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)\n"
                    f"👥 الصفقات: <b>{trades_5m}</b> ({trades_mult:.1f}x)"
                )
            elif price_change <= -0.45:
                return (
                    f"🔴 <b>خروج سيولة وتصريف — Kraken</b>\n"
                    f"🪙 العملة: <b>#{pair}</b>\n"
                    f"💵 السعر: <code>${close_p}</code> (<b>{price_change:.2f}%</b>)\n"
                    f"💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)\n"
                    f"👥 الصفقات: <b>{trades_5m}</b> ({trades_mult:.1f}x)"
                )
    except Exception:
        pass
    return None


def scan_all_kraken():
    alerts = []
    try:
        res = session.get("https://kraken.com", timeout=12).json()
        tickers = res.get("result", {})
    except Exception as e:
        print(f"خطأ في Kraken: {e}")
        return alerts

    excluded = ["USDC", "USDTZ", "DAI", "PYUSD", "EUR", "GBP", "AUD", "CAD", "JPY", "CHF"]
    pairs = []
    for pair, info in tickers.items():
        if not pair.endswith(("USD", "USDT")) or any(s in pair for s in excluded):
            continue
        try:
            vol_usd = float(info["v"][1]) * float(info["c"][0])
            if vol_usd >= 50000:
                pairs.append(pair)
        except (KeyError, IndexError, ValueError):
            continue

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(check_kraken_single, pair) for pair in pairs]
        for future in as_completed(futures):
            res = future.result()
            if res:
                alerts.append(res)
    return alerts


def main():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] 🚀 بدء المسح الشامل لجميع عملات OKX و KuCoin و Kraken...")

    all_alerts = []
    all_alerts.extend(scan_all_okx())
    all_alerts.extend(scan_all_kucoin())
    all_alerts.extend(scan_all_kraken())

    sent_count = 0
    for alert in all_alerts[:12]:
        full_msg = f"{alert}\n\n⚠️ <i>رصد آلي لحركة السيولة — وليس توصية استثمارية.</i>"
        if send_telegram(full_msg):
            sent_count += 1

    print(f"[{now}] ✅ اكتمل المسح الشامل — الإشارات المكتشفة: {len(all_alerts)} | المرسلة: {sent_count}")


if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_IDS:
        print("❌ خطأ: تأكد من إعداد BOT_TOKEN و CHAT_ID في Secrets.")
    else:
        main()
