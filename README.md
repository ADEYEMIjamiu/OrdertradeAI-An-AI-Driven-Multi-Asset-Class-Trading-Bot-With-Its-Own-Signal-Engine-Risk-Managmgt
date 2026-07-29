# OrderTrade AI

An AI-driven, multi-asset-class trading bot with its own signal engine, risk management, tiered trade grading, and a live dashboard — built as an independent project to learn how real trading systems are designed end-to-end, from signal generation through execution, risk control, and monitoring.

Currently running in **paper-trading mode** (simulated and broker-testnet execution only, no real money) while results are validated over several weeks, with plans to open it up for other traders to try afterward.

---

## What it does

OrderTrade AI scans four asset classes for trade opportunities, scores each one with a trained machine learning model, filters it through several layers of risk and portfolio logic, and either logs it as a simulated trade or routes it to a real broker's paper/testnet environment:

- **US Stocks** — via the Alpaca paper-trading API
- **Crypto** — via the Binance Testnet API
- **Forex** — simulated (no live forex broker integrated yet)
- **Commodities** — simulated (no live commodities broker integrated yet)

Every trade idea moves through the same pipeline before it's allowed to execute:

```
Market Data → Signal (ML model) → Strategy Scoring → Trade Planning
    → Risk & Portfolio Checks → Approval Gate → Trade Grade (A+ to D)
    → Position Sizing → Execution → Performance Tracking → Telegram Alert
```

## Key features

**Tiered trade grading.** Every signal is graded A+ through D based on both the model's confidence and its risk/reward ratio, not confidence alone — so a high-confidence setup with a poor reward-to-risk profile doesn't get treated the same as a genuinely strong one.

**Dynamic position sizing and portfolio limits.** Trade size scales with signal confidence, strategy strength, and current market regime, and is capped by per-asset-class exposure limits so no single market can dominate the portfolio.

**Multi-asset paper-trading engine.** Stocks and crypto route through their respective broker APIs; forex and commodities run through the same local paper-trading engine so every asset class can be tested with real market data before a live broker integration exists for them.

**Performance digest.** A built-in dashboard section breaks down realized P&L by day, week, month, and all-time, split by asset class, using FIFO trade matching as the single source of truth for closed-trade accounting.

**Telegram alerts.** Real-time notifications on every trade fill and on-demand performance digests, so the bot can be monitored without needing the dashboard open.

**24/7 unattended operation.** Deployed on a small cloud VPS behind Nginx with HTTP Basic Auth, run as a systemd service (auto-restart on crash, starts on boot), with a safety guard around Streamlit's autorefresh to reduce the risk of interrupted trade execution.

## Tech stack

- **Language / framework:** Python, Streamlit
- **Machine learning:** scikit-learn, XGBoost
- **Market data:** yfinance
- **Brokers:** Alpaca (stocks, paper), Binance (crypto, testnet)
- **Alerts:** Telegram Bot API
- **Deployment:** systemd, Nginx, Ubuntu VPS

## Project structure

```
AI-Trading-Bot/
├── app.py                     # Streamlit dashboard and orchestration
├── config.py                  # Grading thresholds, risk/portfolio constants
├── broker.py                  # Alpaca broker interface
├── binance_broker.py          # Binance testnet broker interface
├── telegram_notifier.py       # Trade-fill and digest alerts
├── data/
│   └── asset_universe.py      # Enabled asset classes and symbols
├── engines/
│   ├── strategy_engine.py     # Setup identification and scoring
│   ├── trade_planner.py       # Stop loss / take profit / trade grade
│   ├── scoring_engine.py      # Final AI trade score
│   ├── approval_engine.py     # Final approval gate
│   ├── priority_engine.py     # Trade queue ranking
│   ├── portfolio_engine.py    # Allocation and exposure limits
│   ├── position_sizing_engine.py
│   ├── risk_engine.py
│   ├── execution_engine.py
│   ├── performance_engine.py  # FIFO trade matching, realized P&L
│   └── digest_engine.py       # Period-based performance breakdown
├── paper_trading/
│   └── paper_engine.py        # Local paper-trading simulation
├── models/                    # Trained ML model + feature set
└── deploy/                    # systemd + Nginx configs for VPS hosting
```

## Running it locally

```bash
git clone <this-repo-url>
cd AI-Trading-Bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then fill in .env with your own Alpaca / Binance testnet / Telegram keys

streamlit run app.py
```

All broker connections above are paper/testnet only — no real funds are ever at risk running this as-is.

## Status

This project is in active development and currently mid-validation on paper trading across all four asset classes. Forex and commodities broker integrations, HTTPS, and multi-user support are on the roadmap but not yet built.

## Disclaimer

This is an independent, self-initiated learning project (not a university assignment) built to explore applied machine learning, systems architecture, and trading infrastructure. It is for educational and research purposes only, is not financial advice, and has not been validated with real capital.

## Author

Built by Adeyemi Jamiu Adegbenro — Data Science, Artificial Intelligence & Digital Business student.
