import os
import pandas as pd
import feedparser
import requests
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from transformers import pipeline

C, G, R, Y, W = '\033[96m', '\033[92m', '\033[91m', '\033[93m', '\033[0m'

# --- 1. CONFIG & SECRETS ---
CAPITAL_API = os.environ.get("CAPITAL_API_KEY")
CAPITAL_USER = os.environ.get("CAPITAL_USER")
CAPITAL_PASS = os.environ.get("CAPITAL_PASS")

scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(eval(os.environ.get("GCP_CREDENTIALS")), scopes=scopes)
gc = gspread.authorize(creds)

print(f"{C}📡 Booting FinBERT NLP Engine...{W}")
finbert = pipeline("text-classification", model="ProsusAI/finbert", top_k=3)

def run_engine():
    print(f"\n{C}🚀 V14.1 Institutional Engine Live (Internal Broker Data)...{W}")
    base_url = "https://demo-api-capital.backend-capital.com/api/v1" 
    
    # --- 2. AUTHENTICATE ---
    auth_resp = requests.post(f"{base_url}/session", headers={"X-CAP-API-KEY": CAPITAL_API}, json={"identifier": CAPITAL_USER, "password": CAPITAL_PASS})
    if auth_resp.status_code != 200:
        print(f"{R}❌ Broker Auth Failed{W}"); return
    
    auth_headers = {"CST": auth_resp.headers.get("CST"), "X-SECURITY-TOKEN": auth_resp.headers.get("X-SECURITY-TOKEN")}

    # --- 3. SAFETY LOCK ---
    target_account = "316396775975691294" 
    switch_resp = requests.put(f"{base_url}/session", headers=auth_headers, json={"accountId": target_account})
    if switch_resp.status_code != 200:
        print(f"{R}❌ Safety Lock Failed. Aborting.{W}"); return
    print(f"{G}🔒 SAFETY LOCK: Execution locked to S&P Program ({target_account}){W}")

    # --- 4. FETCH INTERNAL BROKER CHARTS (THE GOLDEN KEY) ---
    print(f"{Y}📊 Fetching 200-Hour Chart from Capital.com Servers...{W}")
    # FIX: Using the strict 'US500' epic for the historical database
    hist_resp = requests.get(f"{base_url}/prices/US500?resolution=HOUR&max=250", headers=auth_headers)
    
    if hist_resp.status_code == 200 and 'prices' in hist_resp.json():
        prices_data = hist_resp.json()['prices']
        try:
            records = [{'High': p['highPrice']['ask'], 'Low': p['lowPrice']['ask'], 'Close': p['closePrice']['ask']} for p in prices_data]
            df = pd.DataFrame(records)
        except Exception as e:
            print(f"{R}❌ Error parsing broker data: {e}{W}"); return
    else:
        print(f"{R}❌ Broker historical data unavailable.{W}")
        print(f"{Y}🚨 BROKER RESPONSE: {hist_resp.text}{W}") 
        return

    # Calculate precise technicals
    df['SMA'] = df['Close'].rolling(window=200).mean()
    tr = pd.concat([(df['High']-df['Low']), abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    
    p = float(df['Close'].iloc[-1].iloc[0] if isinstance(df['Close'].iloc[-1], pd.Series) else df['Close'].iloc[-1])
    s = float(df['SMA'].iloc[-1].iloc[0] if isinstance(df['SMA'].iloc[-1], pd.Series) else df['SMA'].iloc[-1])
    a = float(df['ATR'].iloc[-1].iloc[0] if isinstance(df['ATR'].iloc[-1], pd.Series) else df['ATR'].iloc[-1])
    regime = "BULLISH" if p > s else "BEARISH"
    print(f"📈 Broker Technicals -> Price: {p} | 200 SMA: {s} | ATR: {a}")
    
    # --- 5. FINBERT SENTIMENT ANALYSIS ---
    headlines = []
    try:
        f = feedparser.parse("https://feeds.content.dowjones.io/public/rss/mw_topstories")
        if getattr(f, 'entries', None): headlines = [e.title for e in f.entries[:10] if hasattr(e, 'title')]
    except: pass
    
    system_scores = []
    for h in headlines:
        if h and isinstance(h, str):
            try:
                results = finbert(str(h))[0]
                probs = {res['label']: res['score'] for res in results}
                system_scores.append(probs.get('positive', 0) - probs.get('negative', 0))
            except: continue
    score = round(sum(system_scores) / len(system_scores), 3) if system_scores else 0.0

    # --- 6. LOGIC & EXECUTION ---
    sh = gc.open('Trading_Journal').worksheet('Sentiment_Log')
    prev_score = float(sh.acell('F2').value) if sh.acell('F2').value else 0.0
    
    sig = "WAIT"
    if score <= -0.15: sig = "ENTER SHORT"
    elif score >= 0.15: sig = "ENTER LONG"
    elif abs(score) < 0.05 and abs(prev_score) >= 0.10: sig = "EXIT POSITION"

    exec_price = round(p, 2)
    
    if "ENTER" in sig:
        # FIX: Using the strict 'US500' epic for the live market quote
        price_resp = requests.get(f"{base_url}/markets/US500", headers=auth_headers)
        if price_resp.status_code == 200:
            live_ask = price_resp.json()['snapshot']['offer']
            live_bid = price_resp.json()['snapshot']['bid']
            exec_price = live_ask if "LONG" in sig else live_bid
            
            stop_dist = round(exec_price * 0.0030, 2)
            stop_price = round(exec_price - stop_dist if "LONG" in sig else exec_price + stop_dist, 2)
            print(f"🎯 Live Execution Target: {exec_price} | Hard Stop: {stop_price}")

            # FIX: Using the strict 'US500' epic for the trade execution payload
            order_payload = {"epic": "US500", "direction": "BUY" if "LONG" in sig else "SELL", "size": 1.0, "guaranteedStop": False, "stopLevel": stop_price}
            trade_resp = requests.post(f"{base_url}/positions", headers=auth_headers, json=order_payload)
            if trade_resp.status_code == 200: print(f"{G}✅ Trade Executed Successfully{W}")
            else: print(f"{R}❌ Execution Failed: {trade_resp.text}{W}")

    # --- 7. GOOGLE SHEET LOGGING ---
    sh.insert_row([datetime.now().strftime("%Y-%m-%d %H:%M"), exec_price, round(s,2), round(a,2), regime, score, sig, "FINBERT", "V14.1_Capital_Data"], 2)
    print(f"[{sig}] | Score: {score} | Logged to Sheet.")

if __name__ == "__main__":
    run_engine()
