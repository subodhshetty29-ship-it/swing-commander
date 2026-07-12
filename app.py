import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import ta
from datetime import datetime, timedelta
import time
import requests
import json
import uuid
from pathlib import Path

# ========== PAGE CONFIG ==========
st.set_page_config(
    page_title="Pro Swing Commander",
    page_icon="📈",
    layout="wide"
)

# ========== PERSISTENCE ==========
# FIX: session_state alone loses your journal when the browser session ends.
# Trades are now saved to a JSON file and reloaded on startup.
# NOTE: on Streamlit Community Cloud the file survives reruns but NOT container
# restarts/redeploys. For true permanence, swap save/load for Google Sheets or a DB.
TRADES_FILE = Path("trades.json")

def load_trades():
    if TRADES_FILE.exists():
        try:
            with open(TRADES_FILE, "r") as f:
                data = json.load(f)
            return data.get("active", []), data.get("history", [])
        except (json.JSONDecodeError, OSError):
            return [], []
    return [], []

def save_trades():
    try:
        with open(TRADES_FILE, "w") as f:
            json.dump({
                "active": st.session_state.active_trades,
                "history": st.session_state.trade_history
            }, f, indent=2, default=str)
    except OSError as e:
        st.warning(f"Could not save trades to disk: {e}")

# ========== INITIALIZE SESSION STATE ==========
def init_session():
    if 'active_trades' not in st.session_state:
        active, history = load_trades()
        st.session_state.active_trades = active
        st.session_state.trade_history = history
    if 'scan_results' not in st.session_state:
        st.session_state.scan_results = []
    if 'scan_errors' not in st.session_state:
        st.session_state.scan_errors = []
    if 'closing_trade' not in st.session_state:
        st.session_state.closing_trade = None  # stores trade id, not list index
    if 'pending_ticker' not in st.session_state:
        st.session_state.pending_ticker = None

init_session()

# FIX: quick-pick buttons and the sidebar search box now share one source of
# truth. Buttons set pending_ticker; we apply it here BEFORE the text_input
# widget is created (Streamlit forbids modifying a widget's state after it
# renders in the same run).
if st.session_state.pending_ticker:
    st.session_state.ticker_input = st.session_state.pending_ticker
    st.session_state.pending_ticker = None

# ========== SIDEBAR ==========
st.sidebar.title("⚙️ Pro Settings")

ticker = st.sidebar.text_input("Search Stock", value="AAPL", key="ticker_input").upper().strip()

period_map = {
    "1 Month": "1mo",
    "3 Months": "3mo",
    "6 Months": "6mo",
    "1 Year": "1y",
    "2 Years": "2y",
    "5 Years": "5y"
}
selected_period = st.sidebar.selectbox("Time Range", list(period_map.keys()), index=3)
period = period_map[selected_period]

st.sidebar.markdown("---")
st.sidebar.subheader("💰 Position Sizing")
account_size = st.sidebar.number_input(
    "Account Size ($)", min_value=100, max_value=10_000_000, value=50_000, step=1000
)
risk_percent = st.sidebar.slider(
    "Risk per Trade (%)", min_value=0.5, max_value=5.0, value=1.0, step=0.1
)
atr_multiplier = st.sidebar.slider(
    "Stop Loss (ATR x)", min_value=0.5, max_value=3.0, value=2.0, step=0.5
)

st.sidebar.markdown("---")
st.sidebar.subheader("🔍 Scanner Filters")
min_score = st.sidebar.slider(
    "Minimum Score", min_value=30, max_value=80, value=50, step=5
)
st.sidebar.caption("📊 Data from Yahoo Finance")

# ========== TABS ==========
tab1, tab2, tab3 = st.tabs(["📊 Pro Scanner", "📈 Active Trades", "📓 Journal"])

# ========== INDICATOR HELPERS ==========
def add_indicators(df):
    """Compute all indicators on a raw OHLCV frame. Used by BOTH the deep
    dive and the scanner (this was the original scanner-killing bug: the
    scanner referenced SMA/ATR columns that were never computed)."""
    df = df.copy()
    df['SMA_20'] = df['Close'].rolling(20).mean()
    df['SMA_50'] = df['Close'].rolling(50).mean()
    df['SMA_200'] = df['Close'].rolling(200).mean()
    df['RSI'] = ta.momentum.RSIIndicator(df['Close'], window=14).rsi()

    macd = ta.trend.MACD(df['Close'])
    df['MACD'] = macd.macd()
    df['MACD_Signal'] = macd.macd_signal()
    df['MACD_Hist'] = macd.macd_diff()

    df['ATR'] = ta.volatility.AverageTrueRange(
        df['High'], df['Low'], df['Close'], window=14
    ).average_true_range()
    df['Volume_MA'] = df['Volume'].rolling(20).mean()
    return df

# ========== DATA FETCH (single ticker deep dive) ==========
@st.cache_data(ttl=600)
def fetch_data(ticker, period):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return None, None, f"No data found for '{ticker}'"
        info = stock.info
        df = add_indicators(df)
        return df, info, None
    except Exception as e:
        return None, None, str(e)

# ========== GET SIGNALS ==========
def get_signals(df):
    if df is None or len(df) < 30:
        return ["Not enough data"], "⚪ HOLD", 0

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    signals = []
    score = 0

    if not pd.isna(latest['RSI']):
        if latest['RSI'] < 30:
            signals.append("🟢 RSI Oversold (<30) - BUY Signal")
            score += 1
        elif latest['RSI'] > 70:
            signals.append("🔴 RSI Overbought (>70) - SELL Signal")
            score -= 1
        else:
            signals.append(f"⚪ RSI: {latest['RSI']:.1f}")

    if not pd.isna(latest['SMA_50']):
        if latest['Close'] > latest['SMA_50']:
            signals.append("🟢 Above 50-day MA - Uptrend")
            score += 1
        else:
            signals.append("🔴 Below 50-day MA - Downtrend")
            score -= 1

    # FIX: original labeled this a "crossover" but only checked position
    # (a state). A crossover is an event: today above, yesterday below.
    if not pd.isna(latest['MACD']) and not pd.isna(latest['MACD_Signal']):
        bull_now = latest['MACD'] > latest['MACD_Signal']
        bull_prev = prev['MACD'] > prev['MACD_Signal'] if not pd.isna(prev['MACD']) else bull_now
        if bull_now and not bull_prev:
            signals.append("🟢 MACD Bullish Crossover (fresh)")
            score += 1
        elif bull_now:
            signals.append("🟢 MACD Above Signal (bullish)")
            score += 1
        elif not bull_now and bull_prev:
            signals.append("🔴 MACD Bearish Crossover (fresh)")
            score -= 1
        else:
            signals.append("🔴 MACD Below Signal (bearish)")
            score -= 1

    if score >= 2:
        rec = "🟢 BUY"
    elif score <= -2:
        rec = "🔴 SELL"
    else:
        rec = "⚪ HOLD"

    return signals, rec, score

# ========== STOCK UNIVERSE ==========
# FIX: pruned delisted/acquired/renamed tickers that were silently failing:
# MRO, PXD, NBL (acquired), SI (delisted), DISH->SATS, VIAC (old PARA), SQ->XYZ
@st.cache_data(ttl=3600)
def get_stock_universe():
    return [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'NFLX', 'AMD', 'INTC',
        'ORCL', 'IBM', 'CSCO', 'QCOM', 'TXN', 'AVGO', 'MU', 'LRCX', 'KLAC', 'AMAT',
        'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'V', 'MA', 'PYPL', 'XYZ',
        'AXP', 'COF', 'DFS', 'SYF', 'ALLY', 'USB', 'PNC', 'TFC', 'MTB', 'FITB',
        'JNJ', 'PFE', 'MRK', 'ABBV', 'UNH', 'CVS', 'ABT', 'TMO', 'DHR', 'AMGN',
        'GILD', 'BMY', 'REGN', 'VRTX', 'BIIB', 'ILMN', 'MTD', 'WST', 'ZBH', 'SYK',
        'WMT', 'TGT', 'COST', 'HD', 'LOW', 'MCD', 'SBUX', 'NKE', 'DIS', 'CMCSA',
        'UBER', 'LYFT', 'DASH', 'ETSY', 'CVNA', 'ABNB', 'BKNG', 'EXPE', 'RCL', 'CCL',
        'BA', 'CAT', 'GE', 'DE', 'F', 'GM', 'RTX', 'LMT', 'NOC', 'GD',
        'HON', 'MMM', 'PH', 'EMR', 'ETN', 'ITW', 'CMI', 'PCAR', 'RSG', 'WM',
        'XOM', 'CVX', 'COP', 'PSX', 'VLO', 'MPC', 'EOG', 'FANG',
        'DVN', 'OXY', 'APA', 'HES', 'T', 'VZ', 'TMUS', 'CHTR', 'SATS',
        'ROKU', 'SPOT', 'SIRI', 'AMCX', 'FOXA', 'PARA', 'WBD', 'NYT',
        'NEE', 'DUK', 'SO', 'D', 'AEP', 'EXC', 'SRE', 'PEG', 'PCG', 'ED',
        'PLTR', 'SNOW', 'DDOG', 'MDB', 'ZS', 'NET', 'CRWD', 'PANW', 'FTNT', 'OKTA',
        'SMCI', 'DELL', 'HPQ', 'WDC', 'STX', 'NTAP', 'PSTG', 'AFRM', 'UPST', 'SOFI',
        'HOOD', 'COIN', 'RIOT', 'MARA', 'HUT'
    ]

# ========== GET NEWS ==========
@st.cache_data(ttl=1800)
def get_news(ticker):
    # FIX: Yahoo blocks requests with no User-Agent, so the original almost
    # always returned []. Added headers + timeout.
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}"
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=10,
        )
        if response.status_code == 200:
            news = response.json().get('news', [])
            return [item.get('title', '') for item in news[:5] if item.get('title')]
        return []
    except Exception:
        return []

# ========== FUNDAMENTAL SCORE ==========
def calculate_fundamental_score(info):
    score = 0

    pe = info.get('trailingPE')
    if pe and pe > 0:
        if pe < 15:
            score += 20
        elif pe < 25:
            score += 10
        elif pe < 35:
            score += 5

    roe = info.get('returnOnEquity')
    if roe:
        if roe > 0.25:
            score += 20
        elif roe > 0.15:
            score += 10
        elif roe > 0.10:
            score += 5

    # FIX: yfinance returns debtToEquity as a PERCENT (e.g. 145.2 = 1.45x),
    # so the original thresholds (< 0.3 etc.) never fired. Normalize first.
    debt_to_equity = info.get('debtToEquity')
    if debt_to_equity is not None:
        dte = debt_to_equity / 100.0
        if dte < 0.3:
            score += 15
        elif dte < 0.6:
            score += 8
        elif dte < 1.0:
            score += 3

    profit_margin = info.get('profitMargins')
    if profit_margin:
        if profit_margin > 0.20:
            score += 15
        elif profit_margin > 0.10:
            score += 8
        elif profit_margin > 0.05:
            score += 3

    revenue_growth = info.get('revenueGrowth')
    if revenue_growth:
        if revenue_growth > 0.20:
            score += 15
        elif revenue_growth > 0.10:
            score += 8
        elif revenue_growth > 0.05:
            score += 3

    earnings_growth = info.get('earningsGrowth')
    if earnings_growth:
        if earnings_growth > 0.20:
            score += 15
        elif earnings_growth > 0.10:
            score += 8
        elif earnings_growth > 0.05:
            score += 3

    return min(score, 100)

# ========== CATALYST SCORE ==========
def calculate_catalyst_score(tkr, info):
    score = 0
    signals = []

    news = get_news(tkr)
    if news:
        # Toned down from +20: mega-caps ALWAYS have news, so this is a
        # weak differentiator. Kept as a small nudge only.
        score += 10
        signals.append("Recent News")

    # FIX: 'earningsDate' is not a reliable info key. Use earningsTimestamp
    # and only score it if earnings are actually upcoming (next 21 days).
    ts = info.get('earningsTimestamp')
    if ts:
        try:
            earnings_dt = datetime.fromtimestamp(ts)
            days_out = (earnings_dt - datetime.now()).days
            if 0 <= days_out <= 21:
                score += 20
                signals.append(f"Earnings in {days_out}d")
        except (ValueError, OSError, OverflowError):
            pass

    short_ratio = info.get('shortRatio')
    if short_ratio and short_ratio > 3:
        score += 10
        signals.append("High Short Interest")

    return min(score, 100), signals

# ========== POSITION SIZING ==========
def calc_position(price, atr, account, risk_pct, atr_mult):
    risk_dollars = account * (risk_pct / 100)
    stop_distance = atr * atr_mult

    if stop_distance < price * 0.01:
        stop_distance = price * 0.01

    # Cap the stop at 15% below entry; re-derive shares so $ risk is preserved
    if stop_distance > price * 0.15:
        stop_distance = price * 0.15

    shares = risk_dollars / stop_distance
    shares = round(shares, 2) if shares < 1 else int(shares)

    stop_price = price - stop_distance
    target_price = price + (stop_distance * 2)  # fixed 2R target

    return shares, stop_price, target_price, risk_dollars

# ========== TECH SCORE (used by scanner) ==========
def score_technicals(hist, is_weekend):
    """Score one ticker from an indicator-enriched OHLCV frame.
    Returns (score, signals, metrics) or None if insufficient data."""
    if hist is None or len(hist) < 60:
        return None

    latest = hist.iloc[-1]
    last_5 = hist.tail(5)

    sma_20 = latest['SMA_20']
    sma_50 = latest['SMA_50']
    sma_200 = latest['SMA_200']
    rsi = latest['RSI']
    vol_ma = latest['Volume_MA']
    atr = latest['ATR']

    if pd.isna(sma_20) or pd.isna(sma_50) or pd.isna(rsi):
        return None

    tech_score = 0
    tech_signals = []

    if latest['Close'] > sma_50:
        tech_score += 25
        tech_signals.append("Uptrend")
    elif latest['Close'] > sma_50 * 0.97:
        tech_score += 12
        tech_signals.append("Near MA")

    if latest['Close'] > sma_20:
        tech_score += 15
        tech_signals.append("Momentum")

    if 30 <= rsi <= 65:
        tech_score += 20
        tech_signals.append(f"RSI: {round(rsi)}")
    elif rsi < 70:
        tech_score += 8

    if not is_weekend:
        if vol_ma and vol_ma > 0 and latest['Volume'] > 1.2 * vol_ma:
            tech_score += 15
            tech_signals.append("Volume Surge")
        elif vol_ma and latest['Volume'] > vol_ma * 0.8:
            tech_score += 5
    else:
        tech_score += 10
        tech_signals.append("Weekend Mode")

    if len(last_5) >= 5 and latest['Close'] > last_5['Close'].mean():
        tech_score += 10
        tech_signals.append("Recent Strength")

    if not pd.isna(sma_200) and latest['Close'] > sma_200:
        tech_score += 15
        tech_signals.append("Long-term Trend")

    metrics = {
        'price': latest['Close'],
        'rsi': rsi,
        'vol_surge': (latest['Volume'] / vol_ma) if vol_ma and vol_ma > 0 else 1.0,
        'atr': atr if not pd.isna(atr) else None,
    }
    return min(tech_score, 100), tech_signals, metrics

# ========== PRO SCANNER ==========
# FIX (the big one): the original referenced hist['SMA_20'] etc. on raw
# yfinance data — those columns never existed, every ticker raised KeyError,
# and the bare `except: continue` silently skipped the ENTIRE universe.
#
# New design, also much faster:
#   Stage 1: ONE batched yf.download for all tickers (threads), score
#            technicals locally.
#   Stage 2: fetch slow .info + news ONLY for the top technical candidates
#            (capped), then compute fundamental + catalyst scores.
MAX_INFO_FETCHES = 40  # only hit .info for this many top technical candidates
TECH_CANDIDATE_FLOOR = 40  # min tech score to be considered for stage 2

def pro_scan(min_score):
    universe = get_stock_universe()
    is_weekend = datetime.now().weekday() >= 5
    errors = []

    status_text = st.empty()
    progress_bar = st.progress(0)

    # ---- Stage 1: batched price download + technical scoring ----
    status_text.text(f"Downloading price history for {len(universe)} tickers...")
    try:
        raw = yf.download(
            universe, period="1y", group_by='ticker',
            auto_adjust=True, threads=True, progress=False
        )
    except Exception as e:
        progress_bar.empty()
        status_text.empty()
        st.error(f"Batch download failed: {e}")
        return [], [str(e)]

    candidates = []
    for i, tkr in enumerate(universe):
        progress_bar.progress((i + 1) / len(universe) * 0.5)
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if tkr not in raw.columns.get_level_values(0):
                    errors.append(f"{tkr}: no data returned")
                    continue
                hist = raw[tkr].dropna(subset=['Close'])
            else:
                hist = raw.dropna(subset=['Close'])  # single-ticker edge case

            if hist.empty or len(hist) < 60:
                errors.append(f"{tkr}: insufficient history")
                continue

            hist = add_indicators(hist)
            result = score_technicals(hist, is_weekend)
            if result is None:
                errors.append(f"{tkr}: indicators unavailable")
                continue

            tech_score, tech_signals, metrics = result
            if tech_score >= TECH_CANDIDATE_FLOOR:
                candidates.append((tkr, tech_score, tech_signals, metrics))
        except Exception as e:
            errors.append(f"{tkr}: {e}")
            continue

    # ---- Stage 2: fundamentals + catalysts for top candidates only ----
    candidates.sort(key=lambda x: x[1], reverse=True)
    candidates = candidates[:MAX_INFO_FETCHES]

    results = []
    for j, (tkr, tech_score, tech_signals, metrics) in enumerate(candidates):
        status_text.text(f"Fundamentals {j+1}/{len(candidates)}: {tkr}")
        progress_bar.progress(0.5 + (j + 1) / max(len(candidates), 1) * 0.5)
        try:
            info = yf.Ticker(tkr).info or {}
        except Exception as e:
            errors.append(f"{tkr}: info fetch failed ({e})")
            info = {}

        fund_score = calculate_fundamental_score(info)
        catalyst_score, catalyst_signals = calculate_catalyst_score(tkr, info)

        final_score = (tech_score * 0.5) + (fund_score * 0.3) + (catalyst_score * 0.2)
        if final_score < min_score:
            continue

        market_cap = info.get('marketCap', 0) or 0
        if market_cap > 1_000_000_000:
            market_cap_display = f"${market_cap / 1_000_000_000:.2f}B"
        elif market_cap > 0:
            market_cap_display = f"${market_cap / 1_000_000:.0f}M"
        else:
            market_cap_display = "N/A"

        results.append({
            'Ticker': tkr,
            'Price': round(metrics['price'], 2),
            'RSI': round(metrics['rsi'], 1),
            'Volume Surge': round(metrics['vol_surge'], 1),
            'Market Cap': market_cap_display,
            'Tech Score': round(tech_score),
            'Fundamental Score': round(fund_score),
            'Catalyst Score': round(catalyst_score),
            'Total Score': round(final_score),
            'Tech Signals': ' | '.join(tech_signals[:3]),
            'Catalyst Signals': ' | '.join(catalyst_signals[:2]) if catalyst_signals else 'None',
            'ATR': round(metrics['atr'], 2) if metrics['atr'] else None,
            'P/E': round(info['trailingPE'], 1) if info.get('trailingPE') else None,
            'ROE': f"{info['returnOnEquity'] * 100:.1f}%" if info.get('returnOnEquity') else None,
        })
        time.sleep(0.05)  # be gentle on Yahoo

    progress_bar.empty()
    status_text.empty()

    results.sort(key=lambda x: x['Total Score'], reverse=True)
    return results, errors

# ========== TAB 1: PRO SCANNER ==========
with tab1:
    st.title("📊 Pro Swing Commander")
    st.markdown("---")

    st.subheader("🔍 Pro Scanner")
    st.caption("Technicals (50%) + Fundamentals (30%) + Catalysts (20%) across the universe")

    scan_btn = st.button("🚀 Run Pro Scan", type="primary")

    if scan_btn:
        with st.spinner("Running pro scan..."):
            results, errors = pro_scan(min_score)
            st.session_state.scan_results = results
            st.session_state.scan_errors = errors

    if st.session_state.scan_results:
        st.success(f"✅ {len(st.session_state.scan_results)} stocks match your criteria (min score: {min_score})")

        df_results = pd.DataFrame(st.session_state.scan_results)
        st.dataframe(
            df_results,
            column_config={
                "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                "Price": st.column_config.NumberColumn("Price", format="$%.2f"),
                "RSI": st.column_config.NumberColumn("RSI", format="%.1f"),
                "Volume Surge": st.column_config.NumberColumn("Vol Surge", format="%.1fx"),
                "Market Cap": st.column_config.TextColumn("Mkt Cap", width="small"),
                "Tech Score": st.column_config.ProgressColumn("Tech", format="%d", min_value=0, max_value=100),
                "Fundamental Score": st.column_config.ProgressColumn("Fund", format="%d", min_value=0, max_value=100),
                "Catalyst Score": st.column_config.ProgressColumn("Catalyst", format="%d", min_value=0, max_value=100),
                "Total Score": st.column_config.ProgressColumn("Total", format="%d", min_value=0, max_value=100),
                "P/E": st.column_config.NumberColumn("P/E", format="%.1f"),
            },
            hide_index=True,
            use_container_width=True
        )

        st.subheader("📈 Quick Analyze")
        top_picks = df_results.head(5)['Ticker'].tolist()
        cols = st.columns(len(top_picks))
        for idx, pick in enumerate(top_picks):
            with cols[idx]:
                if st.button(pick, key=f"quick_{pick}"):
                    st.session_state.pending_ticker = pick
                    st.rerun()

    elif scan_btn:
        st.warning(f"No stocks passed the filters (minimum score: {min_score}). Try lowering it.")

    # Transparency: show what got skipped and why, instead of silently eating errors
    if st.session_state.scan_errors:
        with st.expander(f"⚠️ {len(st.session_state.scan_errors)} tickers skipped during scan"):
            for err in st.session_state.scan_errors:
                st.caption(err)

    st.markdown("---")

    # FIX: the deep dive now actually follows the sidebar search box
    # (previously quick_ticker was always truthy, so the sidebar was dead)
    analysis_ticker = ticker

    if analysis_ticker:
        st.subheader(f"📊 {analysis_ticker} Deep Dive")

        df, info, error = fetch_data(analysis_ticker, period)

        if error:
            st.error(f"❌ {error}")
        else:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            change = latest['Close'] - prev['Close']
            change_pct = (change / prev['Close']) * 100 if prev['Close'] != 0 else 0

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("💰 Price", f"${latest['Close']:.2f}", f"{change:+.2f} ({change_pct:+.2f}%)")
            col2.metric("📊 Volume", f"{latest['Volume']:,.0f}")
            col3.metric("📈 Day High", f"${latest['High']:.2f}")
            col4.metric("📉 Day Low", f"${latest['Low']:.2f}")
            pe = info.get('trailingPE')
            col5.metric("🧮 P/E", f"{pe:.2f}" if pe else "N/A")

            st.subheader("📊 Fundamental Snapshot")
            fund_score = calculate_fundamental_score(info)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("ROE", f"{info['returnOnEquity'] * 100:.1f}%" if info.get('returnOnEquity') else "N/A")
            dte = info.get('debtToEquity')
            col2.metric("Debt/Equity", f"{dte / 100:.2f}x" if dte is not None else "N/A")
            col3.metric("Profit Margin", f"{info['profitMargins'] * 100:.1f}%" if info.get('profitMargins') else "N/A")
            col4.metric("Fundamental Score", f"{fund_score}/100")

            current_atr = latest.get('ATR')
            if current_atr and not pd.isna(current_atr) and current_atr > 0:
                shares, stop_price, target_price, risk_dollars = calc_position(
                    latest['Close'], current_atr, account_size, risk_percent, atr_multiplier
                )

                st.subheader("🎯 Trade Setup")
                col_a, col_b, col_c, col_d = st.columns(4)
                shares_display = f"{shares:.2f} shares" if isinstance(shares, float) else f"{shares:,} shares"
                col_a.metric("📦 Size", shares_display)
                col_b.metric("🛑 Stop Loss", f"${stop_price:.2f}")
                col_c.metric("🎯 Target (2R)", f"${target_price:.2f}")
                col_d.metric("⚠️ Max Risk", f"${risk_dollars:,.2f} ({risk_percent}%)")

                if st.button(f"➕ Add {analysis_ticker} to Active Trades"):
                    st.session_state.active_trades.append({
                        'id': str(uuid.uuid4()),  # FIX: stable unique id, not list length
                        'ticker': analysis_ticker,
                        'entry_date': datetime.now().strftime('%Y-%m-%d'),
                        'entry_price': float(latest['Close']),
                        'shares': shares,
                        'stop_price': float(stop_price),
                        'target_price': float(target_price),
                        'status': 'ACTIVE'
                    })
                    save_trades()
                    st.success(f"✅ {analysis_ticker} added to active trades!")
                    st.rerun()

            st.subheader("📰 News")
            news_headlines = get_news(analysis_ticker)
            if news_headlines:
                for headline in news_headlines:
                    st.write(f"- {headline}")
            else:
                st.caption("No recent news found.")

            signals, rec, score = get_signals(df)
            col_a, col_b = st.columns([2, 1])
            with col_a:
                st.subheader("📡 Technical Signals")
                for s in signals:
                    st.write(s)
            with col_b:
                st.subheader("🎯 Recommendation")
                if rec == "🟢 BUY":
                    st.success(f"### {rec}")
                elif rec == "🔴 SELL":
                    st.error(f"### {rec}")
                else:
                    st.info(f"### {rec}")

            st.subheader("📉 Price Chart")
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=df.index, open=df['Open'], high=df['High'],
                low=df['Low'], close=df['Close'], name='Price'
            ))
            fig.add_trace(go.Scatter(x=df.index, y=df['SMA_20'], mode='lines', name='SMA 20',
                                     line=dict(color='orange', width=1, dash='dot')))
            fig.add_trace(go.Scatter(x=df.index, y=df['SMA_50'], mode='lines', name='SMA 50',
                                     line=dict(color='deepskyblue', width=1, dash='dash')))
            fig.add_trace(go.Scatter(x=df.index, y=df['SMA_200'], mode='lines', name='SMA 200',
                                     line=dict(color='red', width=1, dash='dash')))
            fig.update_layout(height=450, template='plotly_dark',
                              xaxis_title='Date', yaxis_title='Price',
                              xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)

# ========== TAB 2: ACTIVE TRADES ==========
with tab2:
    st.title("📈 Active Trades")
    st.markdown("---")

    if st.session_state.active_trades:
        # Batch the price lookups instead of one call per trade
        open_tickers = list({t['ticker'] for t in st.session_state.active_trades})
        current_prices = {}
        try:
            px = yf.download(open_tickers, period="5d", auto_adjust=True,
                             threads=True, progress=False)
            for tkr in open_tickers:
                try:
                    if len(open_tickers) == 1:
                        series = px['Close'].dropna()
                    else:
                        series = px['Close'][tkr].dropna()
                    if not series.empty:
                        current_prices[tkr] = float(series.iloc[-1])
                except Exception:
                    pass
        except Exception:
            pass

        for trade in list(st.session_state.active_trades):
            tkr = trade['ticker']
            entry_price = trade['entry_price']
            shares = trade['shares']
            current_price = current_prices.get(tkr, entry_price)
            pnl = (current_price - entry_price) * shares
            pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price else 0
            days_held = (datetime.now() - datetime.strptime(trade['entry_date'], '%Y-%m-%d')).days

            with st.container():
                st.subheader(f"📊 {tkr}")
                col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
                with col1:
                    st.caption(f"Entry: {trade['entry_date']} | Days Held: {days_held}")
                    shares_txt = f"{shares:.2f}" if isinstance(shares, float) else f"{shares:,}"
                    st.caption(f"Shares: {shares_txt}")
                    # Visual alerts when price crosses your levels
                    if current_price <= trade['stop_price']:
                        st.error("🛑 Below stop — exit per plan")
                    elif current_price >= trade['target_price']:
                        st.success("🎯 Target hit — consider taking profit")
                with col2:
                    st.metric("Entry", f"${entry_price:.2f}")
                with col3:
                    st.metric("Current", f"${current_price:.2f}")
                with col4:
                    st.metric("P&L", f"${pnl:.2f}", f"{pnl_pct:+.1f}%")
                with col5:
                    st.metric("Stop", f"${trade['stop_price']:.2f}")
                    st.caption(f"Target: ${trade['target_price']:.2f}")

                # FIX: close flow now keys on the trade's stable id instead of
                # its list index (indices shift when trades are removed)
                if st.button(f"✅ Close {tkr}", key=f"close_{trade['id']}"):
                    st.session_state.closing_trade = trade['id']
                    st.rerun()

                if st.session_state.closing_trade == trade['id']:
                    with st.form(key=f"close_form_{trade['id']}"):
                        exit_price = st.number_input("Exit Price", value=float(current_price), step=0.01)
                        notes = st.text_area("Notes", placeholder="What worked? What didn't?")
                        submitted = st.form_submit_button("Confirm Close")
                        if submitted:
                            pnl_final = (exit_price - entry_price) * shares
                            st.session_state.trade_history.append({
                                'ticker': tkr,
                                'entry_date': trade['entry_date'],
                                'entry_price': entry_price,
                                'exit_date': datetime.now().strftime('%Y-%m-%d'),
                                'exit_price': exit_price,
                                'shares': shares,
                                'pnl': pnl_final,
                                'notes': notes,
                                'status': 'CLOSED'
                            })
                            st.session_state.active_trades = [
                                t for t in st.session_state.active_trades if t['id'] != trade['id']
                            ]
                            st.session_state.closing_trade = None
                            save_trades()
                            st.success(f"✅ {tkr} closed! P&L: ${pnl_final:.2f}")
                            st.rerun()

                st.markdown("---")
    else:
        st.info("No active trades. Use the pro scanner to find picks!")

# ========== TAB 3: JOURNAL ==========
with tab3:
    st.title("📓 Trade Journal")
    st.markdown("---")

    with st.expander("➕ Add New Trade Manually", expanded=False):
        with st.form("new_trade"):
            col1, col2 = st.columns(2)
            with col1:
                ticker_input = st.text_input("Ticker", value="AAPL").upper().strip()
                m_entry_price = st.number_input("Entry Price", min_value=0.01, step=0.01, value=150.00)
                m_shares = st.number_input("Shares", min_value=0.01, step=0.01, value=100.0)
            with col2:
                m_stop_price = st.number_input("Stop Loss Price", min_value=0.01, step=0.01, value=140.00)
                m_target_price = st.number_input("Target Price", min_value=0.01, step=0.01, value=165.00)
                m_notes = st.text_area("Notes", placeholder="Why did you enter this trade?")

            submitted = st.form_submit_button("Add Trade")
            if submitted:
                st.session_state.active_trades.append({
                    'id': str(uuid.uuid4()),
                    'ticker': ticker_input,
                    'entry_date': datetime.now().strftime('%Y-%m-%d'),
                    'entry_price': m_entry_price,
                    'shares': m_shares,
                    'stop_price': m_stop_price,
                    'target_price': m_target_price,
                    'notes': m_notes,
                    'status': 'ACTIVE'
                })
                save_trades()
                st.success(f"✅ {ticker_input} added to active trades!")
                st.rerun()

    st.subheader("📊 Trade History")

    if st.session_state.trade_history:
        total_trades = len(st.session_state.trade_history)
        winning_trades = sum(1 for t in st.session_state.trade_history if t.get('pnl') and t['pnl'] > 0)
        total_pnl = sum(t['pnl'] for t in st.session_state.trade_history if t.get('pnl'))
        wins = [t['pnl'] for t in st.session_state.trade_history if t.get('pnl') and t['pnl'] > 0]
        losses = [t['pnl'] for t in st.session_state.trade_history if t.get('pnl') and t['pnl'] <= 0]

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("📊 Total Trades", total_trades)
        col2.metric("✅ Win Rate", f"{winning_trades / total_trades * 100:.0f}%")
        col3.metric("💰 Total P&L", f"${total_pnl:.2f}")
        # Expectancy: the single most useful journal stat for a swing trader
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        expectancy = (winning_trades / total_trades) * avg_win + (1 - winning_trades / total_trades) * avg_loss
        col4.metric("📐 Expectancy/Trade", f"${expectancy:.2f}")

        st.markdown("---")

        for trade in st.session_state.trade_history[::-1]:
            with st.container():
                col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
                with col1:
                    st.subheader(trade['ticker'])
                    st.caption(f"Entry: {trade['entry_date']} | Exit: {trade.get('exit_date', 'Open')}")
                with col2:
                    st.metric("Entry", f"${trade['entry_price']:.2f}")
                    st.metric("Exit", f"${trade['exit_price']:.2f}" if trade.get('exit_price') else "-")
                with col3:
                    st.metric("P&L", f"${trade['pnl']:.2f}" if trade.get('pnl') is not None else "-")
                with col4:
                    if trade.get('notes'):
                        st.caption(f"📝 {trade['notes']}")
                st.markdown("---")
    else:
        st.info("No trades logged yet. Start your trading journey!")

# ========== SIDEBAR - QUICK ACTIONS ==========
st.sidebar.markdown("---")
st.sidebar.subheader("⚡ Quick Actions")
if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption(f"📊 Active Trades: {len(st.session_state.active_trades)}")
st.sidebar.caption(f"📓 Trade History: {len(st.session_state.trade_history)}")
st.sidebar.markdown("---")

is_weekend = datetime.now().weekday() >= 5
if is_weekend:
    st.sidebar.caption("📌 Weekend Mode: Volume filter disabled")
else:
    st.sidebar.caption("📌 Market Hours: 9:30 AM - 4:00 PM EST")
st.sidebar.caption(f"💰 Account: ${account_size:,}")
