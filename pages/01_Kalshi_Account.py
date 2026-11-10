
"""QUEEN'S EDGE | 254 — read-only Kalshi account monitor.

Displays authenticated account data.
Never submits, cancels, or modifies orders.
"""

import base64
import time
from urllib.parse import urlsplit

import requests
import streamlit as st
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


API = "https://external-api.kalshi.com/trade-api/v2"


def credentials():
    key_id = str(st.secrets.get("KALSHI_API_KEY_ID", "")).strip()
    private_key = str(st.secrets.get("KALSHI_PRIVATE_KEY", "")).strip()

    if not key_id or not private_key:
        raise ValueError("Kalshi credentials are missing from Streamlit Secrets.")

    private_key = private_key.replace("\\n", "\n")

    if "BEGIN" not in private_key or "END" not in private_key:
        raise ValueError("The Kalshi private key does not appear to be a complete PEM key.")

    key = serialization.load_pem_private_key(
        private_key.encode("utf-8"),
        password=None,
    )

    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("This connection expects the Ed25519 key created for Queen.")

    return key_id, key


def kalshi_get(path, params=None):
    if not path.startswith("/"):
        raise ValueError("Invalid API path.")

    key_id, key = credentials()
    timestamp = str(int(time.time() * 1000))

    # Kalshi signs timestamp + HTTP method + path (without query parameters).
    signing_path = urlsplit(API + path).path
    message = (timestamp + "GET" + signing_path).encode("utf-8")
    signature = base64.b64encode(key.sign(message)).decode("ascii")

    headers = {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "Accept": "application/json",
        "Cache-Control": "no-store",
    }

    response = requests.get(
        API + path,
        headers=headers,
        params=params,
        timeout=12,
    )

    if response.status_code in (401, 403):
        raise PermissionError(
            "Kalshi rejected authentication or account-read permission."
        )

    if not response.ok:
        raise RuntimeError(
            f"Kalshi account request failed (HTTP {response.status_code})."
        )

    return response.json()


def money_from_cents(value):
    try:
        return f"${int(value) / 100:,.2f}"
    except (TypeError, ValueError):
        return "—"


def positions():
    rows = []
    cursor = None

    for _ in range(20):
        params = {"limit": 200, "count_filter": "position"}
        if cursor:
            params["cursor"] = cursor

        data = kalshi_get("/portfolio/positions", params)
        batch = data.get("market_positions", [])

        if not isinstance(batch, list):
            raise ValueError("Unexpected Kalshi positions response.")

        rows.extend(batch)
        next_cursor = data.get("cursor")

        if not next_cursor:
            return rows, True

        if next_cursor == cursor:
            raise ValueError("Kalshi returned a repeated pagination cursor.")

        cursor = next_cursor

    return rows, False


st.set_page_config(
    page_title="Queen's Edge | Kalshi Account",
    page_icon="👑",
    layout="wide",
)

st.title("👑 QUEEN'S EDGE | 254")
st.subheader("Kalshi account · read-only")
st.caption(
    "Real Kalshi account information. Separate from Queen's V4 paper, "
    "Shadow V1, and Near Miss results. No trading actions."
)

if st.button("Refresh Kalshi account", type="primary"):
    st.rerun()

try:
    balance_data = kalshi_get("/portfolio/balance")

    st.success("KALSHI CONNECTED — READ ONLY")
    st.caption("Authenticated account request succeeded.")

    a, b = st.columns(2)

    a.metric(
        "Available balance",
        money_from_cents(balance_data.get("balance")),
    )

    b.metric(
        "Reported portfolio value",
        money_from_cents(balance_data.get("portfolio_value")),
    )

    st.divider()
    st.subheader("Real Kalshi positions")

    market_positions, complete = positions()

    display = []

    for item in market_positions:
        try:
            position = float(
                item.get("position_fp", item.get("position", 0)) or 0
            )
        except (TypeError, ValueError):
            position = 0

        if position == 0:
            continue

        display.append({
            "Contract": item.get("ticker", "—"),
            "Position (signed contracts)": position,
            "Reported realized P&L": (
                item.get("realized_pnl_dollars")
                if item.get("realized_pnl_dollars") is not None
                else "Not supplied"
            ),
        })

    if display:
        st.dataframe(
            display,
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("No nonzero positions returned by Kalshi.")

    if not complete:
        st.warning(
            "Kalshi position pagination was incomplete. "
            "Not all positions may be displayed."
        )

    st.caption(
        "Positive position counts generally represent YES exposure; "
        "negative counts represent NO exposure. Realized P&L is shown "
        "only if Kalshi supplies it. Portfolio value is not profit. "
        "Unrealized and total net P&L are not calculated here."
    )

except PermissionError:
    st.error(
        "KALSHI NOT CONNECTED — authentication or read permission failed. "
        "Check that Queen's API Key ID matches her private key."
    )

except ValueError as exc:
    st.error(f"KALSHI NOT CONNECTED — {exc}")

except requests.RequestException:
    st.error(
        "KALSHI NOT CONNECTED — network request failed. "
        "Try refreshing."
    )

except Exception:
    st.error(
        "KALSHI NOT CONNECTED — account data could not be loaded. "
        "Check the private-key format and application logs."
    )

st.divider()
st.caption(
    "QUEEN'S EDGE | 254 · Account monitoring only · "
    "GET requests only · No orders"
)
