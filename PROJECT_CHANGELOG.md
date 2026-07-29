# AI Trading Bot — Project Changelog

_Last audited: 2026-07-10_

This changelog reconstructs the current project baseline from the uploaded codebase.

---

## 2026-07-10 — Baseline Audit

### Project State Confirmed
- Uploaded and audited project ZIP: `AI-Trading-Bot-Upload.zip`.
- Confirmed modular engine architecture exists.
- Confirmed Streamlit app is still primary orchestrator.
- Confirmed model artifacts exist under `models/`.
- Confirmed trade journal database exists.

### Existing Core Files
- `app.py`
- `config.py`
- `broker.py`
- `trade_journal.py`
- `train_model.py`
- `requirements.txt`

### Existing Engines
- `engines/regime_engine.py`
- `engines/strategy_engine.py`
- `engines/trade_planner.py`
- `engines/scoring_engine.py`
- `engines/approval_engine.py`
- `engines/priority_engine.py`
- `engines/portfolio_engine.py`
- `engines/position_sizing_engine.py`
- `engines/risk_engine.py`
- `engines/execution_engine.py`
- `engines/position_engine.py`

### Existing Data Module
- `data/asset_universe.py`

### Existing Broker Layer
- `broker.py` currently handles Alpaca Paper Trading.
- `brokers/` folder exists but is not yet implemented.

### Existing Trading Capabilities
- AI prediction with stored model.
- Multi-timeframe signal adjustment.
- Strategy classification.
- Strategy scoring.
- Trade planning with ATR stop loss/take profit.
- AI trade scoring.
- Approval engine.
- Priority engine.
- Portfolio preview and portfolio exposure filtering.
- Dynamic position sizing.
- Alpaca paper trading execution path.
- Crypto scanning enabled but blocked from execution.

### Important Safety Finding
- `.env` was included in the uploaded ZIP. This file may contain API keys.
- Action required: add `.env` to `.gitignore`, remove it from future uploads, and consider regenerating Alpaca API keys if they were exposed.

---

## Completed Milestones Before Baseline

### Core Platform
- Built Streamlit dashboard.
- Added live market data through `yfinance`.
- Trained and connected AI model.
- Added AI confidence and BUY/HOLD/SELL signal output.

### Broker Integration
- Connected Alpaca Paper Trading.
- Added account read, buy, sell, open positions, and order history functions.
- Added live/paper safety controls.

### Risk and Trading Intelligence
- Added dynamic buy confidence.
- Added multi-timeframe signal confirmation.
- Added market risk/regime logic.
- Added stop loss, take profit, and trailing profit logic.
- Added trade approval and scoring logic.
- Added trade priority.

### Architecture Improvements
- Created `engines/` folder.
- Extracted regime, risk, trade planner, approval, scoring, strategy, portfolio, execution, priority, position, and position sizing logic into engine files.

### Multi-Asset Foundation
- Created `data/asset_universe.py`.
- Added US stocks and crypto universe.
- Enabled US stocks and crypto scanning.
- Blocked crypto from execution until broker engine is connected.

---

## Current In-Progress Work

### Institutional Execution Layer
- Order Management System not started.
- Broker Router not started.
- Live broker synchronization needs improvement.
- Execution functions still partially inside `app.py`.

### Portfolio Intelligence
- Portfolio exposure control exists.
- Correlation, sector exposure, and rebalancing not started.

### Analytics
- Trade journal exists.
- Full performance analytics engine not started.

---

## Next Planned Change

### Recommended Next Task
Create `.gitignore` and remove secret/local/cache files from future uploads:
- `.env`
- `__pycache__/`
- `.DS_Store`
- `*.pyc`
- local database files if not intentionally shared

### Next Engineering Task After Cleanup
Build Order Management System v1:
- order object
- order status states
- basic execution logger
- submitted/filled/failed status tracking

