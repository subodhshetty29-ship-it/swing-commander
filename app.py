import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ta
from datetime import datetime, timedelta
import time

# ========== PAGE CONFIG ==========
st.set_page_config(
    page_title="Swing Commander",
    page_icon="📈",
    layout="wide"
)

# ========== INITIALIZE SESSION STATE ==========
def init_session():
    if 'active_trades' not in st.session_state:
        st.session_state.active_trades = []
    if 'trade_history' not in st.session_state:
        st.session_state.trade_history = []
    if 'scan_results' not in st.session_state:
        st.session_state.scan_results = []
    if 'quick_ticker' not in st.session_state:
        st.session_state.quick_ticker = "AAPL"
    if 'closing_trade' not in st.session_state:
        st.session_state.closing_trade = None

init_session()

# ========== SIDEBAR ==========
st.sidebar.title("⚙️ Settings")

ticker = st.sidebar.text_input("Search Stock", value="AAPL").upper()

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
@st.cache_data(ttl=600)
def fetch_data(ticker, period):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return None, None, "No data found"
        
        info = stock.info
        
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

# ========== CURATED WATCHLIST (Quality > Quantity) ==========
@st.cache_data(ttl=3600)
def get_watchlist():
    """Curated list of 40 high-quality, liquid stocks across sectors"""
    return [
        # Technology (10)
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'NFLX', 'AMD', 'ORCL', 'CSCO',
        # Financials (5)
        'JPM', 'BAC', 'GS', 'V', 'MA',
        # Healthcare (5)
        'JNJ', 'PFE', 'MRK', 'ABBV', 'UNH',
        # Consumer (5)
        'WMT', 'COST', 'HD', 'MCD', 'NKE',
        # Industrials (5)
        'BA', 'CAT', 'GE', 'DE', 'HON',
        # Energy (5)
        'XOM', 'CVX', 'COP', 'PSX', 'SLB',
        # Communication (5)
        'T', 'VZ', 'TMUS', 'CMCSA', 'DIS'
    ]

def scan_watchlist():
    """Scan the curated watchlist (40 stocks, fast and reliable)"""
    watchlist = get_watchlist()
    results = []
    
    for tkr in watchlist:
        try:
            stock = yf.Ticker(tkr)
            hist = stock.history(period="3mo")
            info = stock.info
            
            if hist.empty or len(hist) < 50:
                continue
            
            latest = hist.iloc[-1]
            
            # Calculate indicators
            sma_20 = hist['SMA_20'].iloc[-1]
            sma_50 = hist['SMA_50'].iloc[-1]
            sma_200 = hist['SMA_200'].iloc[-1]
            rsi = ta.momentum.RSIIndicator(hist['Close'], window=14).rsi().iloc[-1]
            vol_ma = hist['Volume'].rolling(20).mean().iloc[-1]
            atr = hist['ATR'].iloc[-1] if 'ATR' in hist.columns else None
            
            if pd.isna(sma_20) or pd.isna(sma_50) or pd.isna(rsi) or pd.isna(vol_ma):
                continue
            
            # ===== Scoring System =====
            score = 0
            signals = []
            
            # 1. Uptrend (Price > 50-day MA)
            if latest['Close'] > sma_50:
                score += 20
                signals.append("Uptrend")
            
            # 2. Momentum (Price > 20-day MA)
            if latest['Close'] > sma_20:
                score += 15
                signals.append("Momentum")
            
            # 3. Not overbought (RSI < 70)
            if rsi < 70:
                score += 15
                signals.append("Not Overbought")
            elif rsi < 80:
                score += 5
                signals.append("RSI: " + str(round(rsi)))
            
            # 4. Volume surge (Volume > 1.2x average)
            if latest['Volume'] > 1.2 * vol_ma:
                score += 15
                signals.append("Volume Surge")
            
            # 5. RSI sweet spot (40-65)
            if 40 <= rsi <= 65:
                score += 20
                signals.append("RSI Sweet Spot")
            
            # 6. Long-term trend (Price > 200-day MA)
            if not pd.isna(sma_200) and latest['Close'] > sma_200:
                score += 15
                signals.append("Long-term Uptrend")
            
            # Only include if score is decent
            if score >= 50:
                results.append({
                    'Ticker': tkr,
                    'Price': round(latest['Close'], 2),
                    'RSI': round(rsi, 1),
                    'Volume Surge': round(latest['Volume'] / vol_ma, 1),
                    'Score': score,
                    'Signals': ' | '.join(signals),
                    'ATR': round(atr, 2) if atr else None
                })
            
            # Small delay to avoid rate limiting
            time.sleep(0.2)
            
        except Exception as e:
            continue
    
    # Sort by Score (highest first)
    results = sorted(results, key=lambda x: x['Score'], reverse=True)
    return results[:5]  # Return top 5

# ========== TAB 1: DASHBOARD ==========
with tab1:
    st.title("📊 Swing Commander")
    st.markdown("---")
    
    # ===== SCANNER SECTION =====
    st.subheader("🔍 Scanner")
    st.caption("Scanning 40 high-quality stocks across all sectors")
    
    col1, col2 = st.columns([1, 4])
    with col1:
        scan_btn = st.button("🚀 Scan", type="primary")
    
    if scan_btn:
        with st.spinner("Scanning watchlist..."):
            results = scan_watchlist()
            st.session_state.scan_results = results
        
        if st.session_state.scan_results:
            st.success(f"✅ Found {len(st.session_state.scan_results)} setups!")
            st.markdown("---")
            
            # Display as a table
            df_results = pd.DataFrame(st.session_state.scan_results)
            st.dataframe(
                df_results,
                column_config={
                    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "Price": st.column_config.NumberColumn("Price", format="$%.2f"),
                    "RSI": st.column_config.NumberColumn("RSI", format="%.1f"),
                    "Volume Surge": st.column_config.NumberColumn("Volume Surge", format="%.1fx"),
                    "Score": st.column_config.ProgressColumn("Score", format="%d", min_value=0, max_value=100),
                    "Signals": st.column_config.TextColumn("Signals"),
                    "ATR": st.column_config.NumberColumn("ATR", format="$%.2f")
                },
                hide_index=True,
                use_container_width=True
            )
            
            # Quick analyze buttons
            st.subheader("📈 Quick Analyze")
            cols = st.columns(min(len(results), 5))
            for idx, stock in enumerate(results):
                if idx < 5:
                    with cols[idx]:
                        if st.button(f"{stock['Ticker']}", key=f"quick_{stock['Ticker']}"):
                            st.session_state.quick_ticker = stock['Ticker']
                            st.rerun()
        else:
            st.warning("No setups found today. Try again during market hours.")
    
    st.markdown("---")
    
    # ===== INDIVIDUAL STOCK ANALYSIS =====
    analysis_ticker = st.session_state.quick_ticker if st.session_state.quick_ticker else ticker
    
    if analysis_ticker:
        st.subheader(f"📊 {analysis_ticker} Analysis")
        
        df, info, error = fetch_data(analysis_ticker, period)
        
        if error:
            st.error(f"❌ {error}")
            if "Too Many Requests" in error:
                st.info("💡 Yahoo Finance is rate limiting. Wait 60 seconds and try again.")
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
            
            pe = info.get('trailingPE', None)
            col5.metric("🧮 P/E", f"{pe:.2f}" if pe else "N/A")
            
            current_atr = latest['ATR'] if 'ATR' in df.columns else None
            if current_atr and not pd.isna(current_atr) and current_atr > 0:
                shares, stop_price, target_price, risk_dollars = calc_position(
                    latest['Close'], current_atr, account_size, risk_percent, atr_multiplier
                )
                
                st.subheader("🎯 Trade Setup")
                col_a, col_b, col_c, col_d = st.columns(4)
                col_a.metric("📦 Shares", f"{shares:,}")
                col_b.metric("🛑 Stop Loss", f"${stop_price:.2f}")
                col_c.metric("🎯 Target", f"${target_price:.2f}")
                col_d.metric("⚠️ Max Risk", f"${risk_dollars:,.2f}")
                
                if st.button(f"➕ Add {analysis_ticker} to Active Trades"):
                    new_trade = {
                        'id': len(st.session_state.active_trades) + 1,
                        'ticker': analysis_ticker,
                        'entry_date': datetime.now().strftime('%Y-%m-%d'),
                        'entry_price': latest['Close'],
                        'shares': shares,
                        'stop_price': stop_price,
                        'target_price': target_price,
                        'status': 'ACTIVE'
                    }
                    st.session_state.active_trades.append(new_trade)
                    st.success(f"✅ {analysis_ticker} added to active trades!")
                    st.rerun()
            
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
            
            st.subheader("📉 Price Chart")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='Close', line=dict(color='white', width=2)))
            fig.add_trace(go.Scatter(x=df.index, y=df['SMA_20'], mode='lines', name='SMA 20', line=dict(color='orange', width=1, dash='dot')))
            fig.add_trace(go.Scatter(x=df.index, y=df['SMA_50'], mode='lines', name='SMA 50', line=dict(color='blue', width=1, dash='dash')))
            fig.add_trace(go.Scatter(x=df.index, y=df['SMA_200'], mode='lines', name='SMA 200', line=dict(color='red', width=1, dash='dash')))
            fig.update_layout(height=400, template='plotly_dark', xaxis_title='Date', yaxis_title='Price')
            st.plotly_chart(fig, use_container_width=True)

# ========== TAB 2: ACTIVE TRADES ==========
with tab2:
    st.title("📈 Active Trades")
    st.markdown("---")
    
    if st.session_state.active_trades:
        for idx, trade in enumerate(st.session_state.active_trades):
            ticker = trade['ticker']
            entry_date = trade['entry_date']
            entry_price = trade['entry_price']
            shares = trade['shares']
            stop_price = trade['stop_price']
            target_price = trade['target_price']
            
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d")
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
                st.subheader(f"📊 {ticker}")
                col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
                with col1:
                    st.caption(f"Entry: {entry_date} | Days Held: {days_held}")
                    st.caption(f"Shares: {shares:,}")
                with col2:
                    st.metric("Entry", f"${entry_price:.2f}")
                with col3:
                    st.metric("Current", f"${current_price:.2f}")
                with col4:
                    delta_color = "normal" if pnl >= 0 else "inverse"
                    st.metric("P&L", f"${pnl:.2f}", f"{pnl_pct:+.1f}%", delta_color=delta_color)
                with col5:
                    st.metric("Stop", f"${stop_price:.2f}")
                    st.caption(f"Target: ${target_price:.2f}")
                
                col1, col2 = st.columns([1, 4])
                with col1:
                    if st.button(f"✅ Close {ticker}", key=f"close_{idx}"):
                        st.session_state.closing_trade = idx
                        st.rerun()
                
                if st.session_state.closing_trade == idx:
                    with st.form(key=f"close_form_{idx}"):
                        exit_price = st.number_input("Exit Price", value=current_price, step=0.01)
                        notes = st.text_area("Notes")
                        submitted = st.form_submit_button("Confirm Close")
                        if submitted:
                            pnl_final = (exit_price - entry_price) * shares
                            closed_trade = {
                                'ticker': ticker,
                                'entry_date': entry_date,
                                'entry_price': entry_price,
                                'exit_date': datetime.now().strftime('%Y-%m-%d'),
                                'exit_price': exit_price,
                                'shares': shares,
                                'pnl': pnl_final,
                                'notes': notes,
                                'status': 'CLOSED'
                            }
                            st.session_state.trade_history.append(closed_trade)
                            st.session_state.active_trades.pop(idx)
                            st.session_state.closing_trade = None
                            st.success(f"✅ {ticker} closed! P&L: ${pnl_final:.2f}")
                            st.rerun()
                
                st.markdown("---")
    else:
        st.info("No active trades. Use the scanner to find picks!")

# ========== TAB 3: JOURNAL ==========
with tab3:
    st.title("📓 Trade Journal")
    st.markdown("---")
    
    with st.expander("➕ Add New Trade Manually", expanded=False):
        with st.form("new_trade"):
            col1, col2 = st.columns(2)
            with col1:
                ticker_input = st.text_input("Ticker", value="AAPL").upper()
                entry_price = st.number_input("Entry Price", min_value=0.01, step=0.01, value=150.00)
                shares = st.number_input("Shares", min_value=1, step=1, value=100)
            with col2:
                stop_price = st.number_input("Stop Loss Price", min_value=0.01, step=0.01, value=140.00)
                target_price = st.number_input("Target Price", min_value=0.01, step=0.01, value=165.00)
                notes = st.text_area("Notes", placeholder="Why did you enter this trade?")
            
            submitted = st.form_submit_button("Add Trade")
            if submitted:
                new_trade = {
                    'id': len(st.session_state.active_trades) + 1,
                    'ticker': ticker_input,
                    'entry_date': datetime.now().strftime('%Y-%m-%d'),
                    'entry_price': entry_price,
                    'shares': shares,
                    'stop_price': stop_price,
                    'target_price': target_price,
                    'status': 'ACTIVE'
                }
                st.session_state.active_trades.append(new_trade)
                st.success(f"✅ {ticker_input} added to active trades!")
                st.rerun()
    
    st.subheader("📊 Trade History")
    
    if st.session_state.trade_history:
        total_trades = len(st.session_state.trade_history)
        winning_trades = sum(1 for t in st.session_state.trade_history if t['pnl'] and t['pnl'] > 0)
        total_pnl = sum(t['pnl'] for t in st.session_state.trade_history if t['pnl'])
        
        col1, col2, col3 = st.columns(3)
        col1.metric("📊 Total Trades", total_trades)
        col2.metric("✅ Win Rate", f"{round(winning_trades/total_trades*100)}%" if total_trades > 0 else "N/A")
        col3.metric("💰 Total P&L", f"${total_pnl:.2f}")
        
        st.markdown("---")
        
        for trade in st.session_state.trade_history[::-1]:
            ticker = trade['ticker']
            entry_date = trade['entry_date']
            entry_price = trade['entry_price']
            exit_date = trade['exit_date']
            exit_price = trade['exit_price']
            shares = trade['shares']
            pnl = trade['pnl']
            notes = trade['notes']
            
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
        st.info("No trades logged yet. Start your trading journey!")

# ========== SIDEBAR - QUICK ACTIONS ==========
st.sidebar.markdown("---")
st.sidebar.subheader("⚡ Quick Actions")

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption(f"📊 Active Trades: {len(st.session_state.active_trades)}")
st.sidebar.caption(f"📓 Trades History: {len(st.session_state.trade_history)}")

st.sidebar.markdown("---")
st.sidebar.caption("📌 Watchlist: 40 quality stocks across all sectors")
