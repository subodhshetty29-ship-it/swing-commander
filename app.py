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

# =====================================================================
# PRO SWING COMMANDER v2 — Decision Engine Edition
#
# One button ("Run Tracker") produces:
#   1. Market regime check (SPY vs 50-day MA) — gates all new longs
#   2. Relative-strength ranked scan (vs SPY, percentile-based —
#      fixes the "everything scores 95" saturation problem)
#   3. Complete trade PLANS: entry trigger, stop, target, shares,
#      $ risk, R:R — after passing vetoes (earnings window, sector
#      cap, portfolio heat)
#   4. Exit ACTION LIST for open trades: breakeven moves, trailing
#      stops (chandelier), targets hit, time stops
#   5. Auto-journal: plans flow into trades, closes compute R-multiples
# =====================================================================

st.set_page_config(page_title="Pro Swing Commander", page_icon="📈", layout="wide")

# ========== STRATEGY CONFIG ==========
HOLD_WINDOW_DAYS = 15        # typical swing hold; earnings inside this = veto
EARNINGS_VETO_DAYS = 21      # veto if earnings within this many days
TIME_STOP_DAYS = 10          # exit stale trades after this many days if < 0.5R
MAX_POSITIONS = 5            # max concurrent open trades
MAX_PER_SECTOR = 2           # max open trades in one sector
TRAIL_ATR_MULT = 3.0         # chandelier trailing stop multiplier
MAX_POSITION_PCT = 0.25      # no single position > 25% of account
MAX_INFO_FETCHES = 30        # only fetch slow .info for top candidates
ENTRY_BUFFER = 1.001         # buy-stop trigger = prior high * this

TRADES_FILE = Path("trades.json")
SCAN_FILE = Path("last_scan.json")

# ========== PERSISTENCE ==========
def load_json(path, default):
    if path.exists():
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default

def save_trades():
    try:
        with open(TRADES_FILE, "w") as f:
            json.dump({"active": st.session_state.active_trades,
                       "history": st.session_state.trade_history}, f, indent=2, default=str)
    except OSError as e:
        st.warning(f"Could not save trades: {e}")

def save_scan(payload):
    try:
        with open(SCAN_FILE, "w") as f:
            json.dump(payload, f, indent=2, default=str)
    except OSError:
        pass

# ========== SESSION STATE ==========
def init_session():
    if 'active_trades' not in st.session_state:
        data = load_json(TRADES_FILE, {})
        st.session_state.active_trades = data.get("active", [])
        st.session_state.trade_history = data.get("history", [])
    if 'scan' not in st.session_state:
        st.session_state.scan = load_json(SCAN_FILE, None)  # last scan survives restarts
    if 'closing_trade' not in st.session_state:
        st.session_state.closing_trade = None
    if 'pending_ticker' not in st.session_state:
        st.session_state.pending_ticker = None

init_session()

if st.session_state.pending_ticker:
    st.session_state.ticker_input = st.session_state.pending_ticker
    st.session_state.pending_ticker = None

# ========== SIDEBAR ==========
st.sidebar.title("⚙️ Pro Settings")
ticker = st.sidebar.text_input("Deep Dive Ticker", value="AAPL", key="ticker_input").upper().strip()

period_map = {"3 Months": "3mo", "6 Months": "6mo", "1 Year": "1y", "2 Years": "2y"}
selected_period = st.sidebar.selectbox("Chart Range", list(period_map.keys()), index=2)
period = period_map[selected_period]

st.sidebar.markdown("---")
st.sidebar.subheader("💰 Risk Settings")
account_size = st.sidebar.number_input("Account Size ($)", min_value=100, max_value=10_000_000,
                                       value=50_000, step=1000)
risk_percent = st.sidebar.slider("Risk per Trade (%)", 0.25, 3.0, 1.0, 0.25)
atr_multiplier = st.sidebar.slider("Initial Stop (ATR x)", 1.0, 3.0, 2.0, 0.5)
max_heat_pct = st.sidebar.slider("Max Portfolio Heat (%)", 2.0, 10.0, 5.0, 0.5,
                                 help="Total $ at risk across ALL open trades, as % of account. New plans are vetoed above this.")

st.sidebar.markdown("---")
st.sidebar.subheader("🔍 Scanner")
top_n_plans = st.sidebar.slider("Max New Plans per Scan", 1, 5, 3)
st.sidebar.caption("📊 Data from Yahoo Finance")

# ========== INDICATORS ==========
def add_indicators(df):
    df = df.copy()
    df['SMA_20'] = df['Close'].rolling(20).mean()
    df['SMA_50'] = df['Close'].rolling(50).mean()
    df['SMA_200'] = df['Close'].rolling(200).mean()
    df['RSI'] = ta.momentum.RSIIndicator(df['Close'], window=14).rsi()
    macd = ta.trend.MACD(df['Close'])
    df['MACD'] = macd.macd()
    df['MACD_Signal'] = macd.macd_signal()
    df['ATR'] = ta.volatility.AverageTrueRange(df['High'], df['Low'], df['Close'],
                                               window=14).average_true_range()
    df['Volume_MA'] = df['Volume'].rolling(20).mean()
    return df

@st.cache_data(ttl=600)
def fetch_data(ticker, period):
    try:
        df = yf.Ticker(ticker).history(period=period)
        if df.empty:
            return None, None, f"No data found for '{ticker}'"
        info = yf.Ticker(ticker).info
        return add_indicators(df), info, None
    except Exception as e:
        return None, None, str(e)

@st.cache_data(ttl=1800)
def get_news(ticker):
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}",
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                         timeout=10)
        if r.status_code == 200:
            return [n.get('title', '') for n in r.json().get('news', [])[:5] if n.get('title')]
        return []
    except Exception:
        return []

# ========== UNIVERSE ==========
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

# ========== PORTFOLIO STATE HELPERS ==========
def portfolio_heat():
    """Total $ currently at risk across open trades (entry - stop) * shares."""
    heat = 0.0
    for t in st.session_state.active_trades:
        risk = max(t['entry_price'] - t['stop_price'], 0) * t['shares']
        heat += risk
    return heat

def sector_counts():
    counts = {}
    for t in st.session_state.active_trades:
        s = t.get('sector', 'Unknown')
        counts[s] = counts.get(s, 0) + 1
    return counts

def open_tickers():
    return {t['ticker'] for t in st.session_state.active_trades}

# ========== THE DECISION ENGINE ==========
def run_tracker():
    """Full nightly routine: regime -> RS scan -> vetoes -> trade plans."""
    universe = get_stock_universe()
    errors = []
    status = st.empty()
    prog = st.progress(0)

    # ---- Download everything in one batch (universe + SPY benchmark) ----
    status.text(f"Downloading {len(universe)} tickers + SPY...")
    try:
        raw = yf.download(universe + ['SPY'], period="1y", group_by='ticker',
                          auto_adjust=True, threads=True, progress=False)
    except Exception as e:
        prog.empty(); status.empty()
        return None, [f"Batch download failed: {e}"]

    # ---- 1. MARKET REGIME (SPY vs 50-day MA) ----
    try:
        spy = raw['SPY'].dropna(subset=['Close'])
        spy_close = float(spy['Close'].iloc[-1])
        spy_sma50 = float(spy['Close'].rolling(50).mean().iloc[-1])
        spy_sma200 = float(spy['Close'].rolling(200).mean().iloc[-1])
        spy_ret_1m = spy_close / float(spy['Close'].iloc[-21]) - 1
        spy_ret_3m = spy_close / float(spy['Close'].iloc[-63]) - 1
        if spy_close > spy_sma50 and spy_close > spy_sma200:
            regime = "GREEN"
        elif spy_close > spy_sma200:
            regime = "YELLOW"
        else:
            regime = "RED"
    except Exception as e:
        prog.empty(); status.empty()
        return None, [f"SPY regime check failed: {e}"]

    # ---- 2. RELATIVE STRENGTH + SETUP SCORING (fast, price-only) ----
    rows = []
    for i, tkr in enumerate(universe):
        prog.progress((i + 1) / len(universe) * 0.6)
        try:
            if tkr not in raw.columns.get_level_values(0):
                errors.append(f"{tkr}: no data")
                continue
            hist = raw[tkr].dropna(subset=['Close'])
            if len(hist) < 130:
                errors.append(f"{tkr}: insufficient history")
                continue
            hist = add_indicators(hist)
            latest = hist.iloc[-1]
            close = float(latest['Close'])
            if close < 5:  # skip illiquid penny-ish names
                continue

            # Relative strength vs SPY (the core momentum factor)
            ret_1m = close / float(hist['Close'].iloc[-21]) - 1
            ret_3m = close / float(hist['Close'].iloc[-63]) - 1
            rs_raw = 0.4 * (ret_1m - spy_ret_1m) + 0.6 * (ret_3m - spy_ret_3m)

            # Setup quality (0-100): trend alignment, healthy RSI,
            # buyable pullback (near 20MA, not extended), tradeable ATR
            sma20, sma50, sma200 = latest['SMA_20'], latest['SMA_50'], latest['SMA_200']
            rsi, atr = latest['RSI'], latest['ATR']
            if pd.isna(sma50) or pd.isna(rsi) or pd.isna(atr) or atr <= 0:
                errors.append(f"{tkr}: indicators unavailable")
                continue

            setup = 0
            if close > sma20 > sma50:
                setup += 25
            elif close > sma50:
                setup += 12
            if not pd.isna(sma200) and close > sma200:
                setup += 15
            if 40 <= rsi <= 68:
                setup += 20          # strong but not overbought
            elif 30 <= rsi < 40:
                setup += 8
            ext = (close - sma20) / sma20 if sma20 else 0
            if -0.02 <= ext <= 0.05:
                setup += 20          # near the 20MA = low-risk entry point
            elif ext <= 0.10:
                setup += 8           # somewhat extended
            atr_pct = atr / close
            if 0.015 <= atr_pct <= 0.06:
                setup += 10          # enough movement to pay, not chaos
            vol_ma = latest['Volume_MA']
            if vol_ma and vol_ma > 0 and latest['Volume'] > 1.2 * vol_ma:
                setup += 10

            prior_high = float(hist['High'].iloc[-1])
            rows.append({
                'ticker': tkr, 'close': close, 'rs_raw': rs_raw,
                'setup': min(setup, 100), 'rsi': float(rsi), 'atr': float(atr),
                'prior_high': prior_high,
                'ret_1m': ret_1m, 'ret_3m': ret_3m,
            })
        except Exception as e:
            errors.append(f"{tkr}: {e}")

    if not rows:
        prog.empty(); status.empty()
        return None, errors + ["No tickers survived scoring"]

    # Percentile-rank RS across the universe -> smooth 0-100 distribution
    dfp = pd.DataFrame(rows)
    dfp['rs_pct'] = dfp['rs_raw'].rank(pct=True) * 100
    dfp['composite'] = 0.55 * dfp['rs_pct'] + 0.45 * dfp['setup']
    dfp = dfp.sort_values('composite', ascending=False).reset_index(drop=True)

    # ---- 3. VETO PIPELINE + TRADE PLANS (slow .info only for top names) ----
    plans, vetoed, ranking = [], [], []
    heat_available = account_size * (max_heat_pct / 100) - portfolio_heat()
    sec_counts = sector_counts()
    held = open_tickers()
    slots_left = MAX_POSITIONS - len(st.session_state.active_trades)

    top = dfp.head(MAX_INFO_FETCHES)
    for j, row in top.iterrows():
        tkr = row['ticker']
        status.text(f"Vetting {j+1}/{len(top)}: {tkr}")
        prog.progress(0.6 + (j + 1) / len(top) * 0.4)
        try:
            info = yf.Ticker(tkr).info or {}
        except Exception:
            info = {}
        sector = info.get('sector', 'Unknown')

        # --- Build the raw plan first ---
        entry = round(row['prior_high'] * ENTRY_BUFFER, 2)   # buy-stop trigger
        stop_dist = row['atr'] * atr_multiplier
        stop_dist = min(max(stop_dist, entry * 0.01), entry * 0.15)
        stop = round(entry - stop_dist, 2)
        target = round(entry + 2 * stop_dist, 2)             # 2R target
        risk_dollars = account_size * (risk_percent / 100)
        shares = int(risk_dollars / stop_dist) if stop_dist > 0 else 0
        # position value cap
        max_shares_by_value = int((account_size * MAX_POSITION_PCT) / entry)
        capped = shares > max_shares_by_value
        shares = min(shares, max_shares_by_value)
        actual_risk = round(shares * stop_dist, 2)

        rank_entry = {
            'Ticker': tkr, 'Sector': sector, 'Price': round(row['close'], 2),
            'RS %ile': round(row['rs_pct']), 'Setup': round(row['setup']),
            'Composite': round(row['composite']), 'RSI': round(row['rsi'], 1),
            '1M vs SPY': f"{(row['ret_1m'] - spy_ret_1m) * 100:+.1f}%",
            '3M vs SPY': f"{(row['ret_3m'] - spy_ret_3m) * 100:+.1f}%",
        }
        ranking.append(rank_entry)

        # --- Veto checks (in order of severity) ---
        veto_reason = None
        if regime == "RED":
            veto_reason = "Regime RED — no new longs"
        elif tkr in held:
            veto_reason = "Already holding"
        elif slots_left - len(plans) <= 0:
            veto_reason = f"Max positions ({MAX_POSITIONS}) reached"
        elif sec_counts.get(sector, 0) + sum(1 for p in plans if p['sector'] == sector) >= MAX_PER_SECTOR:
            veto_reason = f"Sector cap: already {MAX_PER_SECTOR} in {sector}"
        else:
            ts = info.get('earningsTimestamp')
            if ts:
                try:
                    days_to_earnings = (datetime.fromtimestamp(ts) - datetime.now()).days
                    if 0 <= days_to_earnings <= EARNINGS_VETO_DAYS:
                        veto_reason = f"Earnings in {days_to_earnings}d (gap risk)"
                except (ValueError, OSError, OverflowError):
                    pass
        if veto_reason is None and actual_risk > heat_available - sum(p['risk'] for p in plans):
            veto_reason = f"Portfolio heat cap ({max_heat_pct}%) would be exceeded"
        if veto_reason is None and shares < 1:
            veto_reason = "Position sizes to < 1 share"
        if veto_reason is None and row['setup'] < 40:
            veto_reason = "Setup quality too low (extended/broken chart)"

        if veto_reason:
            vetoed.append({'Ticker': tkr, 'Composite': round(row['composite']),
                           'Reason': veto_reason})
            continue

        if len(plans) < top_n_plans:
            ts = info.get('earningsTimestamp')
            days_to_earn = None
            if ts:
                try:
                    days_to_earn = (datetime.fromtimestamp(ts) - datetime.now()).days
                except (ValueError, OSError, OverflowError):
                    pass
            plans.append({
                'ticker': tkr, 'sector': sector,
                'composite': round(row['composite']), 'rs_pct': round(row['rs_pct']),
                'setup': round(row['setup']),
                'last_close': round(row['close'], 2),
                'entry': entry, 'stop': stop, 'target': target,
                'shares': shares, 'risk': actual_risk,
                'position_value': round(shares * entry, 2),
                'rr': 2.0, 'atr': round(row['atr'], 2),
                'days_to_earnings': days_to_earn,
                'value_capped': capped,
            })
        time.sleep(0.05)

    prog.empty(); status.empty()

    payload = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'regime': regime,
        'spy': {'close': round(spy_close, 2), 'sma50': round(spy_sma50, 2),
                'sma200': round(spy_sma200, 2)},
        'plans': plans, 'vetoed': vetoed, 'ranking': ranking[:25],
    }
    save_scan(payload)
    return payload, errors

# ========== EXIT MANAGEMENT ENGINE ==========
def exit_actions():
    """Evaluate every open trade against the exit rules. Returns
    (per-trade action dicts keyed by trade id)."""
    actions = {}
    tickers = list(open_tickers())
    if not tickers:
        return actions
    try:
        px = yf.download(tickers, period="3mo", auto_adjust=True,
                         threads=True, progress=False)
    except Exception:
        return actions

    for t in st.session_state.active_trades:
        tkr = t['ticker']
        try:
            if len(tickers) == 1:
                hist = px.dropna(subset=['Close'])
            else:
                hist = px.xs(tkr, axis=1, level=0).dropna(subset=['Close']) \
                    if isinstance(px.columns, pd.MultiIndex) else px
            if hist.empty:
                continue
            current = float(hist['Close'].iloc[-1])
            entry_dt = datetime.strptime(t['entry_date'], '%Y-%m-%d')
            since_entry = hist[hist.index >= pd.Timestamp(entry_dt)]
            highest_close = float(since_entry['Close'].max()) if not since_entry.empty else current
            atr_now = ta.volatility.AverageTrueRange(
                hist['High'], hist['Low'], hist['Close'], window=14
            ).average_true_range().iloc[-1]
            atr_now = float(atr_now) if not pd.isna(atr_now) else None

            entry = t['entry_price']
            stop = t['stop_price']
            initial_stop = t.get('initial_stop', stop)
            init_risk = max(entry - initial_stop, 0.01)
            r_mult = (current - entry) / init_risk
            days_held = (datetime.now() - entry_dt).days

            acts = []
            urgency = "info"
            if current <= stop:
                acts.append(f"🛑 STOP HIT — exit at market (stop ${stop:.2f})")
                urgency = "error"
            elif current >= t['target_price']:
                acts.append(f"🎯 TARGET HIT (+{r_mult:.1f}R) — take profit, or sell half & trail the rest")
                urgency = "success"
            else:
                # breakeven move
                if r_mult >= 1.0 and stop < entry:
                    acts.append(f"⬆️ +1R reached — raise stop to breakeven (${entry:.2f})")
                    urgency = "warning"
                # chandelier trail
                if atr_now:
                    chandelier = round(highest_close - TRAIL_ATR_MULT * atr_now, 2)
                    if chandelier > stop and chandelier < current:
                        acts.append(f"⬆️ Trail stop to ${chandelier:.2f} (chandelier: high ${highest_close:.2f} − {TRAIL_ATR_MULT}×ATR)")
                        if urgency == "info":
                            urgency = "warning"
                # time stop
                if days_held >= TIME_STOP_DAYS and r_mult < 0.5:
                    acts.append(f"⏱️ Day {days_held}, only {r_mult:+.1f}R — time stop: exit & free the capital")
                    urgency = "warning" if urgency == "info" else urgency

            actions[t['id']] = {
                'current': current, 'r_mult': r_mult, 'days_held': days_held,
                'actions': acts, 'urgency': urgency,
                'suggested_stops': {
                    'breakeven': round(entry, 2),
                    'chandelier': round(highest_close - TRAIL_ATR_MULT * atr_now, 2) if atr_now else None,
                },
            }
        except Exception:
            continue
    return actions

# ========== TABS ==========
tab1, tab2, tab3 = st.tabs(["🎛️ Command Center", "📈 Active Trades", "📓 Journal"])

# =====================================================================
# TAB 1: COMMAND CENTER
# =====================================================================
with tab1:
    st.title("🎛️ Pro Swing Commander")
    heat = portfolio_heat()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open Positions", f"{len(st.session_state.active_trades)}/{MAX_POSITIONS}")
    c2.metric("Portfolio Heat", f"${heat:,.0f}",
              f"{heat / account_size * 100:.1f}% of {max_heat_pct}% cap")
    c3.metric("Account", f"${account_size:,}")
    c4.metric("Risk/Trade", f"{risk_percent}% (${account_size * risk_percent / 100:,.0f})")

    st.markdown("---")
    run = st.button("🚀 Run Tracker", type="primary",
                    help="Regime check → RS scan → vetoes → tonight's trade plans")

    if run:
        with st.spinner("Running the full decision engine..."):
            payload, errors = run_tracker()
            if payload:
                st.session_state.scan = payload
            st.session_state.scan_errors = errors

    scan = st.session_state.scan
    if scan:
        st.caption(f"Last run: {scan['timestamp']}")

        # ---- REGIME BANNER ----
        regime = scan['regime']
        spy = scan['spy']
        if regime == "GREEN":
            st.success(f"🟢 **REGIME: GREEN** — SPY ${spy['close']} above 50-day (${spy['sma50']}) and 200-day (${spy['sma200']}). New longs allowed.")
        elif regime == "YELLOW":
            st.warning(f"🟡 **REGIME: YELLOW** — SPY ${spy['close']} below 50-day (${spy['sma50']}) but above 200-day (${spy['sma200']}). Caution: smaller size, A+ setups only.")
        else:
            st.error(f"🔴 **REGIME: RED** — SPY ${spy['close']} below 200-day (${spy['sma200']}). No new longs. Manage exits and wait.")

        # ---- TONIGHT'S TRADE PLANS ----
        st.subheader("📋 Tonight's Trade Plans")
        plans = scan.get('plans', [])
        if plans:
            st.caption("Place these as **buy-stop orders** in Fidelity. If price never hits the trigger, the trade never happens — that's the point (momentum confirmation).")
            for plan in plans:
                with st.container(border=True):
                    h1, h2 = st.columns([3, 1])
                    with h1:
                        st.markdown(f"### {plan['ticker']} · {plan['sector']}")
                        st.caption(f"Composite {plan['composite']} (RS %ile {plan['rs_pct']} · Setup {plan['setup']}) · Last close ${plan['last_close']}")
                    with h2:
                        earn = plan.get('days_to_earnings')
                        st.caption(f"Earnings: {'in ' + str(earn) + 'd' if earn is not None else 'none near'}")

                    p1, p2, p3, p4, p5, p6 = st.columns(6)
                    p1.metric("🎯 Buy Stop", f"${plan['entry']:.2f}")
                    p2.metric("🛑 Stop Loss", f"${plan['stop']:.2f}")
                    p3.metric("💰 Target (2R)", f"${plan['target']:.2f}")
                    p4.metric("📦 Shares", f"{plan['shares']:,}")
                    p5.metric("💵 Position", f"${plan['position_value']:,.0f}")
                    p6.metric("⚠️ Risk", f"${plan['risk']:,.0f}")
                    if plan.get('value_capped'):
                        st.caption(f"ℹ️ Shares capped at {MAX_POSITION_PCT*100:.0f}% of account — actual risk is below your {risk_percent}% budget.")

                    if st.button(f"✅ I placed this order — track {plan['ticker']}",
                                 key=f"take_{plan['ticker']}"):
                        st.session_state.active_trades.append({
                            'id': str(uuid.uuid4()),
                            'ticker': plan['ticker'],
                            'sector': plan['sector'],
                            'entry_date': datetime.now().strftime('%Y-%m-%d'),
                            'entry_price': plan['entry'],
                            'shares': plan['shares'],
                            'stop_price': plan['stop'],
                            'initial_stop': plan['stop'],
                            'target_price': plan['target'],
                            'setup_note': f"Composite {plan['composite']} | RS {plan['rs_pct']} | Setup {plan['setup']} | Regime {regime}",
                            'status': 'ACTIVE'
                        })
                        save_trades()
                        st.success(f"{plan['ticker']} added — exit engine will manage it from here.")
                        st.rerun()
        elif regime == "RED":
            st.info("No plans — regime is RED. The best trade is no trade.")
        else:
            st.info("No setups passed the veto pipeline tonight. That's normal — the system says no more often than yes.")

        # ---- RANKING TABLE ----
        with st.expander("📊 Full Ranking (top 25 by composite)"):
            if scan.get('ranking'):
                st.dataframe(pd.DataFrame(scan['ranking']), hide_index=True,
                             use_container_width=True)

        # ---- VETOED ----
        vetoed = scan.get('vetoed', [])
        if vetoed:
            with st.expander(f"🚫 Vetoed Candidates ({len(vetoed)}) — high scores that failed a safety rule"):
                st.dataframe(pd.DataFrame(vetoed), hide_index=True,
                             use_container_width=True)

    else:
        st.info("Hit **Run Tracker** to generate tonight's trade plans.")

    if st.session_state.get('scan_errors'):
        with st.expander(f"⚠️ {len(st.session_state.scan_errors)} tickers skipped"):
            for err in st.session_state.scan_errors:
                st.caption(err)

    # ---- DEEP DIVE ----
    st.markdown("---")
    st.subheader(f"🔎 {ticker} Deep Dive")
    df, info, error = fetch_data(ticker, period)
    if error:
        st.error(f"❌ {error}")
    elif df is not None:
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        change_pct = (latest['Close'] - prev['Close']) / prev['Close'] * 100 if prev['Close'] else 0
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Price", f"${latest['Close']:.2f}", f"{change_pct:+.2f}%")
        c2.metric("RSI", f"{latest['RSI']:.1f}" if not pd.isna(latest['RSI']) else "N/A")
        c3.metric("ATR", f"${latest['ATR']:.2f}" if not pd.isna(latest['ATR']) else "N/A")
        c4.metric("Sector", info.get('sector', 'N/A'))

        fig = go.Figure()
        fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'],
                                     low=df['Low'], close=df['Close'], name='Price'))
        fig.add_trace(go.Scatter(x=df.index, y=df['SMA_20'], name='SMA 20',
                                 line=dict(color='orange', width=1, dash='dot')))
        fig.add_trace(go.Scatter(x=df.index, y=df['SMA_50'], name='SMA 50',
                                 line=dict(color='deepskyblue', width=1, dash='dash')))
        fig.add_trace(go.Scatter(x=df.index, y=df['SMA_200'], name='SMA 200',
                                 line=dict(color='red', width=1, dash='dash')))
        fig.update_layout(height=420, template='plotly_dark',
                          xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

        headlines = get_news(ticker)
        if headlines:
            st.caption("📰 " + " · ".join(headlines[:3]))

# =====================================================================
# TAB 2: ACTIVE TRADES + EXIT ENGINE
# =====================================================================
with tab2:
    st.title("📈 Active Trades")
    st.markdown("---")

    if st.session_state.active_trades:
        with st.spinner("Evaluating exit rules..."):
            actions = exit_actions()

        # ---- ACTION LIST (the things you actually need to DO) ----
        todo = [(t, actions.get(t['id'])) for t in st.session_state.active_trades
                if actions.get(t['id']) and actions[t['id']]['actions']]
        if todo:
            st.subheader("⚡ Action List")
            for t, a in todo:
                for act in a['actions']:
                    msg = f"**{t['ticker']}** — {act}"
                    if a['urgency'] == 'error':
                        st.error(msg)
                    elif a['urgency'] == 'success':
                        st.success(msg)
                    elif a['urgency'] == 'warning':
                        st.warning(msg)
                    else:
                        st.info(msg)
            st.markdown("---")
        else:
            st.info("✅ No exit actions needed today. Stops and targets stand.")
            st.markdown("---")

        for trade in list(st.session_state.active_trades):
            tkr = trade['ticker']
            a = actions.get(trade['id'], {})
            current = a.get('current', trade['entry_price'])
            r_mult = a.get('r_mult', 0)
            days_held = a.get('days_held',
                              (datetime.now() - datetime.strptime(trade['entry_date'], '%Y-%m-%d')).days)
            pnl = (current - trade['entry_price']) * trade['shares']
            pnl_pct = (current / trade['entry_price'] - 1) * 100 if trade['entry_price'] else 0

            with st.container(border=True):
                st.subheader(f"📊 {tkr} · {trade.get('sector', '')}")
                if trade.get('setup_note'):
                    st.caption(trade['setup_note'])
                c1, c2, c3, c4, c5, c6 = st.columns(6)
                c1.metric("Entry", f"${trade['entry_price']:.2f}")
                c2.metric("Current", f"${current:.2f}")
                c3.metric("P&L", f"${pnl:.2f}", f"{pnl_pct:+.1f}%")
                c4.metric("R-Multiple", f"{r_mult:+.2f}R")
                c5.metric("Stop", f"${trade['stop_price']:.2f}")
                c6.metric("Days", days_held)
                st.caption(f"Target: ${trade['target_price']:.2f} · Initial stop: ${trade.get('initial_stop', trade['stop_price']):.2f} · Shares: {trade['shares']:,}")

                # one-click stop updates suggested by the exit engine
                sugg = a.get('suggested_stops', {})
                bcols = st.columns(4)
                with bcols[0]:
                    be = sugg.get('breakeven')
                    if be and r_mult >= 1.0 and trade['stop_price'] < be:
                        if st.button(f"⬆️ Stop → BE ${be:.2f}", key=f"be_{trade['id']}"):
                            trade['stop_price'] = be
                            save_trades(); st.rerun()
                with bcols[1]:
                    ch = sugg.get('chandelier')
                    if ch and ch > trade['stop_price'] and ch < current:
                        if st.button(f"⬆️ Trail → ${ch:.2f}", key=f"tr_{trade['id']}"):
                            trade['stop_price'] = ch
                            save_trades(); st.rerun()
                with bcols[2]:
                    if st.button(f"✅ Close {tkr}", key=f"close_{trade['id']}"):
                        st.session_state.closing_trade = trade['id']
                        st.rerun()

                if st.session_state.closing_trade == trade['id']:
                    with st.form(key=f"close_form_{trade['id']}"):
                        exit_price = st.number_input("Exit Price", value=float(current), step=0.01)
                        followed = st.selectbox("Did you follow the plan?",
                                                ["Yes — rule-based exit", "No — discretionary override"])
                        notes = st.text_area("Notes", placeholder="What worked? What didn't?")
                        if st.form_submit_button("Confirm Close"):
                            init_risk = max(trade['entry_price'] - trade.get('initial_stop', trade['stop_price']), 0.01)
                            r_final = (exit_price - trade['entry_price']) / init_risk
                            st.session_state.trade_history.append({
                                'ticker': tkr, 'sector': trade.get('sector', 'Unknown'),
                                'entry_date': trade['entry_date'],
                                'entry_price': trade['entry_price'],
                                'exit_date': datetime.now().strftime('%Y-%m-%d'),
                                'exit_price': exit_price,
                                'shares': trade['shares'],
                                'pnl': round((exit_price - trade['entry_price']) * trade['shares'], 2),
                                'r_multiple': round(r_final, 2),
                                'followed_plan': followed.startswith("Yes"),
                                'setup_note': trade.get('setup_note', ''),
                                'notes': notes, 'status': 'CLOSED'
                            })
                            st.session_state.active_trades = [
                                t for t in st.session_state.active_trades if t['id'] != trade['id']
                            ]
                            st.session_state.closing_trade = None
                            save_trades()
                            st.rerun()
    else:
        st.info("No active trades. Run the tracker in the Command Center to get plans.")

# =====================================================================
# TAB 3: JOURNAL
# =====================================================================
with tab3:
    st.title("📓 Trade Journal")
    st.markdown("---")

    with st.expander("➕ Add Trade Manually"):
        with st.form("new_trade"):
            c1, c2 = st.columns(2)
            with c1:
                m_tkr = st.text_input("Ticker", value="AAPL").upper().strip()
                m_entry = st.number_input("Entry Price", min_value=0.01, step=0.01, value=150.00)
                m_shares = st.number_input("Shares", min_value=1, step=1, value=100)
            with c2:
                m_stop = st.number_input("Stop Loss", min_value=0.01, step=0.01, value=140.00)
                m_target = st.number_input("Target", min_value=0.01, step=0.01, value=170.00)
                m_notes = st.text_area("Notes")
            if st.form_submit_button("Add Trade"):
                st.session_state.active_trades.append({
                    'id': str(uuid.uuid4()), 'ticker': m_tkr, 'sector': 'Unknown',
                    'entry_date': datetime.now().strftime('%Y-%m-%d'),
                    'entry_price': m_entry, 'shares': m_shares,
                    'stop_price': m_stop, 'initial_stop': m_stop,
                    'target_price': m_target, 'setup_note': m_notes, 'status': 'ACTIVE'
                })
                save_trades()
                st.success(f"✅ {m_tkr} added!")
                st.rerun()

    hist = st.session_state.trade_history
    if hist:
        total = len(hist)
        wins = [t for t in hist if t.get('pnl', 0) > 0]
        total_pnl = sum(t.get('pnl', 0) for t in hist)
        r_vals = [t['r_multiple'] for t in hist if t.get('r_multiple') is not None]
        avg_r = sum(r_vals) / len(r_vals) if r_vals else 0
        followed = [t for t in hist if t.get('followed_plan') is True]

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Trades", total)
        c2.metric("Win Rate", f"{len(wins) / total * 100:.0f}%")
        c3.metric("Total P&L", f"${total_pnl:,.2f}")
        c4.metric("Avg R / Trade", f"{avg_r:+.2f}R",
                  help="Expectancy in R. Positive after 30+ trades = you have an edge.")
        c5.metric("Plan Adherence", f"{len(followed) / total * 100:.0f}%" if total else "N/A")

        if r_vals and len(r_vals) >= 5:
            fig = go.Figure()
            cum_r = pd.Series(r_vals).cumsum()
            fig.add_trace(go.Scatter(y=cum_r, mode='lines+markers', name='Cumulative R'))
            fig.update_layout(height=250, template='plotly_dark',
                              title="Equity Curve (in R-multiples)",
                              xaxis_title="Trade #", yaxis_title="Cumulative R")
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")
        for trade in hist[::-1]:
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
                with c1:
                    st.subheader(f"{trade['ticker']}")
                    st.caption(f"{trade['entry_date']} → {trade.get('exit_date', 'Open')} · {trade.get('sector', '')}")
                    if trade.get('setup_note'):
                        st.caption(f"🏷️ {trade['setup_note']}")
                with c2:
                    st.metric("Entry", f"${trade['entry_price']:.2f}")
                    st.metric("Exit", f"${trade['exit_price']:.2f}" if trade.get('exit_price') else "-")
                with c3:
                    st.metric("P&L", f"${trade.get('pnl', 0):.2f}")
                    if trade.get('r_multiple') is not None:
                        st.metric("R", f"{trade['r_multiple']:+.2f}R")
                with c4:
                    adherence = "✅ Followed plan" if trade.get('followed_plan') else \
                                ("⚠️ Overrode plan" if trade.get('followed_plan') is False else "")
                    if adherence:
                        st.caption(adherence)
                    if trade.get('notes'):
                        st.caption(f"📝 {trade['notes']}")
    else:
        st.info("No closed trades yet. Your expectancy stats will appear here.")

# ========== SIDEBAR FOOTER ==========
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Clear Cache & Refresh"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption(f"📊 Open: {len(st.session_state.active_trades)}/{MAX_POSITIONS} · "
                   f"Heat: ${portfolio_heat():,.0f}")
st.sidebar.caption(f"📓 Closed trades: {len(st.session_state.trade_history)}")
