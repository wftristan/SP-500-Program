name: V14 Autopilot Engine

on:
  workflow_dispatch: # Keep the manual button active for testing
  schedule:
    - cron: '0 * * * *' # Wake up at the top of every hour (Minute 0)

jobs:
  execute-trade:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install Dependencies
        run: pip install -r requirements.txt

      - name: Run V14 Institutional Engine
        env:
          CAPITAL_API_KEY: ${{ secrets.CAPITAL_API_KEY }}
          CAPITAL_USER: ${{ secrets.CAPITAL_USER }}
          CAPITAL_PASS: ${{ secrets.CAPITAL_PASS }}
          GCP_CREDENTIALS: ${{ secrets.GCP_CREDENTIALS }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python main.py
