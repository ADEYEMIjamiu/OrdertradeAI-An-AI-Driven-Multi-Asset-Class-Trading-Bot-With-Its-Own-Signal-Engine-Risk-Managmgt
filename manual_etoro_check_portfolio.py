"""
One-off diagnostic: prints every open eToro position for this user's
demo account, with resolved symbol names -- used to check whether an
order that didn't get a confirmed match within buy_etoro_for_user()'s
15s poll window actually filled a bit late, rather than truly failing.

Run ON THE DROPLET:
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 manual_etoro_check_portfolio.py
"""

import requests

from engines import tenant_engine as tenant
from engines import saas_broker_factory as factory


def main():
    user_ids = tenant.list_active_users()
    if not user_ids:
        print("No active users found.")
        return

    user_id = user_ids[0]
    creds = factory._require_etoro_creds(user_id)
    headers = factory._etoro_headers_for_user(creds)

    portfolio_path = factory._etoro_portfolio_path(creds)
    response = requests.get(
        f"{factory.ETORO_API_BASE}/{portfolio_path}",
        headers=headers,
        timeout=25,
    )
    response.raise_for_status()
    portfolio = response.json().get("clientPortfolio", {})

    positions = portfolio.get("positions", [])
    print(f"credit={portfolio.get('credit')}  open_positions={len(positions)}\n")

    if not positions:
        print("No open positions at all.")
        return

    # Build instrumentId -> symbol reverse lookup from the same catalog
    # buy_etoro_for_user() uses, so we can show human-readable tickers.
    catalog = factory._load_etoro_catalog_for_user(user_id, creds)
    id_to_symbol = {v: k for k, v in catalog.items()}

    for p in positions:
        instrument_id = p.get("instrumentID")
        symbol = id_to_symbol.get(instrument_id, f"instrumentId={instrument_id}")
        print(
            f"positionID={p.get('positionID')}  orderID={p.get('orderID')}  "
            f"symbol={symbol}  openRate={p.get('openRate')}  "
            f"amount={p.get('amount')}  leverage={p.get('leverage')}  "
            f"isBuy={p.get('isBuy')}  netProfit={p.get('netProfit')}"
        )


if __name__ == "__main__":
    main()
