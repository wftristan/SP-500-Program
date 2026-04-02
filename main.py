import os
import yfinance as yf
import pandas as pd
import feedparser
import requests
import re
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from transformers import pipeline

C, G, R, Y, W = '\033[96m', '\033[92m', '\033[91m', '\033[93m', '\033[0m'

# --- 1. CONFIG & SECRETS ---
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
CAPITAL_API = os.environ.get("CAPITAL_API_KEY")
CAPITAL_USER = os.environ.get("CAPITAL_USER")
CAPITAL_PASS = os.environ.get("CAPITAL_PASS")
client = genai.Client(api_key=GEMINI_KEY)

# Google Sheets Auth
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(eval(os.environ.get("GCP_CREDENTIALS")), scopes=scopes)
gc = gspread.authorize(creds)

print(f"{C}📡 Booting FinBERT NLP Engine...{W}")
finbert = pipeline("text-classification", model="ProsusAI/finbert", top_k=3)

# --- 2. CAPITAL.COM API EXECUTION ---
def execute_trade(direction):
    print(f"{Y}⚡ Connecting to Capital.com...{W}")
    base_url = "https://demo-api-capital.backend-capital.com/api/v1"
    
    # 1. Login
    auth_resp = requests.post(
        f"{base_url}/session",
        headers={"X-CAP-API-KEY": CAPITAL_API},
        json={"identifier": CAPITAL_USER, "password": CAPITAL_PASS}
    )
    if auth_resp.status_code != 200:
        print(f"{R}❌ Broker Auth Failed: {auth_resp.text}{W}")
        return False, 0
        
    cst = auth_resp.headers.get("CST")
    x_sec = auth_resp.headers.get("X-SECURITY-TOKEN")
    auth_headers = {"CST": cst, "X-SECURITY-TOKEN": x_sec}

    # --- NEW: VERIFY ACCOUNT ---
    acc_resp = requests.get(f"{base_url}/accounts", headers=auth_headers)
    if acc_resp.status_code == 200:
        accounts = acc_resp.json().get('accounts', [])
        for acc in accounts:
            print(f"{C}🏦 PRE-FLIGHT CHECK: Connected to Account ID: {acc.get('accountId')} | Type: {acc.get('accountType')}{W}")
    # ---------------------------

    # 2. Get LIVE Capital.com Price for Stop Loss Math
    price_resp = requests.get(f"{base_url}/markets?epics=US500", headers=auth_headers)
    if price_resp.status_code != 200:
        print(f"{R}❌ Failed to fetch live price from broker.{W}")
        return False, 0
        
    market_data = price_resp.json()
    live_bid = market_data['marketDetails'][0]['snapshot']['bid']
    live_ask = market_data['marketDetails'][0]['snapshot']['offer']
    exec_price = live_ask if direction == "LONG" else live_bid
    
    # 3. Calculate True MAE Stop
    stop_dist = round(exec_price * 0.0030, 2)
    stop_price = round(exec_price - stop_dist if direction == "LONG" else exec_price + stop_dist, 2)
    print(f"🎯 Capital.com Live Price: {exec_price} | Hard Stop: {stop_price}")

    # 4. Place Order
    order_payload = {
        "epic": "US500",
        "direction": "BUY" if direction == "LONG" else "SELL",
        "size": 1.0, 
        "guaranteedStop": False,
        "stopLevel": stop_price
    }
    
    trade_resp = requests.post(f"{base_url}/positions", headers=auth_headers, json=order_payload)
    if trade_resp.status_code == 200:
        print(f"{G}✅ Trade Executed Successfully at {direction}{W}")
        return True, exec_price
    else:
        print(f"{R}❌ Trade Execution Failed: {trade_resp.text}{W}")
        return False, exec_price

# --- 3. MARKET CONTEXT (YAHOO FOR HISTORICAL MATH) ---
def get_market_context():
    # 1. Put on the "Google Chrome" disguise
    import requests
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    
    # 2. Ask for 'SPY' using the disguised session
    df = yf.download("SPY", period="60d", interval="1h", progress=False, session=session)
    
    if df.empty:
        print(f"{R}❌ Yahoo Finance blocked the request or data is empty.{W}")
        return None, 0, 0, 0
        
    df['SMA'] = df['Close'].rolling(window=200).mean()
    tr = pd.concat([(df['High']-df['Low']), abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    
    # Extract values cleanly
    p = float(df['Close'].iloc[-1].iloc[0] if isinstance(df['Close'].iloc[-1], pd.Series) else df['Close'].iloc[-1])
    s = float(df['SMA'].iloc[-1].iloc[0] if isinstance(df['SMA'].iloc[-1], pd.Series) else df['SMA'].iloc[-1])
    a = float(df['ATR'].iloc[-1].iloc[0] if isinstance(df['ATR'].iloc[-1], pd.Series) else df['ATR'].iloc[-1])
    
    return ("BULLISH" if p > s else "BEARISH"), round(p,2), round(s,2), round(a,2)

# --- 4. SENTIMENT ENGINE ---
def get_consensus_sentiment():
    headlines = []
    try:
        f = feedparser.parse("https://feeds.content.dowjones.io/public/rss/mw_topstories")
        headlines = [e.title for e in f.entries[:10]]
    except: pass

    system_scores = []
    for h in headlines:
        results = finbert(h)[0]
        probs = {res['label']: res['score'] for res in results}
        system_scores.append(probs.get('positive', 0) - probs.get('negative', 0))
    
    return round(sum(system_scores) / len(system_scores), 3) if system_scores else 0.0, "FINBERT"

# --- 5. EXECUTION CORE ---
def run():
    print(f"\n{C}🚀 V12 Automated FinBERT Engine Live...{W}")
    sh = gc.open('Trading_Journal').worksheet('Sentiment_Log')
    prev_score = float(sh.acell('F2').value) if sh.acell('F2').value else 0.0

    regime, yahoo_price, sma, atr = get_market_context()
    if regime is None:
        print(f"{Y}⚠️ Aborting this hour's run due to missing price data.{W}")
        return
        
    score, src = get_consensus_sentiment()
    sig = "WAIT"
    if score <= -0.15: sig = "ENTER SHORT"
    elif score >= 0.15: sig = "ENTER LONG"
    elif abs(score) < 0.05 and abs(prev_score) >= 0.10: sig = "EXIT POSITION"

    exec_price = yahoo_price # Default to Yahoo for the sheet unless a trade executes

    # --- EXECUTE BROKER API IF SIGNALED ---
    if "ENTER" in sig:
        success, cap_price = execute_trade("LONG" if "LONG" in sig else "SHORT")
        if cap_price > 0: exec_price = cap_price # Use the true broker price for the log

    # --- LOG TO SHEET ---
    delta = round(score - prev_score, 2)
    sh.insert_row([datetime.now().strftime("%Y-%m-%d %H:%M"), exec_price, sma, atr, regime, score, sig, src, "V12_Auto"], 2)
    print(f"[{sig}] | Score: {score} | Logged to Sheet.")

if __name__ == "__main__":
    run()
