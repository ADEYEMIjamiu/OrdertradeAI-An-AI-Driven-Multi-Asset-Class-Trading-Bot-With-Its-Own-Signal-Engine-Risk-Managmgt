# ============================================================
# EXAMPLE CONFIG -- copy this file to config.py and adjust the
# values below to your own tuned strategy parameters.
#
# config.py is gitignored on purpose: this project's actual
# stop-loss/take-profit levels, confidence thresholds, and trade
# grading bars are the tuned output of real iteration and belong
# to whoever is running the bot, not the public repo. Everything
# else -- the engines, the broker integrations, the risk
# architecture -- is unaffected by this and lives in the code as
# normal. The numbers below are illustrative placeholders, not
# the values actually used in production.
# ============================================================

from datetime import datetime, timezone

INITIAL_CASH = 100000

# Cutoff so performance metrics only count fills from a clean
# validation run, not old dev/testing trades sitting in broker
# history. Set this to whenever your own validation period starts.
ALPACA_VALIDATION_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Same idea as ALPACA_VALIDATION_START, but for the local trade
# journal (crypto performance digest). Naive (no tzinfo) to match
# how the journal stores timestamps.
CRYPTO_VALIDATION_START = datetime(2026, 1, 1, 0, 0, 0)

MIN_TRADE_AMOUNT = 100
MAX_TRADE_AMOUNT = 1000
RISK_PER_TRADE = 0.01

# FIX 2026-08-25: MAX_POSITION_SIZE is now the primary governor of real
# trade sizing (engines/risk_engine.py's calculate_trade_amount()), not
# just a secondary check -- every real trade is sized as a fraction of
# (account_balance * MAX_POSITION_SIZE), never more than this share of
# whatever the account actually holds. This replaced sizing off a fixed
# MIN_TRADE_AMOUNT-MAX_TRADE_AMOUNT dollar band, which was tuned around
# one account's size and didn't scale for accounts of different sizes
# (a $500 account and a $100,000 account got offered identical trades).
# MIN_TRADE_AMOUNT remains a hard floor -- an account too small to cover
# it gets 0 (skipped), never a forced trade below the floor.
MAX_POSITION_SIZE = 0.20

BUY_CONFIDENCE = 0.35
# Keep this LOWER than BUY_CONFIDENCE, not equal to it -- equal thresholds
# collapse the HOLD zone to nothing (BUY on confidence >= BUY_CONFIDENCE,
# SELL on confidence <= SELL_CONFIDENCE, so with no gap between them every
# value is forced into one or the other, never HOLD). See config.py's
# comment (2026-08-22 fix) for how this was discovered live in production.
SELL_CONFIDENCE = 0.25

# Fixed stop-loss/take-profit band. Widen or tighten based on how
# much intraday noise your tracked tickers actually have -- too
# tight and normal volatility force-closes positions instantly;
# too wide and losses run further than intended.
STOP_LOSS = 0.02      # placeholder -- tune to your own risk tolerance
TAKE_PROFIT = 0.04    # placeholder -- tune to your own risk tolerance

PAPER_TRADING = True
# Stocks route through the real Alpaca paper account instead of a
# local simulation when this is True.
LIVE_TRADING = True
# Keep locked (manual-confirm only) until you've watched real-broker
# fills behave as expected for a while.
AUTO_LIVE_TRADING_LOCKED = True

# Same pattern as LIVE_TRADING/AUTO_LIVE_TRADING_LOCKED, but for
# eToro (forex/commodities) instead of Alpaca.
ETORO_LIVE_TRADING = True
AUTO_ETORO_TRADING_LOCKED = True

# Position Management
ALLOW_PYRAMIDING = False
MAX_POSITIONS = 5

# Whether crypto BUY signals are allowed to add to an already-held
# position, or skip once a position exists. Off by default -- with
# this on and no auto-exit, a bot can keep buying the same coin on
# every signal with nothing ever forcing a sell.
ALLOW_CRYPTO_PYRAMIDING = False

MAX_OPEN_POSITIONS = 5
MAX_PORTFOLIO_EXPOSURE = 0.50

# Crypto, forex, and commodities each get their own independent
# position budget, separate from stocks -- otherwise stocks filling
# their slots first can silently lock the other asset classes out
# of ever opening a position.
MAX_CRYPTO_POSITIONS = 8
MAX_FOREX_POSITIONS = 3
MAX_COMMODITIES_POSITIONS = 3

DAILY_LOSS_LIMIT = 0.03
DAILY_PROFIT_TARGET = 0.05

# Daily trade limit -- scoped independently per asset class (see
# engines/risk_engine.py), not shared across all of them.
MAX_TRADES_PER_DAY = 5

# Trailing-stop parameters -- how much unrealised profit triggers
# trailing to start, and how far price can pull back from its peak
# before the trailing stop fires.
TRAILING_PROFIT_START = 1.2   # placeholder -- tune to your own strategy
TRAILING_PROFIT_DROP = 0.6    # placeholder -- tune to your own strategy

# Position lifecycle management -- see engines/position_lifecycle_engine.py
BREAKEVEN_STOP_TRIGGER_PERCENT = 1.0
PARTIAL_PROFIT_TRIGGER_PERCENT = 2.5
PARTIAL_PROFIT_TAKE_FRACTION = 0.5
MAX_HOLD_DAYS = 5

# Hard time-based exit -- closes a position after this many days
# regardless of profit or loss, unlike MAX_HOLD_DAYS above (which only
# closes if flat-or-better). Never increases the maximum possible loss --
# the stop-loss still fires first if breached; this only forces
# resolution of positions stuck between the stop-loss floor and
# break-even that would otherwise sit indefinitely.
MAX_HOLD_DAYS_HARD = 7

TRADE_COOLDOWN_MINUTES = 60

# Crypto scalping engine overrides -- see engines/risk_engine.py's
# can_open_position() for how these apply (crypto only).
CRYPTO_MAX_TRADES_PER_DAY = 30
CRYPTO_TRADE_COOLDOWN_MINUTES = 5

MARKET_RISK_LOW = 0.5
MARKET_RISK_MEDIUM = 1.0
MARKET_RISK_HIGH = 2.0

AGGRESSIVE_RISK_MULTIPLIER = 1.25
NORMAL_RISK_MULTIPLIER = 1.0
DEFENSIVE_RISK_MULTIPLIER = 0.5
DANGER_RISK_MULTIPLIER = 0.25

# Trade Planning Engine
ATR_STOP_MULTIPLIER = 1.5           # placeholder
ATR_TAKE_PROFIT_MULTIPLIER = 2.5    # placeholder, kept as a fallback only, see trade_planner.py
TRADE_PLAN_LOOKBACK_DAYS = 20       # swing high/low window for take-profit targets
MIN_RISK_REWARD_RATIO = 1.0

# Trade Grade tiers (engines/trade_planner.py). Both confidence AND
# risk_reward must clear a tier's bar to earn it. Tune these to
# whatever grading strictness your own strategy calls for.
GRADE_A_PLUS_CONFIDENCE = 70   # placeholder
GRADE_A_PLUS_RISK_REWARD = 2.0 # placeholder
GRADE_A_CONFIDENCE = 55        # placeholder
GRADE_A_RISK_REWARD = 1.3      # placeholder
GRADE_B_CONFIDENCE = 40        # placeholder
GRADE_B_RISK_REWARD = 0.8      # placeholder
GRADE_C_CONFIDENCE = 25        # placeholder -- below this, WAIT / grade D

# Market Regime Engine
# FIX 2026-08-25: get_market_regime() (engines/regime_engine.py) can only
# ever produce a score of 0/25/50/75/100 (four independent +25 checks) --
# the old REGIME_DEFENSIVE_SCORE=35 required a score in [35,49], a value
# that can never occur, so "DEFENSIVE" was never actually reachable.
# Changed to 25 so it lines up with the real score domain: 100=STRONG
# BULL, 75=BULL, 50=NEUTRAL, 25=DEFENSIVE, 0=BEAR. Found via the same
# audit that caught the trend-direction scoring bug.
REGIME_STRONG_BULL_SCORE = 80
REGIME_BULL_SCORE = 65
REGIME_NEUTRAL_SCORE = 50
REGIME_DEFENSIVE_SCORE = 25

# Trade Approval Engine -- confidence is gated twice deliberately,
# at two different bars: GRADE_C_CONFIDENCE above (loose, first
# stage) and MIN_TRADE_CONFIDENCE below (strict, final approval).
MIN_TRADE_CONFIDENCE = 40  # placeholder
MIN_TRADE_SCORE = 15       # placeholder

# Smart Position Sizing
HIGH_SCORE_SIZE_MULTIPLIER = 1.5
NORMAL_SCORE_SIZE_MULTIPLIER = 1.0
LOW_SCORE_SIZE_MULTIPLIER = 0.5

# ============================================================
# AI EXECUTION SAFETY CONTROLS
# ============================================================

EXECUTION_KILL_SWITCH = False
MANUAL_ALPACA_EXECUTION_ENABLED = True
AUTOMATIC_ALPACA_EXECUTION_ENABLED = False
ALLOW_EMERGENCY_SELL_EXITS = True
REQUIRE_BROKER_CONNECTION = True
REQUIRE_HEALTHY_BROKER_STATE = True
REQUIRE_MARKET_OPEN_FOR_BUYS = False
BLOCK_BUYS_ON_BROKER_WARNING = True
BLOCK_ALL_ON_BROKER_CRITICAL = True

# Prevent real-money execution during development. Must remain True
# until the production-live phase is deliberately approved.
REQUIRE_ALPACA_PAPER_ENVIRONMENT = True
REQUIRE_ETORO_DEMO_ENVIRONMENT = True

# ============================================================
# REAL-MONEY READINESS SCORECARD (engines/readiness_engine.py)
# ============================================================
# Data-driven bar for deciding when a bot has earned a move from
# paper/demo/testnet to real capital, instead of a gut call.

READINESS_VALIDATION_START = datetime(2026, 1, 1)

READINESS_MIN_DAYS = 37
READINESS_MIN_TRADES = 20
READINESS_MIN_PROFIT_FACTOR = 1.3
READINESS_MAX_DRAWDOWN_PERCENT = 20

# Backward compatibility (DO NOT REMOVE)
STOP_LOSS_PERCENT = STOP_LOSS
TAKE_PROFIT_PERCENT = TAKE_PROFIT
