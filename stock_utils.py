import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

POPULAR_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD",
    "NFLX", "INTC", "DIS", "BA", "KO", "PEP", "NKE", "PFE",
    "XOM", "JPM", "V", "WMT"
]

VALID_PERIODS = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "5y", "max"]

INDICES = {"S&P 500": "^GSPC", "Dow Jones": "^DJI", "Nasdaq": "^IXIC", "Russell 2000": "^RUT"}
COMMODITIES = {"זהב": "GC=F", "כסף": "SI=F", "נפט (WTI)": "CL=F", "גז טבעי": "NG=F"}
VIX_TICKER = "^VIX"


def get_index_snapshot():
    results = []
    for name, ticker in INDICES.items():
        info = get_stock_info(ticker)
        if info:
            results.append((name, info["price"], info["change_pct"]))
    return results


def get_commodities_snapshot():
    results = []
    for name, ticker in COMMODITIES.items():
        info = get_stock_info(ticker)
        if info:
            results.append((name, info["price"], info["change_pct"]))
    return results


def forex_ticker(pair: str) -> str:
    parts = pair.upper().replace(" ", "").split("/")
    if len(parts) != 2:
        return pair.upper() + "=X"
    base, quote = parts
    if base == "USD":
        return f"{quote}=X"
    return f"{base}{quote}=X"


def get_market_status():
    import datetime
    now_utc = datetime.datetime.utcnow()
    ny_offset = -4
    ny_time = now_utc + datetime.timedelta(hours=ny_offset)
    is_weekday = ny_time.weekday() < 5
    open_time = ny_time.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = ny_time.replace(hour=16, minute=0, second=0, microsecond=0)
    is_open = is_weekday and open_time <= ny_time <= close_time
    return is_open, ny_time


CRYPTO_ALIASES = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "DOGE": "DOGE-USD", "SOL": "SOL-USD",
    "ADA": "ADA-USD", "XRP": "XRP-USD", "BNB": "BNB-USD", "LTC": "LTC-USD",
    "MATIC": "MATIC-USD", "AVAX": "AVAX-USD", "DOT": "DOT-USD", "SHIB": "SHIB-USD",
}


def resolve_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    return CRYPTO_ALIASES.get(s, s)


def get_ticker(symbol: str):
    return yf.Ticker(resolve_symbol(symbol))


def get_stock_info(symbol: str):
    resolved = resolve_symbol(symbol)
    t = get_ticker(resolved)
    try:
        fast = t.fast_info
        price = fast.get("lastPrice") or fast.get("last_price")
        prev_close = fast.get("previousClose") or fast.get("previous_close")
        if price is None:
            return None
    except Exception:
        return None

    try:
        info = t.info
        name = info.get("longName") or info.get("shortName") or resolved
        market_cap = info.get("marketCap")
        currency = info.get("currency", "USD")
        pe_ratio = info.get("trailingPE")
        eps = info.get("trailingEps")
        dividend_yield = info.get("dividendYield")
        week52_high = info.get("fiftyTwoWeekHigh")
        week52_low = info.get("fiftyTwoWeekLow")
    except Exception:
        name = resolved
        market_cap = pe_ratio = eps = dividend_yield = week52_high = week52_low = None
        currency = "USD"

    change = None
    change_pct = None
    if price is not None and prev_close:
        change = price - prev_close
        change_pct = (change / prev_close) * 100

    return {
        "symbol": resolved,
        "name": name,
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "market_cap": market_cap,
        "currency": currency,
        "pe_ratio": pe_ratio,
        "eps": eps,
        "dividend_yield": dividend_yield,
        "week52_high": week52_high,
        "week52_low": week52_low,
    }


def get_chart_image(symbol: str, period: str = "1mo"):
    t = get_ticker(symbol)
    interval = "1d"
    if period in ("1d",):
        interval = "5m"
    elif period in ("5d",):
        interval = "30m"

    hist = t.history(period=period, interval=interval)
    if hist.empty:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=130)
    color = "#2ecc71" if hist["Close"].iloc[-1] >= hist["Close"].iloc[0] else "#e74c3c"
    ax.plot(hist.index, hist["Close"], color=color, linewidth=1.8)
    ax.fill_between(hist.index, hist["Close"], hist["Close"].min(), color=color, alpha=0.08)

    ax.set_title(f"{symbol.upper()} - {period}", fontsize=13, fontweight="bold")
    ax.set_ylabel("מחיר")
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def get_news(symbol: str, limit: int = 5):
    t = get_ticker(symbol)
    try:
        news_items = t.news or []
    except Exception:
        return []
    results = []
    for item in news_items[:limit]:
        content = item.get("content", item)
        title = content.get("title") or item.get("title")
        link = (content.get("canonicalUrl") or {}).get("url") or item.get("link")
        if title:
            results.append({"title": title, "link": link})
    return results


def get_sector_info(symbol: str):
    t = get_ticker(symbol)
    try:
        info = t.info
        return {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "employees": info.get("fullTimeEmployees"),
            "country": info.get("country"),
        }
    except Exception:
        return None


def get_earnings_date(symbol: str):
    t = get_ticker(symbol)
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date")
            if dates:
                return dates[0]
        return None
    except Exception:
        return None


def get_analyst_rating(symbol: str):
    t = get_ticker(symbol)
    try:
        info = t.info
        return {
            "recommendation": info.get("recommendationKey"),
            "num_analysts": info.get("numberOfAnalystOpinions"),
            "target_mean": info.get("targetMeanPrice"),
            "target_high": info.get("targetHighPrice"),
            "target_low": info.get("targetLowPrice"),
        }
    except Exception:
        return None


def get_splits(symbol: str, limit: int = 5):
    t = get_ticker(symbol)
    try:
        splits = t.splits
        if splits.empty:
            return []
        recent = splits.tail(limit)
        return [(str(date.date()), ratio) for date, ratio in recent.items()]
    except Exception:
        return []


def get_daily_change(symbol: str):
    info = get_stock_info(symbol)
    if not info or info["change_pct"] is None:
        return None
    return (info["price"], info["change_pct"])


def scan_movers(tickers):
    results = []
    for sym in tickers:
        data = get_daily_change(sym)
        if data:
            price, pct = data
            results.append((sym, price, pct))
    return results


def simulate_historical_growth(symbol: str, amount: float, years: int):
    t = get_ticker(symbol)
    period = f"{years}y" if years <= 10 else "max"
    try:
        hist = t.history(period=period)
        if hist.empty:
            return None
        start_price = hist["Close"].iloc[0]
        end_price = hist["Close"].iloc[-1]
        shares = amount / start_price
        final_value = shares * end_price
        return {
            "start_price": start_price,
            "end_price": end_price,
            "final_value": final_value,
            "profit": final_value - amount,
            "return_pct": ((final_value - amount) / amount) * 100,
        }
    except Exception:
        return None


def simulate_dca(symbol: str, monthly_amount: float, years: int):
    t = get_ticker(symbol)
    period = f"{years}y" if years <= 10 else "max"
    try:
        hist = t.history(period=period, interval="1mo")
        if hist.empty:
            return None
        total_invested = 0.0
        total_shares = 0.0
        for _, row in hist.iterrows():
            price = row["Close"]
            if price and price > 0:
                total_shares += monthly_amount / price
                total_invested += monthly_amount
        final_price = hist["Close"].iloc[-1]
        final_value = total_shares * final_price
        return {
            "total_invested": total_invested,
            "final_value": final_value,
            "profit": final_value - total_invested,
            "return_pct": ((final_value - total_invested) / total_invested) * 100 if total_invested else 0,
            "months": len(hist),
        }
    except Exception:
        return None
