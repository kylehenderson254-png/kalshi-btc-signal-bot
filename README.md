# Kalshi Market Command Center + Flip Radar

A read-only Streamlit dashboard with a responsive 2×2 layout for Bitcoin, Gold, Silver, and WTI Oil 15-minute markets.

## Update your existing GitHub + Streamlit app

1. Download and unzip the package.
2. Open the **main page** of your existing GitHub repository (not a nested folder).
3. Open the existing root `app.py`, click the pencil icon, replace all its text with the new `app.py`, and **Commit changes**.
4. Open the existing root `requirements.txt` and make sure it matches the included `requirements.txt`. Commit if you changed it.
5. Open your Streamlit Community Cloud app and wait for the new deployment. Refresh the page.
6. The new layout will display all four markets, plus dashboard-only Flip Radar alerts.

**Do not upload the ZIP or its enclosing folder into your repository.** The two files should sit at the repository root where your current Streamlit app points.

## How the radar works

- Monitors the nearest currently open contract in each of four configured series.
- Samples the Kalshi YES bid/ask midpoint (or last YES price if midpoint is missing).
- Flags direction reversals when the midpoint moves by at least 0.5¢ in opposite directions across consecutive samples.
- Flags a sudden move when the price changes by at least 4¢ between samples (adjustable in sidebar).
- Only new samples generate new events. History is session-only and resets when the app session restarts.
- No Kalshi credentials, no trades, no external alerts.

## Important limitations

- Live API behavior and all series identifiers have **not** been verified against Kalshi in this environment. If a panel says no open contract, confirm the series ticker and contract availability on Kalshi.
- The contract quote is **not** the underlying asset spot price. A strike shown in a contract may be missing or formatted differently depending on its market fields.
- Price movement alerts are not a validated forecast or financial advice.
- Streamlit refresh is best-effort and can pause when a browser tab sleeps.
