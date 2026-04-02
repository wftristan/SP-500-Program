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

# Google Sheets Auth (Using Service Account for Server Automation)
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(eval(os.environ.get("GCP_CREDENTIALS")), scopes=scopes)
gc = gspread.authorize(creds)

print(f"{C}📡 Booting FinBERT NLP Engine...{W}")
finbert = pipeline("text-classification", model="ProsusAI/finbert", top_k=3)

# --- 2. CAPITAL.COM API EXECUTION ---
def execute_trade(direction, stop_loss_price):
    print(f"{Y}⚡ Transmitting Order to Capital.com API...{W}")
    base_url = "https://api-capital.backend-capital.com/api/v1" # Change to demo-api if using Demo
    
    # 1. Login
    auth_resp = requests.post(
        f"{base_url}/session",
        headers={"X-CAP-API-KEY": CAPITAL_API},
        json={"identifier": CAPITAL_USER, "password": CAPITAL_PASS}
    )
    if auth_resp.status_code != 200:
        print(f"{R}❌ Broker Auth Failed: {auth_resp.text}{W}")
        return False
        
    cst = auth_resp.headers.get("CST")
    x_sec = auth_resp.headers.get("X-SECURITY-TOKEN")
    auth_headers = {"CST": cst, "X-SECURITY-TOKEN": x_sec}

    # 2. Place Order (Standard REST payload for Capital)
    order_payload = {
        "epic": "US500", # Capital's exact ticker for S&P500
        "direction": "BUY" if direction == "LONG" else "SELL",
        "size": 1.0, # Minimum testing size
        "guaranteedStop": False,
        "stopLevel": stop_loss_price
    }
    
    trade_resp = requests.post(f"{base_url}/positions", headers=auth_headers, json=order_payload)
    if trade_resp.status_code == 200:
        print(f"{G}✅ Trade Executed Successfully at {direction}{W}")
        return True
    else:
        print(f"{R}❌ Trade Execution Failed: {trade_resp.text}{W}")
        return False

# --- 3. MARKET CONTEXT ---
def get_market_context():
    df = yf.download("ES=F", period="60d", interval="1h", progress=False, auto_adjust=False)
    df['SMA'] = df['Close'].rolling(window=200).mean()
    tr = pd.concat([(df['High']-df['Low']), abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    p, s, a = df['Close'].iloc[-1].item(), df['SMA'].iloc[-1].item(), df['ATR'].iloc[-1].item()
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
    print(f"\n{C}🚀 V11 Automated FinBERT Engine Live...{W}")
    sh = gc.open('Trading_Journal').worksheet('Sentiment_Log')
    prev_score = float(sh.acell('F2').value) if sh.acell('F2').value else 0.0

    regime, price, sma, atr = get_market_context()
    score, src = get_consensus_sentiment()
    
    sig = "WAIT"
    if score <= -0.15: sig = "ENTER SHORT"
    elif score >= 0.15: sig = "ENTER LONG"
    elif abs(score) < 0.05 and abs(prev_score) >= 0.10: sig = "EXIT POSITION"

    # --- EXECUTE BROKER API IF SIGNALED ---
    if "ENTER" in sig:
        stop_dist = round(price * 0.0030, 2)
        stop_price = round(price - stop_dist if "LONG" in sig else price + stop_dist, 2)
        print(f"🛡️ Target MAE Stop: {stop_price}")
        execute_trade("LONG" if "LONG" in sig else "SHORT", stop_price)

    # --- LOG TO SHEET ---
    delta = round(score - prev_score, 2)
    sh.insert_row([datetime.now().strftime("%Y-%m-%d %H:%M"), price, sma, atr, regime, score, sig, src, "V11_Auto"], 2)
    print(f"[{sig}] | Score: {score} | Logged to Sheet.")

if __name__ == "__main__":
    run()
