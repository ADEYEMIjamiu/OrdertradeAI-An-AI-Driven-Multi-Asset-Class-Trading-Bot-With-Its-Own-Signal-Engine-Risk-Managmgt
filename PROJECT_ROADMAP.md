# OrderTrade AI — Project Roadmap


## Project Goal
Build a professional AI trading platform that scans multiple asset classes, generates AI trade signals, ranks trade quality, controls risk, sizes positions intelligently, routes orders to the correct broker, validates performance in paper/demo/testnet, and later supports live trading with real capital under strict, auditable safety controls.

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
- ✅ US stocks enabled (Alpaca)
- ✅ Crypto enabled (Binance testnet)
- ✅ Forex enabled (eToro Demo)
- ✅ Commodities enabled (eToro Demo)
- ✅ Asset class and broker columns on the watchlist
- 🔴 Data/signal layer still partly inside `app.py` (`prepare_data`, `get_ai_signal`, `get_multi_timeframe_signal`)

### AI Signal Engine
- ✅ Trained model (`models/trading_model.pkl`, `models/features.pkl`)
- ✅ AI confidence calculation
- ✅ BUY / HOLD / SELL signal generation
- ✅ Multi-timeframe trend adjustment
- 🔴 Still partly inside `app.py`

---

## Phase 2 — Trading Intelligence Engines
- ✅ Regime engine (`engines/regime_engine.py`) — market risk level, regime score, dashboard display
- ✅ Strategy engine (`engines/strategy_engine.py`) — classification and score
- ✅ Trade planner (`engines/trade_planner.py`) — ATR stop loss/take profit, risk/reward, trade grade, trade reason
- ✅ Scoring and approval (`engines/scoring_engine.py`, `engines/approval_engine.py`)
- ✅ Priority engine (`engines/priority_engine.py`)

---

## Phase 3 — Risk, Portfolio, and Position Management

### Risk Engine (`engines/risk_engine.py`)
- ✅ Max open positions, cash, and portfolio exposure checks
- ✅ Daily trade limit and ticker cooldown, read from the persisted order journal (not session state) across every asset class
- ✅ Dynamic buy confidence
- 🔴 A few legacy risk calculations still remain in `app.py`

### Portfolio Engine (`engines/portfolio_engine.py`)
- ✅ Asset allocation by asset class
- ✅ Portfolio preview, capped to available cash
- ✅ Exposure limits, corrected to read live broker positions instead of stale local state

### Position Engine (`engines/position_engine.py`)
- ✅ Stop-loss / take-profit exit checks
- ✅ Trailing stop for Alpaca stocks (stop-loss/take-profit/trailing exits now journaled through the order book, not silently bypassing it)
- ✅ Trailing stop for eToro forex/commodities (broker-side, converted right after fill; success and failure both logged)
- ⬜ Break-even stop movement
- ⬜ Partial profit taking
- ⬜ Time-based exits

### Position Sizing Engine (`engines/position_sizing_engine.py`)
- ✅ Confidence / strategy / regime / exposure multipliers
- ✅ Used consistently across paper and live execution paths

---

## Phase 4 — Execution Layer

### Order Management System (`engines/order_manager.py`)
- ✅ Order object model with full lifecycle: pending, submitted, filled, rejected, cancelled, failed
- ✅ Persistent SQLite journal (`trade_journal.db`), the single cross-broker source of truth
- ✅ Every execution path (Alpaca, Binance, eToro, and risk-management exits) writes to it
- ✅ Fill confirmation instead of optimistic logging — orders are only marked FILLED once the broker confirms it
- ✅ Reconciliation for unconfirmed Alpaca and eToro orders on later page loads
- ⬜ Automated retry / resubmission of failed orders
- ⬜ Order expiration
- ⬜ Slippage and commission tracking (not meaningful yet in paper/demo/testnet)

### Execution Engine
- ✅ Crypto, stock, and forex/commodities execution all live and working
- 🔴 Execution functions still live inside `app.py` (`execute_alpaca_trades`, `execute_binance_trades`, `execute_etoro_trades`) rather than a dedicated `engines/execution_engine.py`

### Broker Router
- ✅ Functionally complete — Alpaca for US stocks, Binance for crypto, eToro Demo for forex and commodities
- ✅ Broker health monitoring for all three (`check_broker_connection`, `get_broker_state_health`, Binance/eToro equivalents)
- 🔴 Routing logic is inline (`if/elif` on asset class) rather than a dedicated router engine

---

## Phase 5 — Broker Integrations

### Alpaca (US Stocks)
- ✅ `broker.py` — account info, buy/sell, positions, order history
- ✅ Real Alpaca paper trading (not simulated locally)
- ✅ Market-hours awareness (rotation and other flows gate on it)
- 🟨 Auto-trading intentionally locked (`AUTO_LIVE_TRADING_LOCKED`) — manual approval only, pending validation

### Binance (Crypto)
- ✅ `binance_broker.py` — Binance testnet client via `ccxt`
- ✅ Real testnet order execution and wallet-based position sync
- ✅ Independent position cap, cash check, and pyramiding policy
- ✅ Auto-trading enabled for crypto only, by deliberate decision

### eToro Demo (Forex and Commodities)
- ✅ `etoro_broker.py` — leveraged CFD execution with stop-loss/take-profit
- ✅ Broker-side trailing stop conversion, now with success/failure logging
- ✅ Independent position caps for forex and commodities
- ✅ Order reconciliation for unconfirmed buys
- 🟨 Manual approval only, same as stocks — no auto-trading lock to bypass

_(The original OANDA/Interactive Brokers plan was superseded by eToro Demo, which covers both forex and commodities through one connector.)_

---

## Phase 6 — Journal and Analytics

### Trade Journal
- ✅ Full lifecycle integration across every execution path, including risk-management exits
- ✅ Logs exit price, realised P/L, strategy, trade score, asset class, and broker
- ⬜ Slippage and commission tracking (deferred — not meaningful pre-live-capital)

### Performance Analytics
- ✅ Win rate, profit factor, expectancy, max drawdown
- ✅ Per-asset-class breakdown (Performance Digest)
- ✅ Real-Money Readiness Scorecard — per-asset-class trades closed / win rate / profit factor against go-live thresholds
- ⬜ Sharpe / Sortino ratio
- ⬜ Monthly returns
- ⬜ Per-strategy performance (currently tracked per asset class only)

---

## Phase 7 — Portfolio Intelligence
- ⬜ Correlation engine
- ⬜ Sector/concentration risk engine
- ⬜ Portfolio optimizer and rebalancing engine
- ⬜ Capital allocation AI / risk budgeting

## Phase 8 — AI Learning
- ⬜ Strategy learning, confidence calibration, adaptive thresholds/sizing
- ⬜ Auto-retraining pipeline
- ⬜ Reinforcement learning research mode

## Phase 9 — Backtesting and Validation
- ⬜ Backtesting engine, walk-forward testing, Monte Carlo simulation
- ⬜ Out-of-sample validation
- 🟨 Live paper/demo/testnet validation is the current substitute (see Readiness Scorecard, Phase 6)

---

## Phase 10 — Production and Deployment
- ✅ `.gitignore`, secrets removed from the repo
- ✅ VPS deployment (DigitalOcean, Amsterdam region), systemd service
- ✅ SSH key-only login hardening
- ✅ HTTPS and dashboard password gate
- ✅ Crash recovery and uptime alerting
- ✅ Automated backups of trade history and bot state
- ✅ Emergency stop / kill switch
- ✅ Telegram alerts for fills, digests, and stop/trailing-stop events
- ✅ Real-Money Readiness Scorecard as the live-trading checklist
- ⬜ Docker (deployed directly via venv + systemd instead; not currently needed for a single-instance VPS)

---

## Live Validation Hardening (August 2026)
A concentrated audit pass across every broker connection, prompted by an Alpaca outage that exposed how one broker's issues could silently affect the rest of the system. Found and fixed:
- Rotation feature: market-hours gate, post-close verification before opening the replacement position, and a corrected success message that no longer claims completion when the buy leg was skipped
- Order-fill confirmation for Alpaca and eToro (replacing optimistic "filled" logging with broker-confirmed fills)
- Broker reconciliation for orders left unconfirmed by timeouts or interrupted sessions
- Stock risk-management exits (stop-loss/take-profit/trailing-stop) now journaled, closing a gap where they bypassed the order book entirely
- Confirm Rotation button's autorefresh-interruption guard, and proper recording in the execution log
- eToro trailing-stop success logging, closing a blind spot where only failures were ever recorded
- Daily trade limit and cooldown now read from the persisted journal across all four asset classes, not just crypto

**Real-Money Readiness validation window:** started 2026-08-07, running 30 days / minimum 20 trades per asset class before any live-capital decision is considered.

---

## Immediate Next Tasks
1. 🟨 Split the daily trade limit into per-asset-class caps instead of one shared portfolio-wide limit (proposed, not yet built)
2. 🔴 Move execution functions out of `app.py` into `engines/execution_engine.py`
3. 🔴 Move the data/AI signal layer out of `app.py` into dedicated engine files
4. 🟨 Continue the 30-day real-money validation window through early September
5. 🔵 Per-asset-class trading on/off toggles — under consideration, not committed
6. 🔵 Codebase comment cleanup pass for readability/presentation

---

## Phase 11 (Future) — SaaS Commercialisation and Multi-User Platform

### Objective
Transform the validated personal trading system into a secure, subscription-based platform.

### Planned Features
Product branding, secure registration/auth, free trial, subscription billing (Stripe), multi-user portfolio isolation, per-user broker integration with encrypted credential storage, per-user strategy/risk configuration, notification centre, admin dashboard, usage/audit logging, cloud deployment with background workers, staging/production environments, security and penetration testing, legal/regulatory review.

### Commercialisation Gate
Not to be offered for paid public use until:
- Paper/demo/testnet performance is fully validated (Readiness Scorecard passed for all asset classes)
- Broker reconciliation is proven reliable under real conditions
- Risk controls and emergency shutdown are tested
- User data and credentials can be securely isolated per account
- Terms, privacy policy, and risk disclosures are complete
- The applicable regulatory position has been professionally reviewed

**Status:** PLANNED — begins only after validation, single-user hardening, and production readiness are complete.
