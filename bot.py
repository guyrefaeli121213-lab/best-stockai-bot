import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import database as db
import stock_utils as su
from keep_alive import keep_alive

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def color_for(change_pct):
    if change_pct is None:
        return discord.Color.blurple()
    return discord.Color.green() if change_pct >= 0 else discord.Color.red()


@bot.event
async def on_ready():
    db.init_db()
    try:
        synced = await bot.tree.sync()
        print(f"מחובר בתור {bot.user} | {len(synced)} פקודות סונכרנו")
    except Exception as e:
        print(f"שגיאת סנכרון: {e}")
    if not check_alerts.is_running():
        check_alerts.start()


@tasks.loop(minutes=5)
async def check_alerts():
    alerts = db.get_active_alerts()
    for alert_id, user_id, symbol, target_price, direction in alerts:
        info = su.get_stock_info(symbol)
        if not info:
            continue
        price = info["price"]
        hit = (direction == "above" and price >= target_price) or \
              (direction == "below" and price <= target_price)
        if hit:
            db.deactivate_alert(alert_id)
            try:
                user = await bot.fetch_user(int(user_id))
                dir_word = "עלה מעל" if direction == "above" else "ירד מתחת ל"
                await user.send(
                    f"🔔 **התראת מחיר!** {symbol} {dir_word} {fmt_money(target_price)} "
                    f"(מחיר נוכחי: {fmt_money(price)})"
                )
            except Exception:
                pass


# ---------- 1. /stock ----------
@bot.tree.command(name="stock", description="הצג מידע וגרף על מניה")
@app_commands.describe(symbol="סימול המניה, למשל AAPL")
async def stock_cmd(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()
    info = su.get_stock_info(symbol)
    if not info:
        await interaction.followup.send(f"לא נמצאה מניה בשם `{symbol}`.")
        return

    embed = discord.Embed(
        title=f"{info['name']} ({info['symbol']})",
        color=color_for(info["change_pct"])
    )
    embed.add_field(name="מחיר", value=fmt_money(info["price"]), inline=True)
    if info["change"] is not None:
        sign = "+" if info["change"] >= 0 else ""
        embed.add_field(
            name="שינוי יומי",
            value=f"{sign}{info['change']:.2f} ({sign}{info['change_pct']:.2f}%)",
            inline=True
        )
    if info["market_cap"]:
        embed.add_field(name="שווי שוק", value=f"${info['market_cap']:,}", inline=True)

    chart = su.get_chart_image(symbol, "1mo")
    if chart:
        file = discord.File(chart, filename="chart.png")
        embed.set_image(url="attachment://chart.png")
        await interaction.followup.send(embed=embed, file=file)
    else:
        await interaction.followup.send(embed=embed)


# ---------- 2. /chart ----------
@bot.tree.command(name="chart", description="גרף היסטורי של מניה עם טווח זמן לבחירה")
@app_commands.describe(symbol="סימול המניה", period="טווח זמן")
@app_commands.choices(period=[
    app_commands.Choice(name=p, value=p) for p in su.VALID_PERIODS
])
async def chart_cmd(interaction: discord.Interaction, symbol: str, period: app_commands.Choice[str] = None):
    await interaction.response.defer()
    p = period.value if period else "1mo"
    chart = su.get_chart_image(symbol, p)
    if not chart:
        await interaction.followup.send(f"לא נמצאו נתונים עבור `{symbol}`.")
        return
    file = discord.File(chart, filename="chart.png")
    await interaction.followup.send(f"גרף עבור **{symbol.upper()}** ({p}):", file=file)


# ---------- 3. /buy ----------
@bot.tree.command(name="buy", description="קנה מניות בסימולציה")
@app_commands.describe(symbol="סימול המניה", shares="כמות מניות לקנייה")
async def buy_cmd(interaction: discord.Interaction, symbol: str, shares: float):
    await interaction.response.defer()
    if shares <= 0:
        await interaction.followup.send("כמות המניות חייבת להיות חיובית.")
        return
    info = su.get_stock_info(symbol)
    if not info:
        await interaction.followup.send(f"לא נמצאה מניה בשם `{symbol}`.")
        return

    uid = str(interaction.user.id)
    success = db.buy_stock(uid, info["symbol"], shares, info["price"])
    if not success:
        balance = db.get_balance(uid)
        await interaction.followup.send(
            f"אין מספיק יתרה. עלות: {fmt_money(shares * info['price'])} | יתרה: {fmt_money(balance)}"
        )
        return

    await interaction.followup.send(
        f"✅ קנית {shares} מניות של **{info['symbol']}** במחיר {fmt_money(info['price'])} ליחידה "
        f"(סה\"כ {fmt_money(shares * info['price'])})."
    )


# ---------- 4. /sell ----------
@bot.tree.command(name="sell", description="מכור מניות מהפורטפוליו שלך")
@app_commands.describe(symbol="סימול המניה", shares="כמות מניות למכירה")
async def sell_cmd(interaction: discord.Interaction, symbol: str, shares: float):
    await interaction.response.defer()
    if shares <= 0:
        await interaction.followup.send("כמות המניות חייבת להיות חיובית.")
        return
    info = su.get_stock_info(symbol)
    if not info:
        await interaction.followup.send(f"לא נמצאה מניה בשם `{symbol}`.")
        return

    uid = str(interaction.user.id)
    success = db.sell_stock(uid, info["symbol"], shares, info["price"])
    if not success:
        await interaction.followup.send(f"אין לך מספיק מניות של {info['symbol']} למכירה.")
        return

    await interaction.followup.send(
        f"✅ מכרת {shares} מניות של **{info['symbol']}** במחיר {fmt_money(info['price'])} ליחידה "
        f"(סה\"כ {fmt_money(shares * info['price'])})."
    )


# ---------- 5. /portfolio ----------
@bot.tree.command(name="portfolio", description="הצג את הפורטפוליו שלך")
async def portfolio_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    holdings = db.get_holdings(uid)
    balance = db.get_balance(uid)

    embed = discord.Embed(title=f"פורטפוליו של {interaction.user.display_name}", color=discord.Color.blurple())
    embed.add_field(name="מזומן", value=fmt_money(balance), inline=False)

    total_value = balance
    if not holdings:
        embed.add_field(name="החזקות", value="אין החזקות כרגע.", inline=False)
    else:
        lines = []
        for symbol, shares, avg_price in holdings:
            info = su.get_stock_info(symbol)
            current_price = info["price"] if info else avg_price
            value = shares * current_price
            pl = value - (shares * avg_price)
            pl_sign = "+" if pl >= 0 else ""
            total_value += value
            lines.append(
                f"**{symbol}**: {shares} מניות | מחיר נוכחי {fmt_money(current_price)} | "
                f"שווי {fmt_money(value)} | רווח/הפסד {pl_sign}{fmt_money(pl)}"
            )
        embed.add_field(name="החזקות", value="\n".join(lines), inline=False)

    embed.add_field(name="שווי כולל", value=fmt_money(total_value), inline=False)
    await interaction.followup.send(embed=embed)


# ---------- 6. /balance ----------
@bot.tree.command(name="balance", description="הצג את יתרת המזומן שלך")
async def balance_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    balance = db.get_balance(uid)
    await interaction.response.send_message(f"💰 היתרה שלך: {fmt_money(balance)}")


# ---------- 7. /leaderboard ----------
@bot.tree.command(name="leaderboard", description="טבלת המובילים לפי שווי פורטפוליו כולל")
async def leaderboard_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    user_ids = db.get_all_user_ids()
    rows = []
    for uid in user_ids:
        balance = db.get_balance(uid)
        holdings = db.get_holdings(uid)
        total = balance
        for symbol, shares, avg_price in holdings:
            info = su.get_stock_info(symbol)
            price = info["price"] if info else avg_price
            total += shares * price
        rows.append((uid, total))

    rows.sort(key=lambda r: r[1], reverse=True)
    top = rows[:10]

    if not top:
        await interaction.followup.send("אין עדיין משתמשים עם פורטפוליו.")
        return

    lines = []
    for i, (uid, total) in enumerate(top, start=1):
        try:
            user = await bot.fetch_user(int(uid))
            name = user.display_name if hasattr(user, "display_name") else user.name
        except Exception:
            name = f"משתמש {uid}"
        lines.append(f"{i}. **{name}** — {fmt_money(total)}")

    embed = discord.Embed(title="🏆 טבלת המובילים", description="\n".join(lines), color=discord.Color.gold())
    await interaction.followup.send(embed=embed)


# ---------- 8-10. /watchlist ----------
@bot.tree.command(name="watchlist_add", description="הוסף מניה לרשימת המעקב שלך")
@app_commands.describe(symbol="סימול המניה")
async def watchlist_add_cmd(interaction: discord.Interaction, symbol: str):
    uid = str(interaction.user.id)
    db.add_watchlist(uid, symbol.upper())
    await interaction.response.send_message(f"➕ {symbol.upper()} נוספה לרשימת המעקב שלך.")


@bot.tree.command(name="watchlist_remove", description="הסר מניה מרשימת המעקב שלך")
@app_commands.describe(symbol="סימול המניה")
async def watchlist_remove_cmd(interaction: discord.Interaction, symbol: str):
    uid = str(interaction.user.id)
    db.remove_watchlist(uid, symbol.upper())
    await interaction.response.send_message(f"➖ {symbol.upper()} הוסרה מרשימת המעקב שלך.")


@bot.tree.command(name="watchlist", description="הצג את רשימת המעקב שלך עם מחירים נוכחיים")
async def watchlist_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    symbols = db.get_watchlist(uid)
    if not symbols:
        await interaction.followup.send("רשימת המעקב שלך ריקה. השתמש ב-/watchlist_add כדי להוסיף מניות.")
        return

    lines = []
    for sym in symbols:
        info = su.get_stock_info(sym)
        if info and info["change_pct"] is not None:
            sign = "+" if info["change_pct"] >= 0 else ""
            lines.append(f"**{sym}**: {fmt_money(info['price'])} ({sign}{info['change_pct']:.2f}%)")
        else:
            lines.append(f"**{sym}**: לא זמין")

    embed = discord.Embed(title="👀 רשימת המעקב שלך", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.followup.send(embed=embed)


# ---------- 11. /compare ----------
@bot.tree.command(name="compare", description="השווה בין שתי מניות")
@app_commands.describe(symbol1="מניה ראשונה", symbol2="מניה שנייה")
async def compare_cmd(interaction: discord.Interaction, symbol1: str, symbol2: str):
    await interaction.response.defer()
    info1 = su.get_stock_info(symbol1)
    info2 = su.get_stock_info(symbol2)
    if not info1 or not info2:
        await interaction.followup.send("אחת מהמניות לא נמצאה.")
        return

    embed = discord.Embed(title=f"{info1['symbol']} מול {info2['symbol']}", color=discord.Color.blurple())
    for info in (info1, info2):
        sign = "+" if (info["change_pct"] or 0) >= 0 else ""
        chg = f"{sign}{info['change_pct']:.2f}%" if info["change_pct"] is not None else "N/A"
        embed.add_field(
            name=f"{info['name']} ({info['symbol']})",
            value=f"מחיר: {fmt_money(info['price'])}\nשינוי יומי: {chg}",
            inline=True
        )
    await interaction.followup.send(embed=embed)


# ---------- 12. /news ----------
@bot.tree.command(name="news", description="הצג חדשות אחרונות על מניה")
@app_commands.describe(symbol="סימול המניה")
async def news_cmd(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()
    news_items = su.get_news(symbol)
    if not news_items:
        await interaction.followup.send(f"לא נמצאו חדשות עבור `{symbol}`.")
        return

    lines = []
    for item in news_items:
        if item.get("link"):
            lines.append(f"• [{item['title']}]({item['link']})")
        else:
            lines.append(f"• {item['title']}")

    embed = discord.Embed(title=f"📰 חדשות עבור {symbol.upper()}", description="\n".join(lines),
                           color=discord.Color.blurple())
    await interaction.followup.send(embed=embed)


# ---------- 13. /gainers ----------
@bot.tree.command(name="gainers", description="המניות עם העלייה הגדולה ביותר היום (מתוך רשימת מניות פופולריות)")
async def gainers_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    results = su.scan_movers(su.POPULAR_TICKERS)
    results.sort(key=lambda r: r[2], reverse=True)
    top = results[:5]
    lines = [f"**{sym}**: {fmt_money(price)} (+{pct:.2f}%)" for sym, price, pct in top]
    embed = discord.Embed(title="📈 העולות ביותר היום", description="\n".join(lines) or "אין נתונים",
                           color=discord.Color.green())
    await interaction.followup.send(embed=embed)


# ---------- 14. /losers ----------
@bot.tree.command(name="losers", description="המניות עם הירידה הגדולה ביותר היום (מתוך רשימת מניות פופולריות)")
async def losers_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    results = su.scan_movers(su.POPULAR_TICKERS)
    results.sort(key=lambda r: r[2])
    top = results[:5]
    lines = [f"**{sym}**: {fmt_money(price)} ({pct:.2f}%)" for sym, price, pct in top]
    embed = discord.Embed(title="📉 היורדות ביותר היום", description="\n".join(lines) or "אין נתונים",
                           color=discord.Color.red())
    await interaction.followup.send(embed=embed)


# ---------- 15. /history ----------
@bot.tree.command(name="history", description="הצג את היסטוריית העסקאות שלך")
async def history_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    rows = db.get_history(uid, limit=10)
    if not rows:
        await interaction.response.send_message("עדיין לא ביצעת עסקאות.")
        return

    lines = []
    for symbol, action, shares, price, timestamp in rows:
        emoji = "🟢" if action == "BUY" else "🔴"
        date = timestamp.split("T")[0]
        lines.append(f"{emoji} {action} {shares} {symbol} @ {fmt_money(price)} ({date})")

    embed = discord.Embed(title="📜 היסטוריית עסקאות", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed)


# ---------- בונוס: /reset ----------
@bot.tree.command(name="reset", description="אפס את הפורטפוליו שלך ליתרת ההתחלה")
async def reset_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    db.reset_user(uid)
    await interaction.response.send_message(f"🔄 הפורטפוליו שלך אופס. יתרה חדשה: {fmt_money(db.STARTING_BALANCE)}")


# ---------- /fundamentals ----------
@bot.tree.command(name="fundamentals", description="נתונים פונדמנטליים על מניה: P/E, EPS, דיבידנד, טווח 52 שבועות")
@app_commands.describe(symbol="סימול המניה")
async def fundamentals_cmd(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()
    info = su.get_stock_info(symbol)
    if not info:
        await interaction.followup.send(f"לא נמצאה מניה בשם `{symbol}`.")
        return

    embed = discord.Embed(title=f"📊 נתונים פונדמנטליים - {info['name']} ({info['symbol']})",
                           color=color_for(info["change_pct"]))
    embed.add_field(name="מחיר נוכחי", value=fmt_money(info["price"]), inline=True)
    embed.add_field(name="שווי שוק", value=f"${info['market_cap']:,}" if info["market_cap"] else "N/A", inline=True)
    embed.add_field(name="יחס P/E", value=f"{info['pe_ratio']:.2f}" if info["pe_ratio"] else "N/A", inline=True)
    embed.add_field(name="EPS", value=f"{info['eps']:.2f}" if info["eps"] else "N/A", inline=True)
    div = f"{info['dividend_yield']:.2f}%" if info["dividend_yield"] else "N/A"
    embed.add_field(name="תשואת דיבידנד", value=div, inline=True)
    if info["week52_high"] and info["week52_low"]:
        embed.add_field(name="טווח 52 שבועות",
                         value=f"{fmt_money(info['week52_low'])} - {fmt_money(info['week52_high'])}", inline=True)
    await interaction.followup.send(embed=embed)


# ---------- /alert_add, /alert_list, /alert_remove ----------
@bot.tree.command(name="alert_add", description="הגדר התראת מחיר על מניה או קריפטו")
@app_commands.describe(symbol="סימול המניה/קריפטו", target_price="מחיר היעד", direction="כיוון ההתראה")
@app_commands.choices(direction=[
    app_commands.Choice(name="מעל המחיר (Above)", value="above"),
    app_commands.Choice(name="מתחת למחיר (Below)", value="below"),
])
async def alert_add_cmd(interaction: discord.Interaction, symbol: str, target_price: float,
                         direction: app_commands.Choice[str]):
    await interaction.response.defer()
    info = su.get_stock_info(symbol)
    if not info:
        await interaction.followup.send(f"לא נמצא סימול בשם `{symbol}`.")
        return

    uid = str(interaction.user.id)
    alert_id = db.add_alert(uid, info["symbol"], target_price, direction.value)
    dir_word = "מעל" if direction.value == "above" else "מתחת ל"
    await interaction.followup.send(
        f"🔔 התראה #{alert_id} נוצרה: אודיע לך כש-**{info['symbol']}** יגיע {dir_word} {fmt_money(target_price)} "
        f"(מחיר נוכחי: {fmt_money(info['price'])}). הבדיקה מתבצעת כל 5 דקות, וההתראה תישלח אליך בהודעה פרטית."
    )


@bot.tree.command(name="alert_list", description="הצג את כל התראות המחיר הפעילות שלך")
async def alert_list_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    alerts = db.get_user_alerts(uid)
    if not alerts:
        await interaction.response.send_message("אין לך התראות פעילות כרגע.")
        return

    lines = []
    for alert_id, symbol, target_price, direction in alerts:
        dir_word = "מעל" if direction == "above" else "מתחת ל"
        lines.append(f"#{alert_id}: **{symbol}** {dir_word} {fmt_money(target_price)}")

    embed = discord.Embed(title="🔔 ההתראות הפעילות שלך", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="alert_remove", description="בטל התראת מחיר לפי מספר")
@app_commands.describe(alert_id="מספר ההתראה (מופיע ב-/alert_list)")
async def alert_remove_cmd(interaction: discord.Interaction, alert_id: int):
    uid = str(interaction.user.id)
    success = db.remove_alert(uid, alert_id)
    if success:
        await interaction.response.send_message(f"🗑️ התראה #{alert_id} בוטלה.")
    else:
        await interaction.response.send_message(f"לא נמצאה התראה #{alert_id} השייכת לך.")


# ==================== שכבה 2: נתוני שוק רחבים ====================

@bot.tree.command(name="market_status", description="בדוק אם שוק המניות האמריקאי פתוח כרגע")
async def market_status_cmd(interaction: discord.Interaction):
    is_open, ny_time = su.get_market_status()
    status = "🟢 פתוח" if is_open else "🔴 סגור"
    await interaction.response.send_message(
        f"**סטטוס שוק ה-NYSE:** {status}\nשעה בניו יורק (קירוב): {ny_time.strftime('%H:%M, %A')}"
    )


@bot.tree.command(name="indices", description="הצג את המדדים המרכזיים: S&P 500, דאו ג'ונס, נאסד\"ק")
async def indices_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    results = su.get_index_snapshot()
    if not results:
        await interaction.followup.send("לא הצלחתי למשוך נתוני מדדים כרגע.")
        return
    lines = []
    for name, price, pct in results:
        sign = "+" if (pct or 0) >= 0 else ""
        lines.append(f"**{name}**: {price:,.2f} ({sign}{pct:.2f}%)")
    embed = discord.Embed(title="📊 מדדים מרכזיים", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="forex", description="בדוק שער חליפין בין שני מטבעות, למשל USD/ILS")
@app_commands.describe(pair="זוג המטבעות בפורמט BASE/QUOTE, למשל USD/ILS")
async def forex_cmd(interaction: discord.Interaction, pair: str):
    await interaction.response.defer()
    ticker = su.forex_ticker(pair)
    info = su.get_stock_info(ticker)
    if not info:
        await interaction.followup.send(f"לא נמצא שער עבור `{pair}`. נסה פורמט כמו USD/ILS.")
        return
    await interaction.followup.send(f"💱 **{pair.upper()}**: {info['price']:.4f}")


@bot.tree.command(name="commodities", description="הצג מחירי סחורות: זהב, כסף, נפט, גז טבעי")
async def commodities_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    results = su.get_commodities_snapshot()
    if not results:
        await interaction.followup.send("לא הצלחתי למשוך נתוני סחורות כרגע.")
        return
    lines = []
    for name, price, pct in results:
        sign = "+" if (pct or 0) >= 0 else ""
        lines.append(f"**{name}**: {fmt_money(price)} ({sign}{pct:.2f}%)")
    embed = discord.Embed(title="🛢️ סחורות", description="\n".join(lines), color=discord.Color.gold())
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="vix", description="הצג את מדד התנודתיות VIX")
async def vix_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    info = su.get_stock_info(su.VIX_TICKER)
    if not info:
        await interaction.followup.send("לא הצלחתי למשוך את נתוני ה-VIX כרגע.")
        return
    sign = "+" if (info["change_pct"] or 0) >= 0 else ""
    await interaction.followup.send(
        f"😨 **VIX (מדד הפחד)**: {info['price']:.2f} ({sign}{info['change_pct']:.2f}%)\n"
        f"ערך גבוה = תנודתיות ופחד גבוהים בשוק."
    )


@bot.tree.command(name="earnings", description="הצג את תאריך הדוח הרבעוני הקרוב של מניה")
@app_commands.describe(symbol="סימול המניה")
async def earnings_cmd(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()
    date = su.get_earnings_date(symbol)
    if not date:
        await interaction.followup.send(f"לא נמצא תאריך דוח רבעוני עבור `{symbol}`.")
        return
    await interaction.followup.send(f"📅 הדוח הרבעוני הקרוב של **{symbol.upper()}**: {date}")


@bot.tree.command(name="sector", description="הצג את הענף והתעשייה של מניה")
@app_commands.describe(symbol="סימול המניה")
async def sector_cmd(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()
    data = su.get_sector_info(symbol)
    if not data or not data.get("sector"):
        await interaction.followup.send(f"לא נמצא מידע ענפי עבור `{symbol}`.")
        return
    embed = discord.Embed(title=f"🏭 {symbol.upper()} - מידע ענפי", color=discord.Color.blurple())
    embed.add_field(name="ענף", value=data.get("sector") or "N/A", inline=True)
    embed.add_field(name="תעשייה", value=data.get("industry") or "N/A", inline=True)
    embed.add_field(name="מדינה", value=data.get("country") or "N/A", inline=True)
    if data.get("employees"):
        embed.add_field(name="עובדים", value=f"{data['employees']:,}", inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="analyst_rating", description="הצג המלצות ויעדי מחיר של אנליסטים")
@app_commands.describe(symbol="סימול המניה")
async def analyst_rating_cmd(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()
    data = su.get_analyst_rating(symbol)
    if not data or not data.get("recommendation"):
        await interaction.followup.send(f"לא נמצאו המלצות אנליסטים עבור `{symbol}`.")
        return
    embed = discord.Embed(title=f"🎯 המלצות אנליסטים - {symbol.upper()}", color=discord.Color.blurple())
    embed.add_field(name="המלצה", value=data["recommendation"].upper(), inline=True)
    if data.get("num_analysts"):
        embed.add_field(name="מספר אנליסטים", value=str(data["num_analysts"]), inline=True)
    if data.get("target_mean"):
        embed.add_field(name="יעד מחיר ממוצע", value=fmt_money(data["target_mean"]), inline=True)
    if data.get("target_low") and data.get("target_high"):
        embed.add_field(name="טווח יעדים",
                         value=f"{fmt_money(data['target_low'])} - {fmt_money(data['target_high'])}", inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="split_history", description="הצג היסטוריית פיצולי מניה")
@app_commands.describe(symbol="סימול המניה")
async def split_history_cmd(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()
    splits = su.get_splits(symbol)
    if not splits:
        await interaction.followup.send(f"לא נמצאה היסטוריית פיצולים עבור `{symbol}`.")
        return
    lines = [f"{date}: יחס {ratio}:1" for date, ratio in splits]
    embed = discord.Embed(title=f"✂️ היסטוריית פיצולים - {symbol.upper()}", description="\n".join(lines),
                           color=discord.Color.blurple())
    await interaction.followup.send(embed=embed)


# ==================== שכבה 4: מחשבונים פיננסיים ====================

@bot.tree.command(name="compound_calculator", description="חשב ריבית דריבית על השקעה")
@app_commands.describe(principal="סכום התחלתי", annual_rate="ריבית שנתית באחוזים", years="מספר שנים")
async def compound_calculator_cmd(interaction: discord.Interaction, principal: float, annual_rate: float, years: int):
    rate = annual_rate / 100
    final_amount = principal * ((1 + rate) ** years)
    profit = final_amount - principal
    embed = discord.Embed(title="🧮 מחשבון ריבית דריבית", color=discord.Color.green())
    embed.add_field(name="סכום התחלתי", value=fmt_money(principal), inline=True)
    embed.add_field(name="ריבית שנתית", value=f"{annual_rate}%", inline=True)
    embed.add_field(name="תקופה", value=f"{years} שנים", inline=True)
    embed.add_field(name="סכום סופי", value=fmt_money(final_amount), inline=True)
    embed.add_field(name="רווח", value=fmt_money(profit), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dca_calculator", description="סימולציה: כמה הייתי מרוויח אם הייתי משקיע סכום קבוע כל חודש")
@app_commands.describe(symbol="סימול המניה", monthly_amount="סכום חודשי", years="מספר שנים אחורה")
async def dca_calculator_cmd(interaction: discord.Interaction, symbol: str, monthly_amount: float, years: int):
    await interaction.response.defer()
    result = su.simulate_dca(symbol, monthly_amount, years)
    if not result:
        await interaction.followup.send(f"לא הצלחתי לחשב סימולציה עבור `{symbol}`.")
        return
    sign = "+" if result["profit"] >= 0 else ""
    embed = discord.Embed(title=f"📈 סימולציית DCA - {symbol.upper()}", color=discord.Color.green())
    embed.add_field(name="סה\"כ הושקע", value=fmt_money(result["total_invested"]), inline=True)
    embed.add_field(name="שווי סופי", value=fmt_money(result["final_value"]), inline=True)
    embed.add_field(name="רווח/הפסד", value=f"{sign}{fmt_money(result['profit'])} ({sign}{result['return_pct']:.1f}%)",
                     inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="simulate_growth", description="כמה היה שווה סכום שהושקע במניה לפני N שנים")
@app_commands.describe(symbol="סימול המניה", amount="סכום ההשקעה", years="לפני כמה שנים")
async def simulate_growth_cmd(interaction: discord.Interaction, symbol: str, amount: float, years: int):
    await interaction.response.defer()
    result = su.simulate_historical_growth(symbol, amount, years)
    if not result:
        await interaction.followup.send(f"לא הצלחתי לחשב סימולציה עבור `{symbol}`.")
        return
    sign = "+" if result["profit"] >= 0 else ""
    embed = discord.Embed(title=f"⏳ אילו השקעת {fmt_money(amount)} ב-{symbol.upper()} לפני {years} שנים",
                           color=discord.Color.green())
    embed.add_field(name="מחיר אז", value=fmt_money(result["start_price"]), inline=True)
    embed.add_field(name="מחיר היום", value=fmt_money(result["end_price"]), inline=True)
    embed.add_field(name="שווי היום", value=fmt_money(result["final_value"]), inline=True)
    embed.add_field(name="רווח/הפסד", value=f"{sign}{fmt_money(result['profit'])} ({sign}{result['return_pct']:.1f}%)",
                     inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="retirement_calculator", description="חשב חיסכון עתידי לפרישה")
@app_commands.describe(current_savings="חיסכון נוכחי", monthly_contribution="הפקדה חודשית",
                        annual_rate="תשואה שנתית משוערת באחוזים", years="שנים עד הפרישה")
async def retirement_calculator_cmd(interaction: discord.Interaction, current_savings: float,
                                     monthly_contribution: float, annual_rate: float, years: int):
    rate = annual_rate / 100
    months = years * 12
    monthly_rate = rate / 12
    future_value = current_savings * ((1 + monthly_rate) ** months)
    if monthly_rate > 0:
        future_value += monthly_contribution * (((1 + monthly_rate) ** months - 1) / monthly_rate)
    else:
        future_value += monthly_contribution * months
    total_contributed = current_savings + (monthly_contribution * months)

    embed = discord.Embed(title="🏖️ מחשבון חיסכון לפרישה", color=discord.Color.green())
    embed.add_field(name="חיסכון נוכחי", value=fmt_money(current_savings), inline=True)
    embed.add_field(name="הפקדה חודשית", value=fmt_money(monthly_contribution), inline=True)
    embed.add_field(name="תקופה", value=f"{years} שנים", inline=True)
    embed.add_field(name="סה\"כ יופקד", value=fmt_money(total_contributed), inline=True)
    embed.add_field(name="שווי צפוי בפרישה", value=fmt_money(future_value), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="loan_calculator", description="חשב החזר חודשי על הלוואה")
@app_commands.describe(loan_amount="סכום ההלוואה", annual_rate="ריבית שנתית באחוזים", years="תקופת ההלוואה בשנים")
async def loan_calculator_cmd(interaction: discord.Interaction, loan_amount: float, annual_rate: float, years: int):
    monthly_rate = (annual_rate / 100) / 12
    months = years * 12
    if monthly_rate > 0:
        monthly_payment = loan_amount * (monthly_rate * (1 + monthly_rate) ** months) / \
                           ((1 + monthly_rate) ** months - 1)
    else:
        monthly_payment = loan_amount / months
    total_paid = monthly_payment * months
    total_interest = total_paid - loan_amount

    embed = discord.Embed(title="🏦 מחשבון הלוואה", color=discord.Color.blurple())
    embed.add_field(name="סכום הלוואה", value=fmt_money(loan_amount), inline=True)
    embed.add_field(name="ריבית שנתית", value=f"{annual_rate}%", inline=True)
    embed.add_field(name="תקופה", value=f"{years} שנים", inline=True)
    embed.add_field(name="החזר חודשי", value=fmt_money(monthly_payment), inline=True)
    embed.add_field(name="סה\"כ ריבית", value=fmt_money(total_interest), inline=True)
    embed.add_field(name="סה\"כ יוחזר", value=fmt_money(total_paid), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="goal_tracker", description="חשב כמה צריך לחסוך כל חודש כדי להגיע ליעד השקעה")
@app_commands.describe(target_amount="סכום היעד", current_savings="חיסכון נוכחי",
                        annual_rate="תשואה שנתית משוערת באחוזים", years="תוך כמה שנים")
async def goal_tracker_cmd(interaction: discord.Interaction, target_amount: float, current_savings: float,
                            annual_rate: float, years: int):
    monthly_rate = (annual_rate / 100) / 12
    months = years * 12
    future_of_current = current_savings * ((1 + monthly_rate) ** months)
    remaining_goal = target_amount - future_of_current

    if remaining_goal <= 0:
        await interaction.response.send_message(
            f"🎉 החיסכון הנוכחי שלך כבר מספיק! בתשואה של {annual_rate}% הוא יגדל ל-{fmt_money(future_of_current)} "
            f"תוך {years} שנים, מעל היעד של {fmt_money(target_amount)}."
        )
        return

    if monthly_rate > 0:
        required_monthly = remaining_goal * monthly_rate / (((1 + monthly_rate) ** months) - 1)
    else:
        required_monthly = remaining_goal / months

    embed = discord.Embed(title="🎯 מחשבון יעד חיסכון", color=discord.Color.green())
    embed.add_field(name="יעד", value=fmt_money(target_amount), inline=True)
    embed.add_field(name="חיסכון נוכחי", value=fmt_money(current_savings), inline=True)
    embed.add_field(name="תקופה", value=f"{years} שנים", inline=True)
    embed.add_field(name="נדרש להפקיד כל חודש", value=fmt_money(required_monthly), inline=False)
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("חסר DISCORD_TOKEN בקובץ .env")
    keep_alive()
    bot.run(TOKEN)
