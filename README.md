# Kalshi 15-Minute Market Monitor + Flip Radar

Read-only Streamlit dashboard intended to display available Kalshi contracts for:
- Bitcoin: `KXBTC15M`
- Gold: `KXGOLD15M`
- Silver: `KXSILVER15M`
- WTI Oil: `KXWTI15M`

## Included
- YES/NO quotes and contract metadata returned by Kalshi's public API
- Auto-refresh options
- Dashboard-only Flip Radar that tracks the nearest-closing contract for each market
- Reversal alerts (bullish/bearish) based on a change in direction across quote samples
- Sudden-move alerts when the YES quote midpoint changes by at least 4¢ between samples
- Recent alert list

## Important limitations
- Flip Radar describes recent contract-price movement; it does **not** predict the final settlement outcome.
- The radar needs the dashboard to remain open for several refreshes to collect session history. It resets when the session restarts.
- Alert thresholds are simple defaults, not optimized or backtested trading signals.
- API schemas and market series tickers may change. If a series returns no market, verify its current ticker and availability using Kalshi's official market listing and contract rules.
- The app is read-only and places no orders.

## Deploy to Streamlit Community Cloud
1. Replace the repository's `app.py` with this version.
2. Ensure `requirements.txt` contains the packages listed in this folder.
3. Commit the changes to GitHub. Streamlit should redeploy automatically; otherwise restart the app.
4. Keep the dashboard open to collect samples for Flip Radar.
