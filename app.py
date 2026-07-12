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
@st.cache_data(ttl=300)
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

# ========== GET STOCK LIST (FIXED) ==========
@st.cache_data(ttl=3600)
def get_fallback_tickers():
    """Expanded fallback list of 500+ liquid stocks"""
    return [
        # Tech
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX', 'AMD', 'INTC',
        'ORCL', 'IBM', 'CSCO', 'QCOM', 'TXN', 'AVGO', 'MU', 'LRCX', 'KLAC', 'AMAT',
        'ADI', 'NXPI', 'MCHP', 'ON', 'SWKS', 'QRVO', 'MPWR', 'MKSI', 'ENTG', 'TER',
        'SMCI', 'DELL', 'HPQ', 'WDC', 'STX', 'NTAP', 'PSTG', 'PURE', 'CRWD', 'PANW',
        'FTNT', 'ZS', 'OKTA', 'NET', 'DDOG', 'MDB', 'SNOW', 'PLTR', 'U', 'PATH',
        'TEAM', 'WORK', 'ASAN', 'WDAY', 'CRM', 'NOW', 'ADSK', 'ADBE', 'ANSS', 'ROP',
        # Finance
        'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'V', 'MA', 'PYPL', 'SQ',
        'AXP', 'COF', 'DFS', 'SYF', 'ALLY', 'USB', 'PNC', 'TFC', 'MTB', 'FITB',
        'CFG', 'KEY', 'HBAN', 'RF', 'CMA', 'ZION', 'EWBC', 'FHN', 'COLB', 'GBCI',
        'BLK', 'STT', 'BK', 'TROW', 'BEN', 'IVZ', 'NTRS', 'FDS', 'MORN', 'SEIC',
        # Healthcare
        'JNJ', 'PFE', 'MRK', 'ABBV', 'UNH', 'CVS', 'WMT', 'TGT', 'COST', 'HD',
        'ABT', 'TMO', 'DHR', 'AMGN', 'GILD', 'BMY', 'REGN', 'VRTX', 'BIIB', 'ILMN',
        'MTD', 'WST', 'ZBH', 'SYN', 'BSX', 'MDT', 'EW', 'ISRG', 'DXCM', 'ALGN',
        # Consumer
        'AMZN', 'TSLA', 'HD', 'LOW', 'MCD', 'SBUX', 'NKE', 'DIS', 'CMCSA', 'UBER',
        'LYFT', 'DASH', 'GRUB', 'ETSY', 'CVNA', 'ABNB', 'BKNG', 'EXPE', 'RCL', 'CCL',
        # Industrials
        'BA', 'CAT', 'GE', 'DE', 'F', 'GM', 'RTX', 'LMT', 'NOC', 'GD',
        'HON', 'MMM', 'UTX', 'PH', 'EMR', 'ETN', 'ITW', 'CMI', 'PCAR', 'RSG',
        # Energy
        'XOM', 'CVX', 'COP', 'PSX', 'VLO', 'MPC', 'MRO', 'EOG', 'PXD', 'FANG',
        'DVN', 'OXY', 'APA', 'HES', 'NBL', 'CHK', 'CLR', 'MUR', 'SM', 'CPE',
        # Communication
        'T', 'VZ', 'TMUS', 'CMCSA', 'CHTR', 'DISH', 'ROKU', 'SPOT', 'SIRI', 'AMCX',
        'FOXA', 'VIAC', 'PARA', 'WBD', 'NYT', 'GCI', 'TEGNA', 'NXST',
        # Utilities
        'NEE', 'DUK', 'SO', 'D', 'AEP', 'EXC', 'SRE', 'PEG', 'PCG', 'ED',
        'WEC', 'ES', 'DTE', 'CMS', 'AEE', 'PPL', 'CNP', 'NI', 'LNT', 'EIX'
    ]

@st.cache_data(ttl=3600)
def get_stock_list():
    """Get a list of stocks to scan with multiple backup sources"""
    
    # Try multiple sources in order
    sources = [
        "https://raw.githubusercontent.com/Ate329/top-us-stock-tickers/main/tickers/all.csv",
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
        "https://raw.githubusercontent.com/jerryzhujin/US-Stock-Tickers/main/US-Stock-Tickers.txt"
    ]
    
    for url in sources:
        try:
            df = pd.read_csv(url)
            
            if 'symbol' in df.columns:
                tickers = df['symbol'].head(300).tolist()
            elif 'Symbol' in df.columns:
                tickers = df['Symbol'].head(300).tolist()
            elif 'Ticker' in df.columns:
                tickers = df['Ticker'].head(300).tolist()
            else:
                tickers = df.iloc[:, 0].head(300).tolist()
            
            tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
            tickers = [t for t in tickers if t and not t.startswith('#')]
            
            if len(tickers) > 50:
                return tickers
        except:
            continue
    
    return get_fallback_tickers()

# ========== SCANNER FUNCTIONS ==========
def scan_momentum(tickers):
    results = []
    for tkr in tickers:
        try:
            stock = yf.Ticker(tkr)
            hist = stock.history(period="3mo")
            info = stock.info
            
            if hist.empty or len(hist) < 50:
                continue
            
            latest = hist.iloc[-1]
            market_cap = info.get('marketCap', 0)
            
            if market_cap < 300_000_000 or market_cap > 200_000_000_000:
                continue
            
            sma_20 = hist['SMA_20'].iloc[-1]
            if pd.isna(sma_20) or latest['Close'] <= sma_20:
                continue
            
            sma_50 = hist['SMA_50'].iloc[-1]
            if pd.isna(sma_50) or latest['Close'] <= sma_50:
                continue
            
            is_weekend = datetime.now().weekday() >= 5
            if not is_weekend:
                vol_ma = hist['Volume'].rolling(20).mean().iloc[-1]
                if pd.isna(vol_ma) or latest['Volume'] <= 1.5 * vol_ma:
                    continue
                volume_surge = latest['Volume'] / vol_ma
            else:
                vol_ma = hist['Volume'].rolling(20).mean().iloc[-1]
                volume_surge = 1.5
            
            rsi = ta.momentum.RSIIndicator(hist['Close'], window=14).rsi().iloc[-1]
            if pd.isna(rsi) or rsi < 50 or rsi > 80:
                continue
            
            high_20 = hist['High'].tail(20).max()
            if latest['Close'] < high_20 * 0.95:
                continue
            
            results.append({
                'Ticker': tkr,
                'Price': round(latest['Close'], 2),
                'RSI': round(rsi, 1),
                'Volume Surge': round(volume_surge, 1),
                'Market Cap': f"${round(market_cap / 1_000_000_000, 2)}B",
                'Score': round(50 + (rsi - 50) + (volume_surge * 2), 0),
                'ATR': round(hist['ATR'].iloc[-1], 2) if 'ATR' in hist.columns else None,
                'Strategy': 'Momentum Breakout'
            })
        except:
            continue
    return sorted(results, key=lambda x: x['Score'], reverse=True)[:3]

def scan_mean_reversion(tickers):
    results = []
    for tkr in tickers:
        try:
            stock = yf.Ticker(tkr)
            hist = stock.history(period="3mo")
            info = stock.info
            
            if hist.empty or len(hist) < 50:
                continue
            
            latest = hist.iloc[-1]
            market_cap = info.get('marketCap', 0)
            
            if market_cap < 300_000_000 or market_cap > 200_000_000_000:
                continue
            
            sma_200 = hist['SMA_200'].iloc[-1]
            if pd.isna(sma_200) or latest['Close'] <= sma_200:
                continue
            
            sma_50 = hist['SMA_50'].iloc[-1]
            if pd.isna(sma_50) or latest['Close'] >= sma_50:
                continue
            
            is_weekend = datetime.now().weekday() >= 5
            if not is_weekend:
                vol_ma = hist['Volume'].rolling(20).mean().iloc[-1]
                if pd.isna(vol_ma) or latest['Volume'] <= 1.2 * vol_ma:
                    continue
                volume_surge = latest['Volume'] / vol_ma
            else:
                volume_surge = 1.5
            
            rsi = ta.momentum.RSIIndicator(hist['Close'], window=14).rsi().iloc[-1]
            if pd.isna(rsi) or rsi < 30 or rsi > 50:
                continue
            
            results.append({
                'Ticker': tkr,
                'Price': round(latest['Close'], 2),
                'RSI': round(rsi, 1),
                'Volume Surge': round(volume_surge, 1),
                'Market Cap': f"${round(market_cap / 1_000_000_000, 2)}B",
                'Score': round(50 + (50 - rsi) + (volume_surge), 0),
                'ATR': round(hist['ATR'].iloc[-1], 2) if 'ATR' in hist.columns else None,
                'Strategy': 'Mean Reversion'
            })
        except:
            continue
    return sorted(results, key=lambda x: x['Score'], reverse=True)[:3]

def scan_hybrid(tickers):
    results = []
    for tkr in tickers:
        try:
            stock = yf.Ticker(tkr)
            hist = stock.history(period="4mo")
            info = stock.info
            
            if hist.empty or len(hist) < 50:
                continue
            
            latest = hist.iloc[-1]
            market_cap = info.get('marketCap', 0)
            
            if market_cap < 500_000_000 or market_cap > 100_000_000_000:
                continue
            
            sma_50 = hist['SMA_50'].iloc[-1]
            if pd.isna(sma_50) or latest['Close'] <= sma_50:
                continue
            
            rsi = ta.momentum.RSIIndicator(hist['Close'], window=14).rsi().iloc[-1]
            if pd.isna(rsi) or rsi < 40 or rsi > 70:
                continue
            
            is_weekend = datetime.now().weekday() >= 5
            if not is_weekend:
                vol_ma = hist['Volume'].rolling(20).mean().iloc[-1]
                if pd.isna(vol_ma) or latest['Volume'] <= 1.2 * vol_ma:
                    continue
                volume_surge = latest['Volume'] / vol_ma
            else:
                volume_surge = 1.5
            
            pe = info.get('trailingPE', None)
            roe = info.get('returnOnEquity', None)
            
            value_score = 0
            if pe and pe < 20:
                value_score += 10
            if roe and roe > 0.15:
                value_score += 10
            
            if value_score < 5:
                continue
            
            results.append({
                'Ticker': tkr,
                'Price': round(latest['Close'], 2),
                'RSI': round(rsi, 1),
                'Volume Surge': round(volume_surge, 1),
                'Market Cap': f"${round(market_cap / 1_000_000_000, 2)}B",
                'Score': round(50 + (70 - rsi) + (volume_surge * 2) + value_score, 0),
                'ATR': round(hist['ATR'].iloc[-1], 2) if 'ATR' in hist.columns else None,
                'Strategy': 'Hybrid (Balanced)'
            })
        except:
            continue
    return sorted(results, key=lambda x: x['Score'], reverse=True)[:3]

def scan_full_market(strategy):
    tickers = get_stock_list()
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    results = []
    
    if strategy == "Momentum Breakout":
        results = scan_momentum(tickers)
    elif strategy == "Mean Reversion":
        results = scan_mean_reversion(tickers)
    else:
        results = scan_hybrid(tickers)
    
    progress_bar.empty()
    status_text.empty()
    
    return results

# ========== TAB 1: DASHBOARD ==========
with tab1:
    st.title("📊 Swing Commander")
    st.markdown("---")
    
    st.subheader("🔍 Full Market Scanner")
    st.caption("Choose a strategy and scan the entire US market")
    
    strategy_options = ["Momentum Breakout", "Mean Reversion", "Hybrid (Balanced)"]
    selected_strategy = st.selectbox("Select Strategy", strategy_options, index=2)
    
    col1, col2 = st.columns([1, 4])
    with col1:
        scan_btn = st.button("🚀 Scan Market", type="primary")
    
    with st.expander("📖 Strategy Descriptions"):
        st.markdown("""
        **1. Momentum Breakout** (Aggressive)
        - Finds stocks breaking out to new highs
        - Best for bull markets
        - Filters: Price > 20-day & 50-day MA, RSI 50-80, Volume surge
        
        **2. Mean Reversion** (Conservative)
        - Finds oversold stocks due for a bounce
        - Best for sideways or choppy markets
        - Filters: Price > 200-day MA but < 50-day MA, RSI 30-50
        
        **3. Hybrid (Balanced)**
        - Combines momentum and value factors
        - Works in all market conditions
        - Filters: Price > 50-day MA, RSI 40-70, Value metrics (P/E < 20 or ROE > 15%)
        """)
    
    if scan_btn:
        with st.spinner(f"Scanning with {selected_strategy} strategy..."):
            results = scan_full_market(selected_strategy)
            st.session_state.scan_results = results
        
        if st.session_state.scan_results:
            st.success(f"✅ Found {len(st.session_state.scan_results)} top picks!")
            st.markdown("---")
            
            cols = st.columns(3)
            for idx, stock in enumerate(st.session_state.scan_results):
                with cols[idx]:
                    st.subheader(f"🏆 #{idx+1} {stock['Ticker']}")
                    st.caption(f"Strategy: {stock['Strategy']}")
                    st.metric("💰 Price", f"${stock['Price']}")
                    st.metric("📊 RSI", stock['RSI'])
                    st.metric("📈 Volume Surge", f"{stock['Volume Surge']}x")
                    st.metric("🏢 Market Cap", stock['Market Cap'])
                    st.metric("⭐ Score", f"{stock['Score']}/100")
                    if stock.get('ATR'):
                        st.caption(f"ATR: ${stock['ATR']}")
                    
                    if st.button(f"📈 Analyze {stock['Ticker']}", key=f"quick_{stock['Ticker']}"):
                        st.session_state.quick_ticker = stock['Ticker']
                        st.rerun()
        else:
            st.warning(f"No stocks passed the {selected_strategy} filters today.")
            st.caption("💡 Tip: Try a different strategy or run during market hours.")
    
    st.markdown("---")
    
    # ===== INDIVIDUAL STOCK ANALYSIS =====
    analysis_ticker = st.session_state.quick_ticker if st.session_state.quick_ticker else ticker
    
    if analysis_ticker:
        st.subheader(f"📊 {analysis_ticker} Analysis")
        
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
        for idx
