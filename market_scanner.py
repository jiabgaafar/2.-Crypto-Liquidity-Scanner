import os
from datetime import datetime
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

session = requests.Session()
session.headers.update({"User-Agent": "CryptoWhalesRadar/2.0"})


def send_telegram(message):
    url = f"https://telegram.org{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        response = session.post(url, json=payload, timeout=15)
        data = response.json()
        if not data.get("ok"):
            print("فشل إرسال تيليجرام:", data.get("description", "خطأ غير معروف"))
            return False
        return True
    except Exception:
        print("تعذّر إرسال رسالة تيليجرام.")
        return False


def scan_kraken():
    """رصد دخول وخروج السيولة الفورية من منصة Kraken (فريم 5 دقائق)"""
    alerts = []
    try:
        res = session.get("https://kraken.com", timeout=15).json()
        tickers = res.get("result", {})
    except Exception as e:
        print(f"خطأ في الاتصال بـ Kraken: {e}")
        return alerts

    excluded = ["USDC", "USDTZ", "DAI", "PYUSD", "EUR", "GBP"]
    usd_pairs = []

    for pair, info in tickers.items():
        if not pair.endswith(("USD", "USDT")) or any(s in pair for s in excluded):
            continue
        try:
            vol_usd = float(info["v"][1]) * float(info["c"][0])
            usd_pairs.append((pair, vol_usd))
        except (KeyError, IndexError, ValueError):
            continue

    usd_pairs.sort(key=lambda x: x[1], reverse=True)
    top_pairs = [p[0] for p in usd_pairs[:20]]

    for pair in top_pairs:
        try:
            ohlc_res = session.get(
                "https://kraken.com",
                params={"pair": pair, "interval": 5},
                timeout=10,
            ).json()
            result = ohlc_res.get("result", {})
            pair_key = next((k for k in result if k != "last"), None)
            if not pair_key:
                continue

            candles = result[pair_key]
            if len(candles) < 7:
                continue

            current = candles[-2]
            previous = candles[-7:-2]

            open_p = float(current[1])
            close_p = float(current[4])
            vol_5m = float(current[6]) * close_p
            trades_5m = int(current[7])

            avg_vol = sum(float(c[6]) * float(c[4]) for c in previous) / len(previous)
            avg_trades = sum(int(c[7]) for c in previous) / len(previous)

            if open_p <= 0 or avg_vol <= 0 or avg_trades <= 0:
                continue

            price_change = ((close_p - open_p) / open_p) * 100
            vol_mult = vol_5m / avg_vol
            trades_mult = trades_5m / avg_trades

            # شرط انفجار السيولة (مضاعف الحجم >= 2.2 وعدد الصفقات >= 1.8)
            if vol_mult >= 2.2 and trades_mult >= 1.8:
                if price_change >= 0.45:
                    alerts.append(
                        f"🟢 <b>دخول سيولة شرائية (Kraken)</b>\n"
                        f"🪙 الزوج: <b>#{pair}</b>\n"
                        f"💵 السعر: <code>${close_p}</code> (<b>+{price_change:.2f}%</b>)\n"
                        f"🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)\n"
                        f"👥 الصفقات: <b>{trades_5m}</b> ({trades_mult:.1f}x)"
                    )
                elif price_change <= -0.45:
                    alerts.append(
                        f"🔴 <b>خروج سيولة وتصريف (Kraken)</b>\n"
                        f"🪙 الزوج: <b>#{pair}</b>\n"
                        f"💵 السعر: <code>${close_p}</code> (<b>{price_change:.2f}%</b>)\n"
                        f"💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)\n"
                        f"👥 الصفقات: <b>{trades_5m}</b> ({trades_mult:.1f}x)"
                    )
        except Exception:
            continue

    return alerts


def scan_okx_oi():
    """رصد تغيرات الفائدة المفتوحة (Open Interest - OI) من منصة OKX"""
    alerts = []
    # أشهر العملات ذات السيولة العالية في العقود الآجلة
    symbols = [
        "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
        "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "ADA-USDT-SWAP",
        "AVAX-USDT-SWAP", "LINK-USDT-SWAP", "SUI-USDT-SWAP",
        "PEPE-USDT-SWAP", "TON-USDT-SWAP", "NEAR-USDT-SWAP"
    ]

    for inst_id in symbols:
        try:
            # جلب بيانات الفائدة المفتوحة (فريم 5 دقائق)
            oi_res = session.get(
                "https://okx.com",
                params={"ccy": inst_id.split("-")[0], "period": "5m"},
                timeout=10,
            ).json()

            data_list = oi_res.get("data", [])
            if len(data_list) < 2:
                continue

            # OKX ترتب البيانات من الأحدث للأقدم: data_list[0] هو الأحدث، data_list[1] قبله بـ 5 دقائق
            new_oi = float(data_list[0][1])
            old_oi = float(data_list[1][1])

            if old_oi <= 0:
                continue

            oi_change_pct = ((new_oi - old_oi) / old_oi) * 100
            coin_name = inst_id.split("-")[0]

            # رصد دخول سيولة OI قوية (>= +1.5%)
            if oi_change_pct >= 1.5:
                alerts.append(
                    f"🟢 <b>تدفق سيولة عقود (OI) — OKX</b>\n"
                    f"🪙 العملة: <b>#{coin_name}USDT</b>\n"
                    f"📈 ارتفاع OI (5د): <b>+{oi_change_pct:.2f}%</b>\n"
                    f"💰 إجمالي العقود المفتوحة: <b>${new_oi:,.0f}</b>"
                )
            # رصد خروج سيولة OI حاد (<= -1.5%)
            elif oi_change_pct <= -1.5:
                alerts.append(
                    f"🔴 <b>خروج سيولة عقود (OI) — OKX</b>\n"
                    f"🪙 العملة: <b>#{coin_name}USDT</b>\n"
                    f"📉 انخفاض OI (5د): <b>{oi_change_pct:.2f}%</b>\n"
                    f"💰 المتبقي في العقود: <b>${new_oi:,.0f}</b>"
                )
        except Exception:
            continue

    return alerts


def scan_coinbase():
    """رصد طفرات السيولة الفورية من منصة Coinbase"""
    alerts = []
    pairs = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "SUI-USD"]

    for pair in pairs:
        try:
            res = session.get(
                f"https://coinbase.com{pair}/candles",
                params={"granularity": 300},  # 300 ثانية = 5 دقائق
                timeout=10,
            ).json()

            if not isinstance(res, list) or len(res) < 7:
                continue

            # Coinbase ترتب من الأحدث للأقدم: res[1] آخر شمعة مغلقة
            current = res[1]
            previous = res[2:7]

            open_p = float(current[3])
            close_p = float(current[4])
            vol_5m = float(current[5]) * close_p

            avg_vol = sum(float(c[5]) * float(c[4]) for c in previous) / len(previous)
            if open_p <= 0 or avg_vol <= 0:
                continue

            price_change = ((close_p - open_p) / open_p) * 100
            vol_mult = vol_5m / avg_vol

            if vol_mult >= 2.5:
                if price_change >= 0.45:
                    alerts.append(
                        f"🟢 <b>دخول سيولة مؤسسية (Coinbase)</b>\n"
                        f"🪙 الزوج: <b>#{pair.replace('-', '')}</b>\n"
                        f"💵 السعر: <code>${close_p}</code> (<b>+{price_change:.2f}%</b>)\n"
                        f"🔥 السيولة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                    )
                elif price_change <= -0.45:
                    alerts.append(
                        f"🔴 <b>خروج سيولة بيعية (Coinbase)</b>\n"
                        f"🪙 الزوج: <b>#{pair.replace('-', '')}</b>\n"
                        f"💵 السعر: <code>${close_p}</code> (<b>{price_change:.2f}%</b>)\n"
                        f"💸 السيولة الخارجة (5د): <b>${vol_5m:,.0f}</b> (<b>{vol_mult:.1f}x</b>)"
                    )
        except Exception:
            continue

    return alerts


def main():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] ⏳ جاري مسح السيولة (دخول/خروج) و OI من Kraken و OKX و Coinbase...")

    all_alerts = []
    all_alerts.extend(scan_kraken())
    all_alerts.extend(scan_okx_oi())
    all_alerts.extend(scan_coinbase())

    sent_count = 0
    for alert in all_alerts:
        full_msg = f"{alert}\n\n⚠️ <i>رصد آلي لحركة السيولة — وليس توصية استثمارية.</i>"
        if send_telegram(full_msg):
            sent_count += 1

    print(f"[{now}] ✅ اكتمل الفحص — الإشارات المكتشفة: {len(all_alerts)} | المرسلة: {sent_count}")


if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ خطأ: تأكد من إعداد BOT_TOKEN و CHAT_ID في Secrets.")
    else:
        main()
