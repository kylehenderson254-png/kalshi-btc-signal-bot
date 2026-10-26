# Kalshi Market Command Center — Flip Radar Plus

Read-only Streamlit dashboard for Bitcoin, Gold, Silver and WTI Oil 15-minute Kalshi contracts.

## New features
- Responsive 2×2 market cards with green YES and red NO labels.
- Momentum direction, strength, and latest movement ranking. Strength is a descriptive magnitude band, not a forecast.
- Alerts for direction flips, sudden quote moves and final-minute countdown warnings.
- Last 10 settled contract results per market shown as arrows: ↑ YES / ↓ NO, oldest to newest. These arrows represent the Kalshi contract result, not the underlying asset price direction.
- Dashboard-only alerts, no API keys, no trading, no order placement.

## Deploy
Replace **root-level** `app.py` and `requirements.txt` in the existing GitHub repo. Commit changes and allow Streamlit Community Cloud to redeploy. Do not upload an extra nested folder.

## Notes
Requires `GET /markets` to return contracts for the configured Kalshi series tickers. Settled results require the API to return `result: yes` or `result: no`; missing results are omitted. Requests for settled results are cached for 60 seconds. Quotes cached for 8 seconds. Session quote history resets on app restart. No guarantee that API series tickers and response fields remain unchanged.

Signal strength: Weak (<2¢), Moderate (2–<4¢), Strong (≥4¢) absolute YES quote movement per sample. A stronger signal is not necessarily a better trade. No statistical validation has been performed.

## Charts
- Each market card now includes a live-updating line chart of sampled YES contract quote prices (in cents).
- A comparison bar chart shows recent YES quote movement across Bitcoin, Gold, Silver, and WTI Oil.
- A results chart counts YES and NO settlements among up to 10 recent completed contracts per market, when available.
- Charts are based on public Kalshi market data and local Streamlit session history; they are not predictions, continuous tick charts, or automated trades.
- Chart history resets when the Streamlit session restarts or the current contract rolls to a new ticker.

## Faster refresh and quote styling
- Choose 3, 5, 10, or 15 seconds in the sidebar (3 seconds by default).
- Each contract countdown is computed from the Kalshi API close timestamp and the current clock; it updates when the Streamlit app refreshes, not continuously between refreshes.
- YES and NO bid/ask are displayed as colored, button-like **noninteractive** tiles. Clicking them does not trade.
- 3-second polling can hit Kalshi rate limits, experience latency, or increase Streamlit Community Cloud load. Choose 5 or 10 seconds if errors occur.
- Public API polling is not a Kalshi WebSocket live feed. Charts are sampled at refreshes, and each session retains its own history.
