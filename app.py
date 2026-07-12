import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ta
from datetime import datetime, timedelta
import time
import requests
import json

# ========== PAGE CONFIG ==========
st.set_page_config(
    page_title="Pro Swing Commander",
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
st.sidebar.title("⚙️ Pro Settings")

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
    max_value=5.0,
    value=1.0,
    step=0.1
)

atr_multiplier = st.sidebar.slider(
    "Stop Loss (ATR x)",
    min_value=0.5,
    max_value=3.0,
    value=2.0,
    step=0.5
)

st.sidebar.markdown("---")

st.sidebar.subheader("🔍 Scanner Filters")

min_score = st.sidebar.slider(
    "Minimum Score",
    min_value=30,
    max_value=80,
    value=50,
    step=5
)

st.sidebar.caption("📊 Data from Yahoo Finance")

# ========== TABS ==========
tab1, tab2, tab3 = st.tabs(["📊 Pro Scanner", "📈 Active Trades", "📓 Journal"])

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

# ========== GET SIGNALS ==========
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

# ========== GET STOCK UNIVERSE ==========
@st.cache_data(ttl=3600)
def get_stock_universe():
    return [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'NFLX', 'AMD', 'INTC',
        'ORCL', 'IBM', 'CSCO', 'QCOM', 'TXN', 'AVGO', 'MU', 'LRCX', 'KLAC', 'AMAT',
        'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'V', 'MA', 'PYPL', 'SQ',
        'AXP', 'COF', 'DFS', 'SYF', 'ALLY', 'USB', 'PNC', 'TFC', 'MTB', 'FITB',
        'JNJ', 'PFE', 'MRK', 'ABBV', 'UNH', 'CVS', 'ABT', 'TMO', 'DHR', 'AMGN',
        'GILD', 'BMY', 'REGN', 'VRTX', 'BIIB', 'ILMN', 'MTD', 'WST', 'ZBH', 'SYK',
        'WMT', 'TGT', 'COST', 'HD', 'LOW', 'MCD', 'SBUX', 'NKE', 'DIS', 'CMCSA',
        'UBER', 'LYFT', 'DASH', 'ETSY', 'CVNA', 'ABNB', 'BKNG', 'EXPE', 'RCL', 'CCL',
        'BA', 'CAT', 'GE', 'DE', 'F', 'GM', 'RTX', 'LMT', 'NOC', 'GD',
        'HON', 'MMM', 'PH', 'EMR', 'ETN', 'ITW', 'CMI', 'PCAR', 'RSG', 'WM',
        'XOM', 'CVX', 'COP', 'PSX', 'VLO', 'MPC', 'MRO', 'EOG', 'PXD', 'FANG',
        'DVN', 'OXY', 'APA', 'HES', 'NBL', 'T', 'VZ', 'TMUS', 'CHTR', 'DISH',
        'ROKU', 'SPOT', 'SIRI', 'AMCX', 'FOXA', 'VIAC', 'PARA', 'WBD', 'NYT', 'GCI',
        'NEE', 'DUK', 'SO', 'D', 'AEP', 'EXC', 'SRE', 'PEG', 'PCG', 'ED',
        'PLTR', 'SNOW', 'DDOG', 'MDB', 'ZS', 'NET', 'CRWD', 'PANW', 'FTNT', 'OKTA',
        'SMCI', 'DELL', 'HPQ', 'WDC', 'STX', 'NTAP', 'PSTG', 'AFRM', 'UPST', 'SOFI',
        'HOOD', 'COIN', 'RIOT', 'MARA', 'SI', 'GLXY', 'HUT'
    ]

# ========== GET NEWS ==========
@st.cache_data(ttl=1800)
def get_news(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            news = data.get('news', [])
            headlines = []
            for item in news[:5]:
                headlines.append(item.get('title', ''))
            return headlines
        else:
            return []
    except:
        return []

# ========== CALCULATE FUNDAMENTAL SCORE ==========
def calculate_fundamental_score(info):
    score = 0
    
    pe = info.get('trailingPE', None)
    if pe:
        if pe < 15:
            score += 20
        elif pe < 25:
            score += 10
        elif pe < 35:
            score += 5
    
    roe = info.get('returnOnEquity', None)
    if roe:
        if roe > 0.25:
            score += 20
        elif roe > 0.15:
            score += 10
        elif roe > 0.10:
            score += 5
    
    debt_to_equity = info.get('debtToEquity', None)
    if debt_to_equity:
        if debt_to_equity < 0.3:
            score += 15
        elif debt_to_equity < 0.6:
            score += 8
        elif debt_to_equity < 1.0:
            score += 3
    
    profit_margin = info.get('profitMargins', None)
    if profit_margin:
        if profit_margin > 0.20:
            score += 15
        elif profit_margin > 0.10:
            score += 8
        elif profit_margin > 0.05:
            score += 3
    
    revenue_growth = info.get('revenueGrowth', None)
    if revenue_growth:
        if revenue_growth > 0.20:
            score += 15
        elif revenue_growth > 0.10:
            score += 8
        elif revenue_growth > 0.05:
            score += 3
    
    earnings_growth = info.get('earningsGrowth', None)
    if earnings_growth:
        if earnings_growth > 0.20:
            score += 15
        elif earnings_growth > 0.10:
            score += 8
        elif earnings_growth > 0.05:
            score += 3
    
    return min(score, 100)

# ========== POSITION SIZING ==========
def calc_position(price, atr, account, risk_pct, atr_mult):
    risk_dollars = account * (risk_pct / 100)
    stop_distance = atr * atr_mult
    
    if stop_distance < price * 0.01:
        stop_distance = price * 0.01
    
    shares = risk_dollars / stop_distance
    
    if shares < 1:
        shares = round(shares, 2)
    else:
        shares = int(shares)
    
    stop_price = price - stop_distance
    target_price = price + (stop_distance * 2)
    
    if stop_price < price * 0.85:
        stop_price = price * 0.85
        stop_distance = price - stop_price
        shares = risk_dollars / stop_distance
        if shares < 1:
            shares = round(shares, 2)
        else:
            shares = int(shares)
    
    return shares, stop_price, target_price, risk_dollars

# ========== PRO SCANNER ==========
def pro_scan():
    universe = get_stock_universe()
    results = []
    is_weekend = datetime.now().weekday() >= 5
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total = len(universe)
    
    for i, tkr in enumerate(universe):
        status_text.text(f"Scanning {i+1}/{total}: {tkr}")
        progress_bar.progress((i + 1) / total)
        
        try:
            stock = yf.Ticker(tkr)
            hist = stock.history(period="4mo")
            info = stock.info
            
            if hist.empty or len(hist) < 50:
                continue
            
            latest = hist.iloc[-1]
            last_5 = hist.tail(5)
            
            sma_20 = hist['SMA_20'].iloc[-1]
            sma_50 = hist['SMA_50'].iloc[-1]
            sma_200 = hist['SMA_200'].iloc[-1]
            rsi = ta.momentum.RSIIndicator(hist['Close'], window=14).rsi().iloc[-1]
            vol_ma = hist['Volume'].rolling(20).mean().iloc[-1]
            atr = hist['ATR'].iloc[-1] if 'ATR' in hist.columns else None
            
            if pd.isna(sma_20) or pd.isna(sma_50) or pd.isna(rsi):
                continue
            
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
                if vol_ma > 0 and latest['Volume'] > 1.2 * vol_ma:
                    tech_score += 15
                    tech_signals.append("Volume Surge")
                elif latest['Volume'] > vol_ma * 0.8:
                    tech_score += 5
            else:
                tech_score += 10
                tech_signals.append("Weekend Mode")
            
            if len(last_5) >= 5:
                if latest['Close'] > last_5['Close'].mean():
                    tech_score += 10
                    tech_signals.append("Recent Strength")
            
            if not pd.isna(sma_200) and latest['Close'] > sma_200:
                tech_score += 15
                tech_signals.append("Long-term Trend")
            
            tech_score = min(tech_score, 100)
            
            fund_score = calculate_fundamental_score(info)
            
            catalyst_score = 0
            catalyst_signals = []
            
            news = get_news(tkr)
            if news:
                catalyst_score += 20
                catalyst_signals.append("Recent News")
            
            next_earnings = info.get('earningsDate', None)
            if next_earnings:
                if isinstance(next_earnings, list) and len(next_earnings) > 0:
                    catalyst_score += 10
                    catalyst_signals.append("Earnings Upcoming")
            
            short_ratio = info.get('shortRatio', None)
            if short_ratio and short_ratio > 3:
                catalyst_score += 10
                catalyst_signals.append("High Short Interest")
            
            catalyst_score = min(catalyst_score, 100)
            
            final_score = (tech_score * 0.5) + (fund_score * 0.3) + (catalyst_score * 0.2)
            
            market_cap = info.get('marketCap', 0)
            market_cap_display = f"${round(market_cap / 1_000_000_000, 2)}B" if market_cap > 1_000_000_000 else f"${round(market_cap / 1_000_000, 0)}M"
            
            if final_score >= st.session_state.get('min_score', 50):
                results.append({
                    'Ticker': tkr,
                    'Price': round(latest['Close'], 2),
                    'RSI': round(rsi, 1),
                    'Volume Surge': round(latest['Volume'] / vol_ma, 1) if vol_ma > 0 else 1.0,
                    'Market Cap': market_cap_display,
                    'Tech Score': round(tech_score, 0),
                    'Fundamental Score': round(fund_score, 0),
                    'Catalyst Score': round(catalyst_score, 0),
                    'Total Score': round(final_score, 0),
                    'Tech Signals': ' | '.join(tech_signals[:3]),
                    'Catalyst Signals': ' | '.join(catalyst_signals[:2]) if catalyst_signals else 'None',
                    'ATR': round(atr, 2) if atr else None,
                    'P/E': round(info.get('trailingPE', 0), 1) if info.get('trailingPE') else None,
                    'ROE': f"{round(info.get('returnOnEquity', 0) * 100, 1)}%" if info.get('returnOnEquity') else None
                })
            
            time.sleep(0.1)
            
        except Exception as e:
            continue
    
    progress_bar.empty()
    status_text.empty()
    
    results = sorted(results, key=lambda x: x['Total Score'], reverse=True)
    return results

# ========== TAB 1: PRO SCANNER ==========
with tab1:
    st.title("📊 Pro Swing Commander")
    st.markdown("---")
    
    st.subheader("🔍 Pro Scanner")
    st.caption("Combining Technicals + Fundamentals + Catalysts across 200+ stocks")
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        scan_btn = st.button("🚀 Run Pro Scan", type="primary")
    
    st.session_state['min_score'] = min_score
    
    if scan_btn:
        with st.spinner("Running pro scan... (this may take 1-2 minutes)"):
            results = pro_scan()
            st.session_state.scan_results = results
        
        if st.session_state.scan_results:
            st.success(f"✅ Found {len(st.session_state.scan_results)} stocks matching your criteria!")
            st.markdown("---")
            
            df_results = pd.DataFrame(st.session_state.scan_results)
            
            st.dataframe(
                df_results,
                column_config={
                    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "Price": st.column_config.NumberColumn("Price", format="$%.2f"),
                    "RSI": st.column_config.NumberColumn("RSI", format="%.1f"),
                    "Volume Surge": st.column_config.NumberColumn("Volume Surge", format="%.1fx"),
                    "Market Cap": st.column_config.TextColumn("Market Cap", width="small"),
                    "Tech Score": st.column_config.ProgressColumn("Tech", format="%d", min_value=0, max_value=100),
                    "Fundamental Score": st.column_config.ProgressColumn("Fund", format="%d", min_value=0, max_value=100),
                    "Catalyst Score": st.column_config.ProgressColumn("Catalyst", format="%d", min_value=0, max_value=100),
                    "Total Score": st.column_config.ProgressColumn("Total", format="%d", min_value=0, max_value=100),
                    "Tech Signals": st.column_config.TextColumn("Tech Signals"),
                    "Catalyst Signals": st.column_config.TextColumn("Catalyst"),
                    "P/E": st.column_config.NumberColumn("P/E", format="%.1f"),
                    "ROE": st.column_config.TextColumn("ROE"),
                },
                hide_index=True,
                use_container_width=True
            )
            
            st.markdown("---")
            
            st.subheader("📈 Quick Analyze")
            top_picks = df_results.head(5)['Ticker'].tolist()
            cols = st.columns(len(top_picks))
            for idx, ticker in enumerate(top_picks):
                with cols[idx]:
                    if st.button(f"{ticker}", key=f"quick_{ticker}"):
                        st.session_state.quick_ticker = ticker
                        st.rerun()
        else:
            st.warning(f"No stocks passed the filters (minimum score: {min_score}). Try lowering the minimum score.")
    
    st.markdown("---")
    
    analysis_ticker = st.session_state.quick_ticker if st.session_state.quick_ticker else ticker
    
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
            
            pe = info.get('trailingPE', None)
            col5.metric("🧮 P/E", f"{pe:.2f}" if pe else "N/A")
            
            st.subheader("📊 Fundamental Snapshot")
            fund_score = calculate_fundamental_score(info)
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("📊 ROE", f"{round(info.get('returnOnEquity', 0) * 100, 1)}%" if info.get('returnOnEquity') else "N/A")
            col2.metric("📊 P/E", f"{info.get('trailingPE', 'N/A')}")
            col3.metric("📊 Profit Margin", f"{round(info.get('profitMargins', 0) * 100, 1)}%" if info.get('profitMargins') else "N/A")
            col4.metric("📊 Fundamental Score", f"{fund_score}/100")
            
            current_atr = latest['ATR'] if 'ATR' in df.columns else None
            if current_atr and not pd.isna(current_atr) and current_atr > 0:
                shares, stop_price, target_price, risk_dollars = calc_position(
                    latest['Close'], current_atr, account_size, risk_percent, atr_multiplier
                )
                
                st.subheader("🎯 Trade Setup")
                col_a, col_b, col_c, col_d = st.columns(4)
                
                if isinstance(shares, float) and shares < 1:
                    shares_display = f"{shares:.2f} shares (fractional)"
                elif isinstance(shares, float):
                    shares_display = f"{shares:.2f} shares"
                else:
                    shares_display = f"{shares:,} shares"
                
                col_a.metric("📦 Shares", shares_display)
                col_b.metric("🛑 Stop Loss", f"${stop_price:.2f}")
                col_c.metric("🎯 Target", f"${target_price:.2f}")
                col_d.metric("⚠️ Max Risk", f"${risk_dollars:,.2f} ({risk_percent}%)")
                
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
            
            st.subheader("📰 News")
            news_headlines = get_news(analysis_ticker)
            if news_headlines:
                for headline in news_headlines[:5]:
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
                    if isinstance(shares, float):
                        st.caption(f"Shares: {shares:.2f}")
                    else:
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
        st.info("No active trades. Use the pro scanner to find picks!")

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
                shares = st.number_input("Shares", min_value=0.01, step=0.01, value=100.0)
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
is_weekend = datetime.now().weekday() >= 5
if is_weekend:
    st.sidebar.caption("📌 Weekend Mode: Volume filter disabled")
else:
    st.sidebar.caption("📌 Market Hours: 9:30 AM - 4:00 PM EST")
st.sidebar.caption(f"💰 Account: ${account_size:,}")
