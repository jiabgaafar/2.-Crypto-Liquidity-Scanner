import os
from datetime import datetime

import requests


BOT_TOKEN = os.getenv("8557681859:AAFKMBGxvtik8ge-Hsrw9wUeZw7f65J40aQ").strip()
CHAT_ID = os.getenv("7624036216").strip()

session = requests.Session()


def get_kraken_data(endpoint, params=None):
    url = f"https://api.kraken.com/0/public/{endpoint}"

    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError("; ".join(data["error"]))

    return data["result"]


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        response = session.post(
            url,
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            print(
                "فشل إرسال تيليجرام:",
                data.get("description", "خطأ غير معروف"),
            )
            return False

        return True

    except requests.RequestException:
        # لا نطبع الاستثناء حتى لا يظهر التوكن ضمن الرابط.
        print("تعذّر إرسال رسالة تيليجرام؛ تحقق من الاتصال والإعدادات.")
        return False

    except ValueError:
        print("استجابة تيليجرام ليست بصيغة JSON صالحة.")
        return False


def scan_market():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] جاري مسح السوق — شموع 5 دقائق...")

    try:
        tickers = get_kraken_data("Ticker")
    except Exception as error:
        print(f"خطأ في جلب بيانات السوق: {error}")
        return

    excluded = [
        "USDC",
        "USDTZ",
        "DAI",
        "PYUSD",
        "EUR",
        "GBP",
    ]

    usd_pairs = []

    for pair, info in tickers.items():
        if not pair.endswith(("USD", "USDT")):
            continue

        if any(symbol in pair for symbol in excluded):
            continue

        try:
            estimated_volume = (
                float(info["v"][1]) * float(info["c"][0])
            )
            usd_pairs.append((pair, estimated_volume))

        except (KeyError, IndexError, TypeError, ValueError):
            print(f"تم تخطي {pair}: بيانات أسعار غير مكتملة.")

    usd_pairs.sort(key=lambda item: item[1], reverse=True)
    top_pairs = [pair for pair, _ in usd_pairs[:25]]

    signals_found = 0
    alerts_sent = 0
    failed_pairs = 0

    for pair in top_pairs:
        try:
            result = get_kraken_data(
                "OHLC",
                params={
                    "pair": pair,
                    "interval": 5,
                },
            )

            pair_key = next(
                key for key in result if key != "last"
            )
            candles = result[pair_key]

            if len(candles) < 7:
                continue

            # آخر شمعة ما زالت مفتوحة؛ نفحص التي قبلها.
            current = candles[-2]
            previous = candles[-7:-2]

            open_price = float(current[1])
            close_price = float(current[4])

            estimated_volume_5m = (
                float(current[6]) * close_price
            )
            trades_5m = int(current[7])

            average_volume = sum(
                float(candle[6]) * float(candle[4])
                for candle in previous
            ) / len(previous)

            average_trades = sum(
                int(candle[7])
                for candle in previous
            ) / len(previous)

            if (
                open_price <= 0
                or average_volume <= 0
                or average_trades <= 0
            ):
                continue

            price_change = (
                (close_price - open_price) / open_price
            ) * 100

            matched = (
                estimated_volume_5m >= average_volume * 2.0
                and trades_5m >= average_trades * 1.8
                and price_change >= 0.4
            )

            if not matched:
                continue

            signals_found += 1

            quote_currency = (
                "USDT" if pair.endswith("USDT") else "USD"
            )

            message = (
                "<b>تنبيه ارتفاع حجم التداول — 5 دقائق</b>\n\n"
                f"الزوج: <b>{pair}</b>\n"
                f"السعر: <code>{close_price}</code> "
                f"{quote_currency}\n"
                f"تغير الشمعة: <b>+{price_change:.2f}%</b>\n"
                f"حجم التداول التقديري: "
                f"<b>{estimated_volume_5m:,.0f}</b> "
                f"{quote_currency}\n"
                f"مضاعف الحجم: "
                f"<b>{estimated_volume_5m / average_volume:.1f}x</b>\n"
                f"عدد الصفقات: <b>{trades_5m}</b>\n"
                f"مضاعف عدد الصفقات: "
                f"<b>{trades_5m / average_trades:.1f}x</b>\n\n"
                "إشارة آلية وفق الشروط، وليست توصية شراء."
            )

            if send_telegram(message):
                alerts_sent += 1
                print(f"تم إرسال تنبيه للزوج {pair}")

        except Exception as error:
            failed_pairs += 1
            print(f"تعذّر فحص {pair}: {error}")

    print(
        f"انتهى الفحص: {signals_found} إشارة مطابقة، "
        f"{alerts_sent} تنبيه مرسل، "
        f"{failed_pairs} زوج تعذّر فحصه."
    )


if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit(
            "الإعدادات ناقصة: يجب توفير BOT_TOKEN وCHAT_ID "
            "في متغيرات البيئة قبل التشغيل."
        )

    scan_market()
