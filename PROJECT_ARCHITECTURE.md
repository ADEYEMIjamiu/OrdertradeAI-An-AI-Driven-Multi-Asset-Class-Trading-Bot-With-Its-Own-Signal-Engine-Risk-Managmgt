# AI Trading Bot — Project Architecture

_Last audited: 2026-07-10_

## High-Level Goal
This system is designed to become a professional AI trading platform that scans multiple markets, generates AI trade signals, filters them through risk and portfolio rules, sizes positions dynamically, and routes execution to the correct broker.

---

## Current Folder Structure

```text
AI-Trading-Bot/
├── app.py
├── config.py
├── broker.py
├── train_model.py
├── trade_journal.py
├── trade_journal.db
├── requirements.txt
├── data/
│   └── asset_universe.py
├── engines/
│   ├── approval_engine.py
│   ├── execution_engine.py
│   ├── portfolio_engine.py
│   ├── position_engine.py
│   ├── position_sizing_engine.py
│   ├── priority_engine.py
│   ├── regime_engine.py
│   ├── risk_engine.py
│   ├── scoring_engine.py
│   ├── strategy_engine.py
│   └── trade_planner.py
├── models/
│   ├── trading_model.pkl
│   └── features.pkl
├── paper_trading/
│   └── paper_engine.py
├── brokers/
├── broker/
├── strategies/
├── strategy/
└── logs/
```

---

## Current Engine Responsibilities

### `data/asset_universe.py`
Defines supported asset classes and enabled symbols.

Current enabled asset classes:
- `US_STOCKS` → broker: `alpaca`
- `CRYPTO` → broker: `binance` but execution blocked

Defined but disabled:
- `FOREX`
- `COMMODITIES`

---

### `engines/regime_engine.py`
Responsible for broad market environment.

Functions:
- `get_market_risk_level(market_df)`
- `get_market_regime()`

Outputs:
- Risk level
- Risk multiplier
- Market regime
- Regime score

---

### `engines/strategy_engine.py`
Responsible for identifying the type and quality of a setup.

Functions:
- `identify_strategy(row)`
- `score_strategy(row)`

Outputs:
- Strategy name
- Strategy Score

---

### `engines/trade_planner.py`
Responsible for stop loss, take profit, and risk/reward planning.

Functions:
- `prepare_trade_data(ticker)`
- `create_trade_plan(row)`

Outputs:
- Trade Decision
- Stop Loss
- Take Profit
- Risk Reward
- Trade Grade
- Trade Reason

---

### `engines/scoring_engine.py`
Responsible for final AI trade quality score.

Functions:
- `calculate_trade_score(row)`

Outputs:
- AI Trade Score

---

### `engines/approval_engine.py`
Responsible for final approval gate before execution.

Functions:
- `approve_trade(row)`

Outputs:
- Trade Approved
- Approval Reason

---

### `engines/priority_engine.py`
Responsible for ranking approved trades.

Functions:
- `calculate_priority(row)`

Outputs:
- Priority

---

### `engines/portfolio_engine.py`
Responsible for portfolio allocation, previews, and exposure filters.

Functions:
- `calculate_asset_allocation(positions, market_df)`
- `can_add_asset_class(asset_class, allocation, portfolio_value, max_asset_exposure)`
- `rank_trades_by_portfolio_fit(trade_queue)`
- `preview_allocation_after_trades(...)`
- `filter_trades_by_portfolio_limits(...)`

Outputs:
- Asset allocation
- Preview allocation
- Portfolio-approved trades
- Portfolio-rejected trades

---

### `engines/position_sizing_engine.py`
Responsible for dynamic trade amount.

Functions:
- `confidence_multiplier(confidence)`
- `strategy_multiplier(strategy_score)`
- `regime_multiplier(regime)`
- `exposure_multiplier(exposure_percent)`
- `calculate_position_size(...)`

Outputs:
- Position Size

---

### `engines/risk_engine.py`
Responsible for risk checks and portfolio helper calculations.

Functions:
- `calculate_trade_amount(...)` — legacy/older sizing helper
- `can_open_position(ticker)`
- `risk_check_before_trade(ticker, trade_amount, market_df)`
- `get_dynamic_buy_confidence(market_df)`
- `get_exposure_percent(market_df)`
- `calculate_portfolio_value(market_df)`
- `get_open_positions_value(market_df)`

Note: `calculate_trade_amount` is now mostly superseded by `position_sizing_engine.calculate_position_size`.

---

### `engines/execution_engine.py`
Responsible for trade queue sorting and asset-class execution filter.

Functions:
- `sort_trade_queue(df)`
- `filter_executable_trades(trade_queue, allowed_asset_classes=None)`

Current behavior:
- Only `US_STOCKS` are executable.
- `CRYPTO` is scanned but blocked from execution.

---

### `engines/position_engine.py`
Responsible for managing local paper positions.

Functions:
- `initialize_position(position, stop_loss, take_profit)`
- `update_position(position, current_price)`
- `check_position_exit(position, current_price)`

---

## Main App Pipeline

Current `app.py` orchestration sequence:

```text
1. Load configuration and models
2. Load enabled asset universe
3. Generate AI signals per asset
4. Add asset class and broker metadata
5. Identify strategy
6. Score strategy
7. Create trade plan
8. Calculate AI Trade Score
9. Approve/reject trade
10. Assign priority
11. Sort trade queue
12. Apply risk management
13. Calculate portfolio value/exposure
14. Apply portfolio exposure limits
15. Filter executable trades by asset class
16. Calculate position size
17. Display watchlist and decision tables
18. Execute paper or Alpaca trades on button click
19. Display performance, positions, orders, and status
```

---

## Current Execution Rules

- `US_STOCKS` can be executed through Alpaca.
- `CRYPTO` can be scanned but is blocked from execution.
- Automatic live trading is locked for safety.
- Execution currently still lives mainly in `app.py` through:
  - `execute_paper_trades`
  - `execute_alpaca_trades`

---

## Key Technical Debt

1. `app.py` still contains AI/data functions:
   - `prepare_data`
   - `get_ai_signal`
   - `get_multi_timeframe_signal`

2. `app.py` still contains execution functions:
   - `execute_paper_trades`
   - `execute_alpaca_trades`
   - `apply_risk_management`

3. Duplicate/unnecessary config imports exist in `app.py`.

4. `config.py` contains duplicate constant definitions for:
   - `MAX_OPEN_POSITIONS`
   - `MAX_PORTFOLIO_EXPOSURE`
   - `MIN_TRADE_AMOUNT`
   - `MAX_TRADE_AMOUNT`
   - `RISK_PER_TRADE`

5. There are duplicate placeholder folders:
   - `broker/` and `brokers/`
   - `strategy/` and `strategies/`

6. Project upload included `.env`; this should be excluded from future uploads and commits.

---

## Recommended Target Architecture

```text
AI-Trading-Bot/
├── app.py                         # UI and orchestration only
├── config.py
├── engines/
│   ├── ai_engine.py
│   ├── regime_engine.py
│   ├── strategy_engine.py
│   ├── trade_planner.py
│   ├── scoring_engine.py
│   ├── approval_engine.py
│   ├── priority_engine.py
│   ├── portfolio_engine.py
│   ├── risk_engine.py
│   ├── position_engine.py
│   ├── position_sizing_engine.py
│   ├── execution_engine.py
│   ├── order_manager.py
│   └── performance_engine.py
├── brokers/
│   ├── alpaca_broker.py
│   ├── binance_broker.py
│   ├── oanda_broker.py
│   └── ibkr_broker.py
├── data/
│   ├── asset_universe.py
│   ├── market_data.py
│   └── feature_engineering.py
├── models/
├── database/
├── logs/
└── tests/
```

---

## Immediate Architecture Priority

Do not add more dashboard features until:
1. `.gitignore` is added.
2. `.env` is excluded from future uploads.
3. Order Management System v1 is started.
4. Execution functions are moved out of `app.py`.
5. Broker Router skeleton is created.


###---------------------------

## Future SaaS Architecture 

The current application is a single-user local trading system.
A future commercial version will use a multi-user service architecture.

### Main Components
1. Web application frontend
2. Authentication and user-management service
3. Subscription and entitlement service
4. Trading API backend
5. Strategy engine
6. Risk and approval engine
7. Position-sizing engine
8. Broker integration layer
9. Order-management and reconciliation service
10. Notification service
11. Encrypted secrets manager
12. PostgreSQL database
13. Background task queue
14. Monitoring and audit-log service

### Security Principles
- No shared broker account between users
- No broker secrets stored in source code
- Per-user encrypted broker credentials
- Strict separation of paper and live trading
- Live trading disabled by default
- Per-user kill switch and global emergency stop
- Complete order and decision audit trail
- Least-privilege access controls
- Staging tests before production deployment


# AI Trading Bot – Architecture

## Components

### 1. Signal Engine
- Generates BUY / SELL / HOLD

### 2. Risk Engine
- Stop loss / take profit

### 3. Execution Engine
- Sends orders to broker

### 4. Broker Layer
- Alpaca (stocks)
- Binance (crypto later)

### 5. Notification Layer
- Telegram alerts

### 6. UI Layer
- Streamlit dashboard

## Flow

Market Data → Signal → Risk Check → Execution → Broker → Log → Notify