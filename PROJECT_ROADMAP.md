# AI Trading Bot — Project Roadmap

_Last audited: 2026-07-10_

## Project Goal
Build a professional AI trading platform that can scan multiple asset classes, generate AI trade signals, rank trade quality, control risk, size positions intelligently, route orders to the correct broker, begin in paper trading, and later support live trading with real capital under strict safety controls.

---

## Status Legend
- ✅ Completed
- 🟨 In Progress
- ⬜ Not Started
- 🔴 Needs Refactoring / Review
- 🔵 Future Version

---

## Phase 1 — Core Trading Platform

### Market Data and Asset Universe
- ✅ Live market data through `yfinance`
- ✅ Multi-asset universe in `data/asset_universe.py`
- ✅ US stocks enabled
- ✅ Crypto scanning enabled
- ✅ Forex defined but disabled
- ✅ Commodities defined but disabled
- ✅ Asset class and broker columns added to watchlist
- 🔴 Data layer still partly inside `app.py` (`prepare_data`, `get_ai_signal`, `get_multi_timeframe_signal`)

### AI Signal Engine
- ✅ Trained model stored in `models/trading_model.pkl`
- ✅ Feature list stored in `models/features.pkl`
- ✅ AI confidence calculation
- ✅ BUY / HOLD / SELL signal generation
- ✅ Multi-timeframe trend adjustment
- 🔴 AI engine still partly inside `app.py`

---

## Phase 2 — Trading Intelligence Engines

### Regime Engine
- ✅ `engines/regime_engine.py`
- ✅ Market risk level
- ✅ Market regime score from SPY
- ✅ Regime dashboard display

### Strategy Engine
- ✅ `engines/strategy_engine.py`
- ✅ Strategy classification
- ✅ Strategy score

### Trade Planner
- ✅ `engines/trade_planner.py`
- ✅ ATR-based stop loss
- ✅ ATR-based take profit
- ✅ Risk/reward ratio
- ✅ Trade grade
- ✅ Trade reason

### Scoring and Approval
- ✅ `engines/scoring_engine.py`
- ✅ AI Trade Score
- ✅ `engines/approval_engine.py`
- ✅ Trade approval decision
- ✅ Approval reason

### Priority Engine
- ✅ `engines/priority_engine.py`
- ✅ Priority stars
- ✅ Execution queue sorting support

---

## Phase 3 — Risk, Portfolio, and Position Management

### Risk Engine
- ✅ `engines/risk_engine.py`
- ✅ Max open positions check
- ✅ Cash check
- ✅ Portfolio exposure check
- ✅ Daily trade limit check
- ✅ Ticker cooldown check
- ✅ Dynamic buy confidence
- ✅ Portfolio value helpers
- 🔴 Some duplicate/legacy risk calculations still remain in `app.py`

### Portfolio Engine
- ✅ `engines/portfolio_engine.py`
- ✅ Asset allocation by asset class
- ✅ Portfolio preview after AI trades
- ✅ Portfolio exposure limit filter
- ✅ Trade queue split into approved/rejected portfolio trades
- 🔴 Portfolio approved/rejected trades need clearer dashboard display

### Position Engine
- ✅ `engines/position_engine.py`
- ✅ Initialize position
- ✅ Update highest/lowest price
- ✅ Check stop loss and take profit exit
- 🟨 Trailing stop logic exists for Alpaca live risk management but is still in `app.py`
- ⬜ Break-even stop movement
- ⬜ Partial profit taking
- ⬜ Time-based exits

### Position Sizing Engine
- ✅ `engines/position_sizing_engine.py`
- ✅ Confidence multiplier
- ✅ Strategy multiplier
- ✅ Regime multiplier
- ✅ Exposure multiplier
- ✅ Final dynamic position size
- ✅ Watchlist shows Position Size
- ✅ Paper execution uses Position Size
- 🟨 Confirm live Alpaca execution also uses Position Size in all branches

---

## Phase 4 — Execution Layer

### Execution Engine
- ✅ `engines/execution_engine.py`
- ✅ Sort trade queue
- ✅ Filter executable asset classes
- ✅ Crypto blocked from execution for safety
- ✅ US stocks executable through Alpaca path
- 🟨 Execution functions still mainly live inside `app.py` (`execute_paper_trades`, `execute_alpaca_trades`)

### Order Management System
- ⬜ Order object model
- ⬜ Pending orders
- ⬜ Submitted orders
- ⬜ Filled orders
- ⬜ Partially filled orders
- ⬜ Cancelled orders
- ⬜ Failed orders
- ⬜ Retry logic
- ⬜ Order expiration
- ⬜ Execution logger
- ⬜ Slippage tracking
- ⬜ Commission tracking

### Broker Router
- ⬜ Broker router engine
- ⬜ Alpaca route for US stocks
- ⬜ Binance route for crypto
- ⬜ OANDA route for forex
- ⬜ Interactive Brokers route for global assets/futures
- ⬜ Broker health monitor

---

## Phase 5 — Broker Integrations

### Alpaca
- ✅ `broker.py` with Alpaca Paper Trading connection
- ✅ Account info
- ✅ Buy order
- ✅ Sell order
- ✅ Open positions
- ✅ Order history
- 🟨 Live trading locked for safety
- 🔴 `.env` was included in uploaded project; do not share secrets in future uploads

### Binance / Crypto
- ✅ Crypto symbols scan through `yfinance`
- ✅ Crypto tagged as broker `binance`
- ✅ Crypto execution blocked
- ⬜ Binance API client
- ⬜ Binance paper/testnet mode
- ⬜ Crypto order execution
- ⬜ Crypto position sync

### OANDA / Forex
- ✅ Forex universe defined but disabled
- ⬜ OANDA API client
- ⬜ Forex market data normalization
- ⬜ Forex execution routing

### Interactive Brokers
- ✅ Commodities universe defined but disabled
- ⬜ IBKR integration
- ⬜ Futures/commodities routing

---

## Phase 6 — Journal and Analytics

### Trade Journal
- ✅ `trade_journal.py`
- ✅ SQLite database exists: `trade_journal.db`
- ✅ Basic trade logging functions
- 🟨 Needs full integration for all execution paths
- ⬜ Log exit price
- ⬜ Log realized P/L
- ⬜ Log strategy
- ⬜ Log trade score
- ⬜ Log asset class
- ⬜ Log broker
- ⬜ Log slippage and commissions

### Performance Analytics
- ⬜ Performance engine
- ⬜ Win rate
- ⬜ Profit factor
- ⬜ Expectancy
- ⬜ Sharpe ratio
- ⬜ Sortino ratio
- ⬜ Max drawdown
- ⬜ Strategy performance
- ⬜ Asset-class performance
- ⬜ Broker performance
- ⬜ Monthly returns

---

## Phase 7 — Portfolio Intelligence
- ⬜ Correlation engine
- ⬜ Sector exposure engine
- ⬜ Concentration risk engine
- ⬜ Portfolio optimizer
- ⬜ Rebalancing engine
- ⬜ Capital allocation AI
- ⬜ Risk budgeting

---

## Phase 8 — AI Learning
- ⬜ Strategy learning
- ⬜ Confidence calibration
- ⬜ Adaptive thresholds
- ⬜ Adaptive position sizing
- ⬜ Auto retraining pipeline
- ⬜ Reinforcement learning research mode
- ⬜ Dynamic strategy selection

---

## Phase 9 — Backtesting and Validation
- ⬜ Backtesting engine
- ⬜ Walk-forward testing
- ⬜ Strategy comparison
- ⬜ Monte Carlo risk simulation
- ⬜ Out-of-sample validation
- ⬜ Paper trading validation report

---

## Phase 10 — Production and Deployment
- ⬜ Remove secrets from project archives
- ⬜ `.gitignore`
- ⬜ Logging system
- ⬜ Docker
- ⬜ VPS deployment
- ⬜ 24/7 process monitor
- ⬜ Alerts/notifications
- ⬜ Backup system
- ⬜ Kill switch / emergency stop
- ⬜ Live trading checklist

---

## Immediate Next Tasks
1. 🔴 Create `.gitignore` and remove `.env`, `__pycache__`, `.DS_Store`, and local databases from uploads.
2. 🟨 Build Order Management System v1.
3. 🟨 Move execution functions from `app.py` into `engines/execution_engine.py`.
4. 🟨 Add Broker Router skeleton.
5. 🟨 Expand trade journal to capture complete lifecycle.
6. ⬜ Add Performance Analytics Engine.


---

## Current Development Status

**Last updated:** 10 July 2026

### Phase 1 — AI Signal and Market Analysis
Status: COMPLETE

Completed components:

- Market data collection
- Technical indicator generation
- Trained AI classification model
- BUY / HOLD / SELL signals
- Multi-timeframe trend analysis
- Market regime detection
- Strategy identification
- Trade scoring
- Trade approval logic
- Trade prioritisation

---

### Phase 2 — Paper Trading and Portfolio Management
Status: COMPLETE

Completed components:

- $100,000 paper-trading account
- Dynamic position sizing
- Portfolio exposure calculation
- Risk management checks
- Maximum daily trade limit
- Trade cooldown protection
- Manual trade execution
- Automatic paper-trading option
- Current positions tracking
- AI Order Manager
- Trade Log
- Unique Order IDs
- Order status tracking
- Cash balance updates
- Portfolio value updates
- Asset allocation
- Portfolio preview
- Equity history
- Profit and loss calculation
- Performance dashboard
- Stop-loss and take-profit framework
- SELL execution framework

### Confirmed Phase 2 Test Result

A successful SPY paper trade was executed:

- Order status: FILLED
- Cash reduced correctly
- Position created correctly
- AI Order Manager updated
- Trade Log updated
- Portfolio exposure updated
- Capital invested updated
- Remaining daily trades reduced correctly

### Known Behaviour

Win Rate and Trades Closed remain zero until an open position is sold.

---

### Phase 3 — Decision Consistency and Advanced Trade Management
Status: IN PROGRESS

Current task:

1. Make the AI Decision Engine use the same executable-trade dataset as the execution system.
2. Prevent disagreement between:
   - Live Market Watchlist
   - AI Decision Engine
   - Execute Trades button
3. Add clearer reasons when a signal is visible but not executable.
4. Confirm BUY and SELL decisions remain consistent across the application.

### Resume Point

Continue from:

**Phase 3, Step 1 — Fix AI Decision Engine consistency.**

### Phase 2 – Paper Trading Execution Engine

Status: Nearly complete

Completed:
- Manual execution uses approved BUY and SELL tables
- Automatic execution uses approved BUY and SELL tables
- Dynamic position sizing operational
- Cash updates correctly after execution
- Current positions update correctly
- AI Order Manager records filled orders
- Trade Log records executed trades
- Cooldown prevents duplicate purchases
- Portfolio exposure displays correctly
- Manual paper-trade execution successfully tested with NVDA

Remaining:
- Correct Portfolio Preview After AI Trades so it does not add a fallback amount
  for an already-held or cooldown-protected ticker
- Perform final automatic-trading validation

### Phase 2 – Paper Trading Execution Engine

Status: COMPLETE

Completed:
- Manual paper-trade execution
- Approved BUY and SELL filtering
- Dynamic position sizing
- Cash and portfolio updates
- Current-position tracking
- AI Order Manager
- Trade Log
- Unique order IDs
- Cooldown protection
- Duplicate-position prevention
- Portfolio exposure calculation
- Portfolio preview uses approved position sizes
- Portfolio preview excludes already-held tickers
- Paper-trading execution validated successfully

Next:
- Phase 3 – Broker Integration and Alpaca Paper-Trading Validation









# Phase 6 — SaaS Commercialisation and Multi-User Platform

## Objective
Transform the validated personal AI Trading Machine into a secure,
subscription-based trading software platform.

## Planned Features
- Product name, brand identity and website
- Secure user registration and authentication
- Seven-day free trial
- Monthly and annual subscription plans
- Stripe billing and entitlement management
- Multi-user portfolio isolation
- Per-user broker integration
- Encrypted storage of broker credentials
- Per-user strategy and risk configuration
- Notification centre and Telegram alerts
- Administrative dashboard
- Usage monitoring and audit logs
- Cloud deployment and background workers
- Staging and production environments
- Security, penetration and reliability testing
- Legal and regulatory review before public launch

## Commercialisation Gate
The product must not be offered for paid public use until:
- Paper-trading performance has been validated
- Broker reconciliation is reliable
- Risk controls and emergency shutdown have been tested
- User data and credentials are securely isolated
- Terms, privacy policy and risk disclosures are complete
- The applicable regulatory position has been professionally reviewed

## Status
PLANNED — begins only after strategy validation, broker integration,
automation testing and production hardening are complete.


## Phase 3.5 — Broker State Reconciliation

- [x] Alpaca broker health check
- [x] Alpaca paper account metrics
- [x] Alpaca position retrieval
- [x] Alpaca order-history retrieval
- [x] Accurate Alpaca Paper Trading mode label
- [x] Broker-based daily filled-order counter
- [x] Broker-based remaining-trades calculation
- [x] Disable local reset control in broker mode
- [ ] Broker-derived performance metrics
- [ ] Broker versus proposed-portfolio separation
- [ ] Order submission and fill reconciliation
- [ ] Controlled market-open broker test

### Phase 3.5 — Alpaca Performance Synchronisation

- [x] Load Alpaca account equity and cash
- [x] Load Alpaca open positions
- [x] Load Alpaca filled orders
- [x] Display broker-derived capital invested and exposure
- [x] Count completed sell transactions
- [ ] Match Alpaca BUY and SELL order lifecycles
- [ ] Calculate realised broker profit and loss
- [ ] Calculate broker win/loss counts and win rate
- [ ] Build Alpaca-backed equity history
- [ ] Validate dashboard metrics against Alpaca

### Phase 3.5 Progress — Alpaca Realised Performance

- [x] Load Alpaca account equity and cash
- [x] Load current Alpaca positions
- [x] Load filled Alpaca orders
- [x] Count completed sell transactions
- [x] Implement FIFO BUY-to-SELL lifecycle matching
- [x] Calculate matched cost basis
- [x] Calculate realised broker profit and loss
- [x] Calculate wins, losses and breakeven trades
- [x] Calculate broker win rate
- [x] Add matched-trade verification table
- [ ] Validate NVDA realised result against broker history
- [ ] Feed broker equity into the live equity curve
- [ ] Synchronise completed lifecycle data with SQLite trade journal

### Phase 3.5 — Alpaca Broker-Backed Dashboard Metrics ✅ COMPLETED

Completed:
- Alpaca paper-account connection verified
- Broker account status and market clock displayed
- Account equity and cash loaded directly from Alpaca
- Open positions loaded directly from Alpaca
- Broker order history displayed
- Daily remaining-trades calculation connected to Alpaca orders
- Realised profit/loss calculated from matched Alpaca BUY and SELL orders
- Win rate and closed-trade metrics connected to broker data
- Portfolio exposure and invested capital connected to Alpaca
- Broker-backed equity curve connected to Alpaca portfolio history
- Local paper-account reset disabled in Alpaca mode
- Automatic Alpaca execution remains safety-locked

Current execution environment:
- Alpaca Paper Trading
- No real money is being used

## Current Development Checkpoint

Phase 3: Broker Integration and Execution Safety

Completed:
- Alpaca paper broker connection
- Broker account synchronisation
- Broker-backed portfolio metrics
- Broker order and position reconciliation
- Duplicate-order protection
- Existing-position protection
- Broker execution gate
- Broker state health monitor
- Broker performance and matched-trade verification

Current Task:
- Execution Kill Switch
- AI Trading Readiness Gate

Next After Completion:
- Controlled automatic Alpaca paper-trading loop
- Telegram alerts and operational notifications





###-----------------------
# AI Trading Bot – Roadmap

## Phase 1 – Core Engine ✅
- Signal generation ✅
- Paper trading ✅
- Position tracking ✅
- Risk management basic ✅

## Phase 2 – Live Trading (CURRENT)
- [ ] Alpaca API integration
- [ ] Real paper trading via Alpaca
- [ ] Execution engine separation
- [ ] Logging system

## Phase 3 – Notifications
- [ ] Telegram alerts
- [ ] Trade notifications
- [ ] Error alerts

## Phase 4 – Multi-Market
- [ ] Crypto (Binance)
- [ ] Forex (OANDA)

## Phase 5 – SaaS Platform
- [ ] User accounts
- [ ] Subscription model
- [ ] Web dashboard