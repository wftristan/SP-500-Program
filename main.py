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
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_ID = os.environ.get("TELEGRAM_CHAT_ID")
TARGET_ACCOUNT = "316396775975691294"

scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(eval(os.environ.get("GCP_CREDENTIALS")), scopes=scopes)
gc = gspread.authorize(creds)

print(f"{C}📡 Booting FinBERT NLP Engine...{W}")
finbert = pipeline("text-classification", model="ProsusAI/finbert", top_k=3)

# --- 2. TELEGRAM NOTIFIER ---
def send_telegram(message):
    if not TG_TOKEN or not TG_ID: return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TG_ID, "text": message})
    except: print(f"{R}❌ Telegram notification failed.{W}")

# --- 3. DYNAMIC RISK & INVENTORY MANAGER ---
def execute_signal(sig, auth_headers, base_url):
    if sig == "WAIT": return False, 0.0

    # A. Check Inventory
    pos_resp = requests.get(f"{base_url}/positions", headers=auth_headers)
    open_pos = None
    if pos_resp.status_code == 200:
        for p in pos_resp.json().get('positions', []):
            if p['market']['epic'] == 'US500':
                open_pos = p['position']; break
    
    current_dir = open_pos['direction'] if open_pos else None
    deal_id = open_pos['dealId'] if open_pos else None
    target_dir = "BUY" if "LONG" in sig else "SELL"

    # B. Handle EXIT
    if sig == "EXIT POSITION":
        if current_dir:
            del_resp = requests.delete(f"{base_url}/positions/{deal_id}", headers=auth_headers)
            if del_resp.status_code == 200:
                send_telegram(f"🛑 EXIT: Closed {current_dir} S&P 500 position.")
                return True, 0.0
        return False, 0.0

    # C. Handle Reversal or Entry
    if current_dir == target_dir: return False, 0.0
    if current_dir and current_dir != target_dir:
        requests.delete(f"{base_url}/positions/{deal_id}", headers=auth_headers)
        send_telegram(f"🔄 REVERSING: Closing {current_dir} to open {target_dir}.")

    # D. Fetch Live Balance for Dynamic Sizing
    balance = 1000.0 # Fallback
    acc_resp = requests.get(f"{base_url}/accounts", headers=auth_headers)
    if acc_resp.status_code == 200:
        for acc in acc_resp.json().get('accounts', []):
            if str(acc.get('accountId')) == TARGET_ACCOUNT:
                balance = float(acc.get('balance', {}).get('balance', 1000.0))
                break

    # E. Execute New Trade with Dynamic Size
    price_resp = requests.get(f"{base_url}/markets/US500", headers=auth_headers)
    if price_resp.status_code == 200:
        snap = price_resp.json()['snapshot']
        exec_price = snap['offer'] if target_dir == "BUY" else snap['bid']
        
        # --- THE 1% MATH ---
        risk_capital = balance * 0.01 
        stop_dist = round(exec_price * 0.0030, 2)
        raw_size = risk_capital / stop_dist
        trade_size = max(0.1, round(raw_size, 1)) # Broker requires 1 decimal place, minimum 0.1
        
        stop_price = round(exec_price - stop_dist if target_dir == "BUY" else exec_price + stop_dist, 2)

        order_payload = {"epic": "US500", "direction": target_dir, "size": trade_size, "guaranteedStop": False, "stopLevel": stop_price}
        trade_resp = requests.post(f"{base_url}/positions", headers=auth_headers, json=order_payload)
        
        if trade_resp.status_code == 200: 
            msg = f"✅ {target_dir} EXECUTED\nPrice: {exec_price}\nSize: {trade_size} Units\nStop: {stop_price}\nRisk: 1% ({round(risk_capital, 2)} GBP)"
            send_telegram(msg)
            print(f"{G}{msg}{W}")
            return True, exec_price
    return False, 0.0

# --- 4. MAIN ENGINE ---
def run_engine():
    print(f"\n{C}🚀 V17 Engine (Dynamic 1% Risk) Live...{W}")
    base_url = "https://demo-api-capital.backend-capital.com/api/v1" 
    
    auth_resp = requests.post(f"{base_url}/session", headers={"X-CAP-API-KEY": CAPITAL_API}, json={"identifier": CAPITAL_USER, "password": CAPITAL_PASS})
    if auth_resp.status_code != 200: return
    auth_headers = {"CST": auth_resp.headers.get("CST"), "X-SECURITY-TOKEN": auth_resp.headers.get("X-SECURITY-TOKEN")}
    requests.put(f"{base_url}/session", headers=auth_headers, json={"accountId": TARGET_ACCOUNT})

    hist_resp = requests.get(f"{base_url}/prices/US500?resolution=HOUR&max=250", headers=auth_headers)
    if hist_resp.status_code != 200: return
    df = pd.DataFrame([{'High': p['highPrice']['ask'], 'Low': p['lowPrice']['ask'], 'Close': p['closePrice']['ask']} for p in hist_resp.json()['prices']])

    df['SMA'] = df['Close'].rolling(window=200).mean()
    tr = pd.concat([(df['High']-df['Low']), abs(df['High']-df['Close'].shift()), abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    
    p, s, a = df['Close'].iloc[-1], df['SMA'].iloc[-1], df['ATR'].iloc[-1]
    regime = "BULLISH" if p > s else "BEARISH"
    
    headlines = []
    try:
        f = feedparser.parse("https://feeds.content.dowjones.io/public/rss/mw_topstories")
        headlines = [e.title for e in f.entries[:10] if hasattr(e, 'title')]
    except: pass
    
    scores = []
    for h in headlines:
        try:
            res = finbert(str(h))[0]
            scores.append({r['label']: r['score'] for r in res}.get('positive', 0) - {r['label']: r['score'] for r in res}.get('negative', 0))
        except: continue
    score = round(sum(scores) / len(scores), 3) if scores else 0.0

    sh = gc.open('Trading_Journal').worksheet('Sentiment_Log')
    prev_score = float(sh.acell('F2').value) if sh.acell('F2').value else 0.0
    
    sig = "WAIT"
    if score <= -0.15: sig = "ENTER SHORT"
    elif score >= 0.15: sig = "ENTER LONG"
    elif abs(score) < 0.05 and abs(prev_score) >= 0.10: sig = "EXIT POSITION"

    executed, exec_price = execute_signal(sig, auth_headers, base_url)
    
    log_price = exec_price if executed and exec_price > 0 else round(p, 2)
    sh.insert_row([datetime.now().strftime("%Y-%m-%d %H:%M"), log_price, round(s,2), round(a,2), regime, score, sig, "FINBERT", "V17_Dynamic"], 2)
    print(f"[{sig}] | Score: {score} | Logged.")

if __name__ == "__main__":
    run_engine()
