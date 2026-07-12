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
    page_title="Swing Commander Pro",
    page_icon="📈",
    layout="wide"
)

# ========== SIDEBAR - SETTINGS & POSITION SIZING ==========
st.sidebar.title("⚙️ Settings")

# Stock Input
ticker = st.sidebar.text_input("Stock Ticker", value="AAPL").upper()

# Time Period
period_map = {
    "1 Month": "1mo",
    "3 Months": "3mo",
    "6 Months": "6mo",
    "1 Year": "1y",
    "2 Years": "2y",
    "5 Years": "5y"
}
selected_period_label = st.sidebar.selectbox("Time Range", list(period_map.keys()), index=3)
period = period_map[selected_period_label]

st.sidebar.markdown("---")

# ========== POSITION SIZING ==========
st.sidebar.subheader("💰 Position Sizing")

account_size = st.sidebar.number_input(
    "Your Account Size ($)",
    min_value=1000,
    max_value=10000000,
    value=50000,
    step=1000,
    help="Total capital you have for trading"
)

risk_percent = st.sidebar.slider(
    "Risk per Trade (%)",
    min_value=0.5,
    max_value=3.0,
    value=1.0,
    step=0.1,
    help="Percentage of your account you're willing to lose on one trade"
)

atr_multiplier = st.sidebar.slider(
    "Stop Loss (ATR multiple)",
    min_value=1.0,
    max_value=3.0,
    value=2.0,
    step=0.5,
    help="How many ATRs below entry you place your stop loss"
)

st.sidebar.markdown("---")

# ========== UNDER DOG SCANNER ==========
st.sidebar.subheader("🐕 Under Dog Scanner")

scan_universe = st.sidebar.text_area(
    "Stocks to Scan (one per line)",
    value="AAPL\nMSFT\nGOOGL\nTSLA\nNVDA\nAMD\nINTC\nAMZN\nMETA\nIREN\nAPLD\nSOFI\nHOOD\nCOIN\nPLTR\nSNOW\nDDOG\nMDB\nZS\nNET\nRBLX\nAFRM\nUPST\nLCID\nRIVN\nNIO\nLI\nXPEV\nBABA\nJD\nPDD\nSE\nSHOP\nSQ\nROKU\nZM\nDOCU\nU\nTEAM\nWDAY\nCRM\nNOW\nORCL\nIBM\nDELL\nNOK\nERIC\nGRMN\nSWKS\nTXN\nMCHP\nON\nLSCC\nTER\nKLAC\nLRCX\nMU\nWDC\nSTX\nNTAP\nPSTG\nPURE\nNMBL\nVERI\nPLTR\nPD\nASAN\nPATH\nU\nSNOW\nDDOG\nMDB\nESTC\nZS\nNET\nFSLY\nFAST\nPAYC\nADP\nPAYX\nCTSH\nFIS\nFISV\nJKHY\nGPN\nWU\nWEX\nFLT\nTSS\nPYPL\nSQ\nAFRM\nUPST\nSOFI\nHOOD\nCOIN\nRIOT\nMARA\nSI\nGLXY\nHUT\nBTBT\nCIFR\nCLSK\nIREN\nAPLD\nCORZ\nCSPR\nWULF\nANY\nSDIG\nMIGI\nREBN\nFRHC\nIX\nWLFC\nALGT\nFLXS\nHSY\nFIZZ\nCELH\nMNST\nKDP\nKO\nPEP\nMO\nPM\nBTI\nSTZ\nTAP\nSAM\nBF-B\nBUD\nLYFT\nUBER\nDASH\nGRUB\nGETY\nANGI\nZ\nREAL\nCOMP\nOPAD\nRDFN\nZG\nFRPT\nPLAY\nOSW\nHGV\nMTN\nFUN\nSIX\nBOWL\nLYV\nLIVE\nMSGE\nMSGS\nSPHR\nTPC\nTRON\nMS\nGS\nJPM\nC\nWFC\nUSB\nPNC\nCOF\nDFS\nALLY\nAXP\nMA\nV\nFIS\nPYPL\nSQ\nAFRM\nUPST\nLC\nCOIN\nHOOD\nSOFI"
)

scan_button = st.sidebar.button("🚀 Run Scanner", type="primary")

st.sidebar.markdown("---")
st.sidebar.caption("📊 Data from Yahoo Finance")
st.sidebar.caption("💡 Enter any ticker (e.g. AAPL, TSLA, NVDA)")

# ========== CACHE DATA FETCH ==========
@st.cache_data(ttl=600)
def fetch_data(ticker, period):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return None, None, "No data found. Check ticker symbol."
        
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

# ========== SIGNAL ENGINE ==========
def get_signals(df):
    if df is None or len(df) < 20:
        return ["⚠️ Not enough data"], "NEUTRAL", 0
    
    latest = df.iloc[-1]
    signals = []
    score = 0
    
    # RSI
    if not pd.isna(latest['RSI']):
        if latest['RSI'] < 30:
            signals.append("🟢 RSI Oversold (<30) -> Bullish")
            score += 1
        elif latest['RSI'] > 70:
            signals.append("🔴 RSI Overbought (>70) -> Bearish")
            score -= 1
        else:
            signals.append(f"⚪ RSI Neutral ({latest['RSI']:.1f})")
    
    # Price vs SMA 50
    if not pd.isna(latest['SMA_50']):
        if latest['Close'] > latest['SMA_50']:
            signals.append("🟢 Price above 50-day MA -> Uptrend")
            score += 1
        else:
            signals.append("🔴 Price below 50-day MA -> Downtrend")
            score -= 1
    
    # Price vs SMA 200
    if not pd.isna(latest['SMA_200']):
        if latest['Close'] > latest['SMA_200']:
            signals.append("🟢 Price above 200-day MA -> Bullish long-term")
            score += 1
        else:
            signals.append("🔴 Price below 200-day MA -> Bearish long-term")
            score -= 1
    
    # MACD
    if not pd.isna(latest['MACD']) and not pd.isna(latest['MACD_Signal']):
        if latest['MACD'] > latest['MACD_Signal']:
            signals.append("🟢 MACD above Signal -> Momentum up")
            score += 1
        else:
            signals.append("🔴 MACD below Signal -> Momentum down")
            score -= 1
    
    # Recommendation
    if score >= 3:
        rec = "🟢 STRONG BUY"
    elif score >= 1:
        rec = "🟡 BUY / HOLD"
    elif score <= -3:
        rec = "🔴 STRONG SELL"
    elif score <= -1:
        rec = "🟠 SELL / AVOID"
    else:
        rec = "⚪ NEUTRAL"
    
    return signals, rec, score

# ========== POSITION SIZING CALC ==========
def calculate_position(price, atr, account, risk_pct, atr_mult):
    risk_dollars = account * (risk_pct / 100)
    stop_distance = atr * atr_mult
    shares = int(risk_dollars / stop_distance) if stop_distance > 0 else 0
    total_cost = shares * price
    stop_price = price - stop_distance
    return shares, total_cost, stop_price, risk_dollars, stop_distance

# ========== UNDER DOG SCANNER ENGINE ==========
def scan_for_underdogs(ticker_list, progress_bar=None, status_text=None):
    """Scan a list of tickers for Under Dog candidates."""
    results = []
    total = len(ticker_list)
    
    for i, tkr in enumerate(ticker_list):
        tkr = tkr.strip().upper()
        if status_text:
            status_text.text(f"Scanning {i+1}/{total}: {tkr}")
        if progress_bar:
            progress_bar.progress((i + 1) / total)
        
        try:
            stock = yf.Ticker(tkr)
            hist = stock.history(period="3mo")
            info = stock.info
            
            if hist.empty or len(hist) < 50:
                continue
            
            latest = hist.iloc[-1]
            
            # --- Stage 1: Market Cap & Liquidity ---
            market_cap = info.get('marketCap', 0)
            if market_cap == 0:
                continue
            
            # Target: Small to mid-cap ($300M - $2B)
            if not (300_000_000 <= market_cap <= 2_000_000_000):
                continue
            
            # --- Stage 2: Value Fundamentals ---
            pe = info.get('trailingPE', None)
            ps = info.get('priceToSalesTrailing12Months', None)
            debt_to_equity = info.get('debtToEquity', None)
            roe = info.get('returnOnEquity', None)
            
            # P/E: below 15
            if pe and pe > 15:
                continue
            # P/S: below 1.0
            if ps and ps > 1.0:
                continue
            # Debt/Equity: below 0.5
            if debt_to_equity and debt_to_equity > 0.5:
                continue
            # ROE: above 15%
            if roe and roe < 0.15:
                continue
            
            # --- Stage 3: Under the Radar (Analyst Coverage) ---
            num_analysts = info.get('numberOfAnalystOpinions', 0)
            
            # --- Stage 4: Catalyst (Technical) ---
            # Unusual volume: last 5 days avg volume > 1.5x 30-day avg
            vol_ma_30 = hist['Volume'].rolling(30).mean().iloc[-1]
            vol_ma_5 = hist['Volume'].rolling(5).mean().iloc[-1]
            if pd.isna(vol_ma_30) or pd.isna(vol_ma_5) or vol_ma_5 <= 1.5 * vol_ma_30:
                continue
            
            # Price above 50-day SMA
            sma_50 = hist['SMA_50'].iloc[-1]
            if pd.isna(sma_50) or latest['Close'] <= sma_50:
                continue
            
            # --- If we got here, it passed all filters! ---
            # Calculate a simple "Under Dog Score" (0-100)
            score = 50  # Base score
            
            # Bonus for low analyst coverage
            if num_analysts and num_analysts < 3:
                score += 20
            elif num_analysts and num_analysts < 5:
                score += 10
            
            # Bonus for strong volume surge
            vol_ratio = vol_ma_5 / vol_ma_30 if vol_ma_30 > 0 else 0
            if vol_ratio > 2.0:
                score += 15
            elif vol_ratio > 1.5:
                score += 5
            
            # Bonus for high ROE
            if roe and roe > 0.20:
                score += 10
            
            # Bonus for very low P/E
            if pe and pe < 10:
                score += 5
            
            results.append({
                'Ticker': tkr,
                'Price': round(latest['Close'], 2),
                'Market Cap ($M)': round(market_cap / 1_000_000, 0),
                'P/E': round(pe, 2) if pe else 'N/A',
                'P/S': round(ps, 2) if ps else 'N/A',
                'ROE': f"{round(roe * 100, 1)}%" if roe else 'N/A',
                'Under Dog Score': min(score, 100),
                'Analysts': num_analysts if num_analysts else 0,
                'Volume Surge': round(vol_ratio, 1),
            })
            
        except Exception:
            continue
    
    # Sort by Under Dog Score descending
    results = sorted(results, key=lambda x: x['Under Dog Score'], reverse=True)
    return results

# ========== MAIN APP ==========
st.title("📈 Swing Commander Pro")
st.markdown(f"### Active Ticker: *{ticker}*")
st.markdown("---")

# ========== UNDER DOG SCANNER RESULTS ==========
if scan_button and scan_universe:
    ticker_list = [t.strip().upper() for t in scan_universe.split('\n') if t.strip()]
    
    if ticker_list:
        st.subheader("🐕 Under Dog Scanner Results")
        st.info(f"Scanning {len(ticker_list)} stocks... This may take a few moments.")
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        results = scan_for_underdogs(ticker_list, progress_bar, status_text)
        
        progress_bar.empty()
        status_text.empty()
        
        if results:
            df_results = pd.DataFrame(results)
            st.success(f"✅ Found {len(df_results)} potential Under Dog candidates!")
            
            # Display as a beautiful table
            st.dataframe(
                df_results,
                column_config={
                    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                    "Price": st.column_config.NumberColumn("Price ($)", format="$%.2f"),
                    "Market Cap ($M)": st.column_config.NumberColumn("Market Cap", format="$%dM"),
                    "P/E": st.column_config.NumberColumn("P/E", format="%.2f"),
                    "P/S": st.column_config.NumberColumn("P/S", format="%.2f"),
                    "ROE": st.column_config.TextColumn("ROE"),
                    "Under Dog Score": st.column_config.ProgressColumn(
                        "Under Dog Score",
                        format="%d",
                        min_value=0,
                        max_value=100,
                    ),
                    "Analysts": st.column_config.NumberColumn("Analysts"),
                    "Volume Surge": st.column_config.NumberColumn("Volume Surge", format="%.1fx"),
                },
                hide_index=True,
                use_container_width=True
            )
            
            # Show top picks
            st.subheader("🏆 Top Under Dog Picks")
            top_picks = df_results.head(5)['Ticker'].tolist()
            cols = st.columns(len(top_picks))
            for idx, pick in enumerate(top_picks):
                with cols[idx]:
                    st.metric(f"#{idx+1}", pick)
            
        else:
            st.warning("No Under Dog candidates found in this scan. Try expanding your ticker list or adjusting criteria.")
        
        st.markdown("---")

# ========== INDIVIDUAL STOCK ANALYSIS ==========
if ticker:
    df, info, error = fetch_data(ticker, period)
    
    if error:
        st.error(f"❌ {error}")
        st.info("Try: AAPL, MSFT, GOOGL, TSLA, NVDA, AMZN")
    else:
        # ===== TOP METRICS =====
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
        if pe:
            col5.metric("🧮 P/E Ratio", f"{pe:.2f}")
        else:
            col5.metric("🧮 P/E Ratio", "N/A")
        
        st.markdown("---")
        
        # ===== POSITION SIZING OUTPUT =====
        current_atr = latest['ATR']
        if not pd.isna(current_atr) and current_atr > 0:
            shares, total_cost, stop_price, risk_dollars, stop_dist = calculate_position(
                latest['Close'], 
                current_atr, 
                account_size, 
                risk_percent, 
                atr_multiplier
            )
            
            st.subheader("🎯 Position Sizing Recommendation")
            col_a, col_b, col_c, col_d, col_e = st.columns(5)
            col_a.metric("📦 Shares to Buy", f"{shares:,}")
            col_b.metric("💰 Total Cost", f"${total_cost:,.2f}")
            col_c.metric("🛑 Stop Loss Price", f"${stop_price:.2f}")
            col_d.metric("📉 Stop Distance", f"${stop_dist:.2f}")
            col_e.metric("⚠️ Max Risk", f"${risk_dollars:,.2f} ({risk_percent}%)")
            
            st.caption(f"Based on: Account ${account_size:,} | Risk {risk_percent}% | ATR {current_atr:.2f} x {atr_multiplier}")
            st.markdown("---")
        else:
            st.warning("⚠️ ATR data not available yet. Need at least 14 days of data.")
            st.markdown("---")
        
        # ===== SIGNALS SECTION =====
        signals, rec, score = get_signals(df)
        
        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.subheader("📡 Technical Signals")
            for s in signals:
                st.write(s)
        
        with col_b:
            st.subheader("🎯 Recommendation")
            if "STRONG BUY" in rec:
                st.success(f"### {rec}")
            elif "STRONG SELL" in rec:
                st.error(f"### {rec}")
            elif "BUY" in rec:
                st.info(f"### {rec}")
            elif "SELL" in rec:
                st.warning(f"### {rec}")
            else:
                st.info(f"### {rec}")
            st.caption(f"Signal Score: {score:+d} / 4")
        
        st.markdown("---")
        
        # ===== PRICE CHART =====
        st.subheader("📉 Price & Moving Averages")
        fig1 = go.Figure()
        fig1.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='Close', line=dict(color='white', width=2)))
        fig1.add_trace(go.Scatter(x=df.index, y=df['SMA_20'], mode='lines', name='SMA 20', line=dict(color='orange', width=1, dash='dot')))
        fig1.add_trace(go.Scatter(x=df.index, y=df['SMA_50'], mode='lines', name='SMA 50', line=dict(color='blue', width=1, dash='dash')))
        fig1.add_trace(go.Scatter(x=df.index, y=df['SMA_200'], mode='lines', name='SMA 200', line=dict(color='red', width=1, dash='dash')))
        # Add stop loss line on chart
        if not pd.isna(current_atr) and current_atr > 0:
            fig1.add_hline(y=stop_price, line_dash="dash", line_color="red", annotation_text="Stop Loss", annotation_position="bottom right")
        fig1.update_layout(height=400, template='plotly_dark', xaxis_title='Date', yaxis_title='Price (USD)')
        st.plotly_chart(fig1, use_container_width=True)
        
        # ===== VOLUME CHART =====
        st.subheader("📊 Volume")
        fig2 = go.Figure()
        colors = ['green' if df['Close'].iloc[i] >= df['Open'].iloc[i] else 'red' for i in range(len(df))]
        fig2.add_trace(go.Bar(x=df.index, y=df['Volume'], name='Volume', marker_color=colors, opacity=0.7))
        fig2.add_trace(go.Scatter(x=df.index, y=df['Volume_MA'], mode='lines', name='Volume MA 20', line=dict(color='yellow', width=1)))
        fig2.update_layout(height=250, template='plotly_dark', xaxis_title='Date', yaxis_title='Volume')
        st.plotly_chart(fig2, use_container_width=True)
        
        # ===== RSI & MACD =====
        st.subheader("📈 Indicators (RSI & MACD)")
        fig3 = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.5, 0.5])
        
        # RSI
        fig3.add_trace(go.Scatter(x=df.index, y=df['RSI'], mode='lines', name='RSI', line=dict(color='purple', width=2)), row=1, col=1)
        fig3.add_hline(y=70, line_dash="dash", line_color="red", row=1, col=1)
        fig3.add_hline(y=30, line_dash="dash", line_color="green", row=1, col=1)
        fig3.update_yaxes(title_text="RSI", row=1, col=1)
        
        # MACD
        fig3.add_trace(go.Scatter(x=df.index, y=df['MACD'], mode='lines', name='MACD', line=dict(color='cyan', width=2)), row=2, col=1)
        fig3.add_trace(go.Scatter(x=df.index, y=df['MACD_Signal'], mode='lines', name='Signal', line=dict(color='orange', width=2)), row=2, col=1)
        fig3.add_trace(go.Bar(x=df.index, y=df['MACD_Hist'], name='Histogram', marker_color='grey', opacity=0.5), row=2, col=1)
        fig3.update_yaxes(title_text="MACD", row=2, col=1)
        fig3.update_layout(height=400, template='plotly_dark', showlegend=True)
        st.plotly_chart(fig3, use_container_width=True)
        
        # ===== DATA TABLE =====
        with st.expander("📋 View Raw Data"):
            display_df = df.tail(10)[['Open', 'High', 'Low', 'Close', 'Volume', 'RSI', 'MACD', 'SMA_50', 'ATR']].round(2)
            st.dataframe(display_df, use_container_width=True)
        
        # ===== ABOUT STOCK =====
        with st.expander("🏢 Company Info"):
            if info:
                st.write(f"*Name:* {info.get('longName', 'N/A')}")
                st.write(f"*Sector:* {info.get('sector', 'N/A')}")
                st.write(f"*Industry:* {info.get('industry', 'N/A')}")
                st.write(f"*Market Cap:* {info.get('marketCap', 'N/A')}")
                st.write(f"*Website:* {info.get('website', 'N/A')}")
                st.write(f"*Description:* {info.get('longBusinessSummary', 'N/A')[:500]}...")
else:
    st.info("👈 Enter a stock ticker in the sidebar to begin.")
