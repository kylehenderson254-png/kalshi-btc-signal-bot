# Kalshi BTC 15-Minute Signal Dashboard (starter)

This starter dashboard is for live signals and safe observation only. It does not place orders and does not connect to your Kalshi account.

## What it does
- Pulls public BTC/USDT spot price and recent 1-minute candles from Binance.
- Lets you enter the contract's exact dollar target and settlement time.
- Estimates above/below probabilities with a simple normal-distribution baseline.
- Shows ABOVE, BELOW, or NO TRADE.
- Refreshes automatically.

## Important limitations
This is **not a validated AI model**. It uses recent return average and volatility as a simplistic statistical estimate. The output can be badly calibrated and is not evidence of a profitable edge. Binance BTC/USDT may differ from the reference used by a particular Kalshi contract. Verify the exact contract settlement rules and time before interpreting the result. This first version does not read Kalshi market prices or official settlement data.

## Run it
1. Install Python 3.10 or newer on a computer.
2. Unzip this folder.
3. Open a terminal/command prompt in the folder.
4. Install packages:
   `python -m pip install -r requirements.txt`
5. Start the dashboard:
   `python -m streamlit run app.py`
6. Open the local URL printed in the terminal.

## Phone access
For a quick test, your phone and computer can be on the same Wi-Fi. Run Streamlit with:
`python -m streamlit run app.py --server.address 0.0.0.0`
Then open `http://COMPUTER_LOCAL_IP:8501` on your phone. Do not expose this port to the public internet. For persistent access away from home, deploy to a trusted hosting service and review its access/privacy settings first.

## Next development steps
1. Save every forecast with timestamp, target, settlement time, price, probability, and eventual outcome.
2. Build a historical dataset and test on later, unseen periods.
3. Add contract-specific Kalshi public market discovery and settlement-reference handling.
4. Measure probability calibration and paper-trading outcomes before considering any live trading.
