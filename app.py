import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ta
from datetime import datetime, timedelta
import time
import sqlite3
import requests
import json

# ========== PAGE CONFIG ==========
st.set_page_config(
    page_title="Swing Commander",
    page_icon="📈",
    layout="wide"
)

# ========== INITIALIZE DATABASE ==========
def init_db():
    conn = sqlite3.connect('trades.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            entry_date TEXT,
            entry_price REAL,
            shares INTEGER,
            stop_price REAL,
            target_price REAL,
            exit_date TEXT,
            exit_price REAL,
            pnl REAL,
            notes TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ========== SIDEBAR ==========
st.sidebar.title("⚙️ Settings")

# Stock Input
ticker = st.sidebar.text_input("Search Stock", value="AAPL").upper()

# Time Period
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

# ========== POSITION SIZING ==========
st.sidebar.subheader("💰 Position Sizing")

account_size = st.sidebar.number_input(
    "Account Size ($)",
    min_value=100,
    max_value=10000000,
    value=50000,
    step=1000
)

risk_percent = st.sidebar.slider(
    "Risk per Trade (%)",
    min_value=0.5,
    max_value=3.0,
    value=1.0,
    step=0.1
)

atr_multiplier = st.sidebar.slider(
    "Stop Loss (ATR x)",
    min_value=1.0,
    max_value=3.0,
    value=2.0,
    step=0.5
)

st.sidebar.markdown("---")
st.sidebar.caption("📊 Data from Yahoo Finance")

# ========== TABS ==========
tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "📈 Active Trades", "📓 Journal"])

# ========== DATA FETCH ==========
@st.cache_data(ttl=300)
def fetch_data(ticker, period):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return None, None, "No data found"
        
        info = stock.info
        
        # Technical Indicators
        df['SMA_20'] = df['Close'].rolling(20).mean()
        df['SMA_50'] = df['Close'].rolling(50).mean()
        df['SMA_200'] = df['Close'].rolling(200).mean()
        df['RSI'] = ta.momentum.RSIIndicator(df['Close'], window=14).rsi()
        
        macd = ta.trend.MACD(df['Close'])
        df['MACD'] = macd.macd()
        df['MACD_Signal'] = macd.macd_signal()
        df['MACD_Hist'] = macd.macd_diff()
        
        df['ATR'] = ta.volatility.AverageTrueRange(df['High'], df['Low'], df['Close'], window=14).average_true_range()
        df['Volume_MA'] = df['Volume'].rolling(20).mean()
        
        return df, info, None
    except Exception as e:
        return None, None, str(e)

# ========== SIGNALS ==========
def get_signals(df):
    if df is None or len(df) < 20:
        return ["Not enough data"], "NEUTRAL", 0
    
    latest = df.iloc[-1]
    signals = []
    score = 0
    
    if not pd.isna(latest['RSI']):
        if latest['RSI'] < 30:
            signals.append("🟢 RSI Oversold - BUY Signal")
            score += 1
        elif latest['RSI'] > 70:
            signals.append("🔴 RSI Overbought - SELL Signal")
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
    
    if not pd.isna(latest['MACD']) and not pd.isna(latest['MACD_Signal']):
        if latest['MACD'] > latest['MACD_Signal']:
            signals.append("🟢 MACD Bullish Crossover")
            score += 1
        else:
            signals.append("🔴 MACD Bearish Crossover")
            score -= 1
    
    if score >= 2:
        rec = "🟢 BUY"
    elif score <= -2:
        rec = "🔴 SELL"
    else:
        rec = "⚪ HOLD"
    
    return signals, rec, score

# ========== POSITION SIZING ==========
def calc_position(price, atr, account, risk_pct, atr_mult):
    risk_dollars = account * (risk_pct / 100)
    stop_distance = atr * atr_mult
    shares = int(risk_dollars / stop_distance) if stop_distance > 0 else 0
    stop_price = price - stop_distance
    target_price = price + (stop_distance * 2)
    return shares, stop_price, target_price, risk_dollars

# ========== FULL MARKET SCANNER (Using Yahoo Finance Screener) ==========
def scan_full_market():
    """Scan the entire US market using Yahoo Finance screener"""
    
    results = []
    
    try:
        # Use Yahoo Finance screener via yfscreen
        # First, try to import yfscreen
        try:
            import yfscreen as yfs
            
            # Set filters: US stocks, market cap $500M to $100B, volume > 1M
            filters = [
                ["eq", ["region", "us"]],
                ["btwn", ["intradaymarketcap", 500000000, 100000000000]],
                ["gt", ["dayvolume", 1000000]]
            ]
            
            query = yfs.create_query(filters)
            payload = yfs.create_payload("equity", query)
            data = yfs.get_data(payload)
            
            # Data comes back as a list of tickers
            tickers_to_scan = data[:200]  # Limit to 200 for speed
            
        except ImportError:
            # Fallback: Use the GitHub ticker list
            st.info("Using GitHub ticker list (install yfscreen for faster scanning)")
            url = "https://raw.githubusercontent.com/Ate329/top-us-stock-tickers/main/tickers/all.csv"
            df = pd.read_csv(url)
            tickers_to_scan = df['symbol'].head(200).tolist()
        
        # Now scan each ticker
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, tkr in enumerate(tickers_to_scan):
            tkr = tkr.strip().upper()
            status_text.text(f"Scanning {i+1}/{len(tickers_to_scan)}: {tkr}")
            progress_bar.progress((i + 1) / len(tickers_to_scan))
            
            try:
                stock = yf.Ticker(tkr)
                hist = stock.history(period="3mo")
                info = stock.info
                
                if hist.empty or len(hist) < 50:
                    continue
                
                latest = hist.iloc[-1]
                market_cap = info.get('marketCap', 0)
                
                if market_cap < 500_000_000 or market_cap > 100_000_000_000:
                    continue
                
                # Filter 1: Price > 50-day MA
                sma_50 = hist['SMA_50'].iloc[-1]
                if pd.isna(sma_50) or latest['Close'] <= sma_50:
                    continue
                
                # Filter 2: Volume > 1.5x average
                vol_ma = hist['Volume'].rolling(20).mean().iloc[-1]
                if pd.isna(vol_ma) or latest['Volume'] <= 1.5 * vol_ma:
                    continue
                
                # Filter 3: RSI between 40 and 70
                rsi = ta.momentum.RSIIndicator(hist['Close'], window=14).rsi().iloc[-1]
                if pd.isna(rsi) or rsi < 40 or rsi > 70:
                    continue
                
                # Passed all filters!
                results.append({
                    'Ticker': tkr,
                    'Price': round(latest['Close'], 2),
                    'RSI': round(rsi, 1),
                    'Volume Surge': round(latest['Volume'] / vol_ma, 1) if vol_ma > 0 else 0,
                    'Market Cap': f"${round(market_cap / 1_000_000_000, 2)}B",
                    'Score': min(100, 50 + (70 - rsi) + (latest['Volume'] / vol_ma if vol_ma > 0 else 0))
                })
                
                # Stop if we have enough
                if len(results) >= 10:
                    break
                
            except Exception:
                continue
        
        progress_bar.empty()
        status_text.empty()
        
    except Exception as e:
        st.error(f"Scanner error: {str(e)}")
    
    # Sort by Score and return top 3
    results = sorted(results, key=lambda x: x['Score'], reverse=True)
    return results[:3]

# ========== TAB 1: DASHBOARD ==========
with tab1:
    st.title("📊 Swing Commander")
    st.markdown("---")
    
    # ===== FULL MARKET SCANNER =====
    st.subheader("🔍 Full Market Scanner")
    st.caption("Scanning the entire US market for the best setups")
    
    col1, col2 = st.columns([1, 4])
    with col1:
        scan_btn = st.button("🚀 Scan Market", type="primary")
    
    if scan_btn:
        with st.spinner("Scanning 200+ stocks across the entire market..."):
            results = scan_full_market()
        
        if results:
            st.success(f"✅ Found {len(results)} top picks from the entire market!")
            
            # Display top 3 picks
            cols = st.columns(3)
            for idx, stock in enumerate(results):
                with cols[idx]:
                    st.subheader(f"#{idx+1} {stock['Ticker']}")
                    st.metric("Price", f"${stock['Price']}")
                    st.metric("RSI", stock['RSI'])
                    st.metric("Volume Surge", f"{stock['Volume Surge']}x")
                    st.caption(f"Market Cap: {stock['Market Cap']}")
                    st.caption(f"Score: {stock['Score']:.0f}/100")
        else:
            st.warning("No stocks passed the filters today. Try again later.")
    
    st.markdown("---")
    
    # ===== INDIVIDUAL STOCK ANALYSIS =====
    if ticker:
        st.subheader(f"📊 {ticker} Analysis")
        
        df, info, error = fetch_data(ticker, period)
        
        if error:
            st.error(f"❌ {error}")
        else:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            change = latest['Close'] - prev['Close']
            change_pct = (change / prev['Close']) * 100 if prev['Close'] != 0 else 0
            
            # Metrics
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("💰 Price", f"${latest['Close']:.2f}", f"{change:+.2f} ({change_pct:+.2f}%)")
            col2.metric("📊 Volume", f"{latest['Volume']:,.0f}")
            col3.metric("📈 Day High", f"${latest['High']:.2f}")
            col4.metric("📉 Day Low", f"${latest['Low']:.2f}")
            
            pe = info.get('trailingPE', None)
            col5.metric("🧮 P/E", f"{pe:.2f}" if pe else "N/A")
            
            # Position Sizing
            current_atr = latest['ATR']
            if not pd.isna(current_atr) and current_atr > 0:
                shares, stop_price, target_price, risk_dollars = calc_position(
                    latest['Close'], current_atr, account_size, risk_percent, atr_multiplier
                )
                
                st.subheader("🎯 Trade Setup")
                col_a, col_b, col_c, col_d = st.columns(4)
                col_a.metric("📦 Shares", f"{shares:,}")
                col_b.metric("🛑 Stop Loss", f"${stop_price:.2f}")
                col_c.metric("🎯 Target", f"${target_price:.2f}")
                col_d.metric("⚠️ Max Risk", f"${risk_dollars:,.2f}")
            
            # Signals
            signals, rec, score = get_signals(df)
            
            col_a, col_b = st.columns([2, 1])
            with col_a:
                st.subheader("📡 Signals")
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
            
            # Chart
            st.subheader("📉 Price Chart")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='Close', line=dict(color='white', width=2)))
            fig.add_trace(go.Scatter(x=df.index, y=df['SMA_50'], mode='lines', name='SMA 50', line=dict(color='blue', width=1, dash='dash')))
            fig.add_trace(go.Scatter(x=df.index, y=df['SMA_200'], mode='lines', name='SMA 200', line=dict(color='red', width=1, dash='dash')))
            fig.update_layout(height=400, template='plotly_dark', xaxis_title='Date', yaxis_title='Price')
            st.plotly_chart(fig, use_container_width=True)

# ========== TAB 2: ACTIVE TRADES ==========
with tab2:
    st.title("📈 Active Trades")
    st.markdown("---")
    
    conn = sqlite3.connect('trades.db')
    c = conn.cursor()
    
    c.execute('''
        SELECT id, ticker, entry_date, entry_price, shares, stop_price, target_price, status
        FROM trades WHERE status = 'ACTIVE'
    ''')
    active = c.fetchall()
    
    if active:
        for trade in active:
            trade_id, ticker, entry_date, entry_price, shares, stop_price, target_price, status = trade
            
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                current_price = hist['Close'].iloc[-1]
                pnl = (current_price - entry_price) * shares
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                days_held = (datetime.now() - datetime.strptime(entry_date, '%Y-%m-%d')).days
            else:
                current_price = entry_price
                pnl = 0
                pnl_pct = 0
                days_held = 0
            
            with st.container():
                col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
                with col1:
                    st.subheader(ticker)
                    st.caption(f"Entry: {entry_date} | Days Held: {days_held}")
                with col2:
                    st.metric("Entry", f"${entry_price:.2f}")
                with col3:
                    st.metric("Current", f"${current_price:.2f}")
                with col4:
                    delta_color = "normal"
                    if pnl > 0:
                        delta_color = "normal"
                    elif pnl < 0:
                        delta_color = "inverse"
                    st.metric("P&L", f"${pnl:.2f}", f"{pnl_pct:+.1f}%", delta_color=delta_color)
                with col5:
                    st.metric("Stop", f"${stop_price:.2f}")
                    st.caption(f"Target: ${target_price:.2f}")
                
                col1, col2, col3 = st.columns([1, 1, 3])
                with col1:
                    if st.button(f"✏️ Edit {ticker}", key=f"edit_{trade_id}"):
                        st.info("Edit coming soon")
                with col2:
                    if st.button(f"✅ Close {ticker}", key=f"close_{trade_id}"):
                        st.session_state['closing_trade'] = trade_id
                        st.rerun()
                
                if st.session_state.get('closing_trade') == trade_id:
                    with st.form(key=f"close_form_{trade_id}"):
                        exit_price = st.number_input("Exit Price", value=current_price, step=0.01)
                        notes = st.text_area("Notes")
                        submitted = st.form_submit_button("Confirm Close")
                        if submitted:
                            pnl_final = (exit_price - entry_price) * shares
                            c.execute('''
                                UPDATE trades SET exit_date = ?, exit_price = ?, pnl = ?, notes = ?, status = 'CLOSED'
                                WHERE id = ?
                            ''', (datetime.now().strftime('%Y-%m-%d'), exit_price, pnl_final, notes, trade_id))
                            conn.commit()
                            st.session_state['closing_trade'] = None
                            st.success(f"✅ {ticker} closed!")
                            st.rerun()
                
                st.markdown("---")
    else:
        st.info("No active trades. Scan for picks and start trading!")

# ========== TAB 3: JOURNAL ==========
with tab3:
    st.title("📓 Trade Journal")
    st.markdown("---")
    
    with st.expander("➕ Add New Trade"):
        with st.form("new_trade"):
            col1, col2 = st.columns(2)
            with col1:
                ticker_input = st.text_input("Ticker", value="AAPL").upper()
                entry_price = st.number_input("Entry Price", min_value=0.01, step=0.01)
                shares = st.number_input("Shares", min_value=1, step=1)
            with col2:
                stop_price = st.number_input("Stop Loss Price", min_value=0.01, step=0.01)
                target_price = st.number_input("Target Price", min_value=0.01, step=0.01)
                notes = st.text_area("Notes")
            
            submitted = st.form_submit_button("Add Trade")
            if submitted:
                conn = sqlite3.connect('trades.db')
                c = conn.cursor()
                c.execute('''
                    INSERT INTO trades (ticker, entry_date, entry_price, shares, stop_price, target_price, notes, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
                ''', (ticker_input, datetime.now().strftime('%Y-%m-%d'), entry_price, shares, stop_price, target_price, notes))
                conn.commit()
                conn.close()
                st.success(f"✅ {ticker_input} added to active trades!")
                st.rerun()
    
    st.subheader("📊 Trade History")
    
    conn = sqlite3.connect('trades.db')
    c = conn.cursor()
    c.execute('''
        SELECT ticker, entry_date, entry_price, exit_date, exit_price, shares, pnl, notes, status
        FROM trades ORDER BY id DESC
    ''')
    history = c.fetchall()
    conn.close()
    
    if history:
        for trade in history:
            ticker, entry_date, entry_price, exit_date, exit_price, shares, pnl, notes, status = trade
            
            with st.container():
                col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
                with col1:
                    st.subheader(ticker)
                    st.caption(f"Entry: {entry_date} | Exit: {exit_date if exit_date else 'Open'}")
                with col2:
                    st.metric("Entry", f"${entry_price:.2f}")
                    st.metric("Exit", f"${exit_price:.2f}" if exit_price else "-")
                with col3:
                    if pnl:
                        delta_color = "normal" if pnl > 0 else "inverse"
                        st.metric("P&L", f"${pnl:.2f}", delta_color=delta_color)
                    else:
                        st.metric("P&L", "-")
                with col4:
                    if notes:
                        st.caption(f"📝 {notes}")
                
                st.markdown("---")
    else:
        st.info("No trades logged yet.")

# ========== SIDEBAR - QUICK ACTIONS ==========
st.sidebar.markdown("---")
st.sidebar.subheader("⚡ Quick Actions")

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

conn = sqlite3.connect('trades.db')
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM trades WHERE status = 'ACTIVE'")
active_count = c.fetchone()[0]
conn.close()
st.sidebar.caption(f"📊 Active Trades: {active_count}")
