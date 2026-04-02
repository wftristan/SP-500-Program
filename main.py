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

# Google Sheets Auth (Drive scope added for server search)
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(eval(os.environ.get("GCP_CREDENTIALS")), scopes=scopes)
gc = gspread.authorize(creds)

print(f"{C}📡 Booting FinBERT NLP Engine...{W}")
finbert = pipeline("text-classification", model="ProsusAI/finbert", top_k=3)

# --- 2. CAPITAL.COM API EXECUTION ---
def execute_trade(direction):
    print(f"{Y}⚡ Connecting to Capital.com Demo...{W}")
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

    # 1.5. THE SAFETY LOCK
    target_account = "316396775975691294" # S&P Program Demo Account
    switch_resp = requests.put(
        f"{base_url}/session", 
        headers=auth_headers, 
        json={"accountId": target_account}
    )
    if switch_resp.status_code == 200:
        print(f"{G}🔒 SAFETY LOCK: Execution locked to S&P Program ({target_account}){W}")
    else:
        print(f"{R}❌ Safety Lock Failed. Could not switch accounts. Aborting.{W}")
        return False, 0

    # 2. Get LIVE Price (URL Encoded Space)
    price_resp = requests.get(f"{base_url}/markets/US%20500", headers=auth_headers)
    
    if price_resp.status_code != 200:
        print(f"{R}❌ Failed to fetch live price. Broker responded with: {price_resp.text}{W}")
        return False, 0
        
    market_data = price_resp.json()
    live_bid = market_data['snapshot']['bid']
    live_ask = market_data['snapshot']['offer']
    exec_price = live_ask if direction == "LONG" else live_bid
    
    # 3. Calculate True MAE Stop
    stop_dist = round(exec_price * 0.0030, 2)
    stop_price = round(exec_price - stop_dist if direction == "LONG" else exec_price + stop_dist, 2)
    print(f"🎯 Capital.com Live Price: {exec_price} | Hard Stop: {stop_price}")

    # 4. Place Order (Literal Space in JSON)
    order_payload = {
        "epic": "US 500", 
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

# --- 3. MARKET CONTEXT (YAHOO DISGUISE) ---
def get_market_context():
    # Put on the "Google Chrome" disguise to bypass Yahoo filters
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0'})
    
    # Download SPY instead of ES=F to prevent blocks
    df = yf.download("SPY", period="60d", interval="1h", progress=False, session=session)
    
    if df.empty:
        print(f"{R}❌ Yahoo Finance blocked the request or data is empty.{W}")
        return None, 0, 0, 0
        
    df['SMA'] = df['Close'].rolling(window=200).mean()
    tr = pd.concat([(df['High']-df['Low']), abs(df['High']-df['Close'].
