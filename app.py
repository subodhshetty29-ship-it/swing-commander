import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Swing Commander", layout="wide")
st.title("📈 Swing Commander")

ticker = st.text_input("Enter Stock Ticker", "AAPL").upper()

if ticker:
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    
    if not hist.empty:
        st.metric("Current Price", f"${hist['Close'].iloc[-1]:.2f}")
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=hist.index, y=hist['Close'], mode='lines', name='Close'))
        fig.update_layout(height=400, template='plotly_dark')
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error("No data found")
