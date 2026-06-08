"""portfolio — Stage 7. Manage open positions and exits.

Marks positions against live price (DexScreener/Jupiter); applies rules-based exits from config:
take-profit ladder, hard stop-loss, trailing stop, time-stop (memes decay fast), and
"smart-money exited => we exit". Persists PnL. Tracks exposure for the risk limits used in Stage 5/6.
"""
