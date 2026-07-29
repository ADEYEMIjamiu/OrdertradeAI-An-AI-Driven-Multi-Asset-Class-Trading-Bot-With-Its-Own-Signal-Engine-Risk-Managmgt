INITIAL_CASH = 100000

MIN_TRADE_AMOUNT = 100
MAX_TRADE_AMOUNT = 1000
RISK_PER_TRADE = 0.01

MAX_POSITION_SIZE = 0.20

BUY_CONFIDENCE = 0.40
SELL_CONFIDENCE = 0.40

STOP_LOSS = 0.01      # 1%
TAKE_PROFIT = 0.01   # 1%

PAPER_TRADING = True
LIVE_TRADING = False
AUTO_LIVE_TRADING_LOCKED = True

# Position Management
ALLOW_PYRAMIDING = False
MAX_POSITIONS = 5

# Crypto trades 24/7 on Binance testnet and testnet accounts commonly come
# pre-seeded with nonzero balances of tracked coins. Unlike stocks, crypto
# BUY execution previously had a hardcoded "already holds -> skip forever"
# gate with no way to add to a position. This flag makes that configurable,
# matching how ALLOW_PYRAMIDING works for stocks.
ALLOW_CRYPTO_PYRAMIDING = True

MAX_OPEN_POSITIONS = 5
MAX_PORTFOLIO_EXPOSURE = 0.50

# Crypto positions are tracked on the Binance testnet wallet, completely
# separate from st.session_state.positions (stocks only). Previously
# stock and crypto trades shared the same MAX_POSITIONS/MAX_OPEN_POSITIONS
# check against the stock-only count, so stocks filling their 5 slots
# first silently locked crypto out of every run. This gives crypto its
# own budget -- one slot per tracked coin (BTC, ETH, SOL, BNB).
MAX_CRYPTO_POSITIONS = 4

DAILY_LOSS_LIMIT = 0.03
DAILY_PROFIT_TARGET = 0.05

MAX_TRADES_PER_DAY = 5

TRAILING_PROFIT_START = 1.5
TRAILING_PROFIT_DROP = 0.75

TRADE_COOLDOWN_MINUTES = 60

MARKET_RISK_LOW = 0.5
MARKET_RISK_MEDIUM = 1.0
MARKET_RISK_HIGH = 2.0

AGGRESSIVE_RISK_MULTIPLIER = 1.25
NORMAL_RISK_MULTIPLIER = 1.0
DEFENSIVE_RISK_MULTIPLIER = 0.5
DANGER_RISK_MULTIPLIER = 0.25

# Trade Planning Engine
ATR_STOP_MULTIPLIER = 1.5

# NOTE: previously take-profit was entry_price +/- (atr * ATR_TAKE_PROFIT_MULTIPLIER),
# scaled from the SAME atr value used for the stop. That made
# risk_reward = reward/risk collapse to the constant
# ATR_TAKE_PROFIT_MULTIPLIER / ATR_STOP_MULTIPLIER (= 3.0/1.5 = 2.0) for
# every single ticker and signal, always -- it wasn't measuring this
# trade's actual risk/reward, just re-deriving a config ratio. This
# silently made MIN_RISK_REWARD_RATIO below a no-op (2.0 always passes).
# Take-profit is now based on real recent price structure (see
# TRADE_PLAN_LOOKBACK_DAYS / trade_planner.py) so the ratio genuinely
# varies with how much room a ticker actually has left to run.
ATR_TAKE_PROFIT_MULTIPLIER = 3.0  # kept as a fallback only, see trade_planner.py
TRADE_PLAN_LOOKBACK_DAYS = 20     # swing high/low window for take-profit targets
MIN_RISK_REWARD_RATIO = 1.0

# Trade Grade tiers (engines/trade_planner.py). Both confidence AND
# risk_reward must clear a tier's bar to earn it -- a high-confidence
# signal with a weak reward-to-risk shouldn't grade as if it were
# excellent, and vice versa. engines/scoring_engine.py already has a
# point rubric expecting exactly these four grades (A+/A/B/C); before
# this, trade_planner only ever emitted "A" or "D", so A+ and B were
# unreachable and the grade component of AI Trade Score never actually
# differentiated trade quality.
GRADE_A_PLUS_CONFIDENCE = 75
GRADE_A_PLUS_RISK_REWARD = 2.5
GRADE_A_CONFIDENCE = 60
GRADE_A_RISK_REWARD = 1.5
GRADE_B_CONFIDENCE = 45
GRADE_B_RISK_REWARD = 1.0
GRADE_C_CONFIDENCE = 30  # below this, no real trade case -- WAIT / grade D
                          # (see MIN_TRADE_CONFIDENCE below -- this is the
                          # loose first-stage bar, not the final approval bar)

# Market Regime Engine
REGIME_STRONG_BULL_SCORE = 80
REGIME_BULL_SCORE = 65
REGIME_NEUTRAL_SCORE = 50
REGIME_DEFENSIVE_SCORE = 35

# Trade Approval Engine
#
# Confidence is gated TWICE, deliberately, at two different bars:
#   1. GRADE_C_CONFIDENCE (30, above) in trade_planner.py -- the loose bar.
#      Below it there's no real trade case at all, so the signal is forced
#      to WAIT and graded D before it's even considered further.
#   2. MIN_TRADE_CONFIDENCE (below) in approval_engine.py -- the strict,
#      final bar. A signal can clear the loose bar (30) and still get a
#      full trade plan + a C grade, but approval_engine will still reject
#      it here if confidence hasn't reached this higher bar. This used to
#      be a bare hardcoded "45" with no link to trade_planner's threshold,
#      making it unclear whether the gap between the two numbers was
#      intentional design or accidental drift. It's intentional: this is
#      a two-stage funnel (loose planning bar, then a stricter approval
#      bar), not a bug.
MIN_TRADE_CONFIDENCE = 45
MIN_TRADE_SCORE = 20

# Smart Position Sizing
HIGH_SCORE_SIZE_MULTIPLIER = 1.5
NORMAL_SCORE_SIZE_MULTIPLIER = 1.0
LOW_SCORE_SIZE_MULTIPLIER = 0.5

# ============================================================
# AI EXECUTION SAFETY CONTROLS
# ============================================================

# Master emergency switch.
# False = execution is allowed to continue through other checks.
# True = block all new AI trade entries immediately.
EXECUTION_KILL_SWITCH = False

# Allows manual execution through the "Execute Trades" button.
MANUAL_ALPACA_EXECUTION_ENABLED = True

# Allows automatic Alpaca paper-trading execution.
# Keep False until the automatic trading loop is fully validated.
AUTOMATIC_ALPACA_EXECUTION_ENABLED = False

# When True, SELL orders may still close existing positions
# even when the kill switch blocks new BUY entries.
ALLOW_EMERGENCY_SELL_EXITS = True

# Require the Alpaca broker connection to be healthy.
REQUIRE_BROKER_CONNECTION = True

# Require broker-state synchronisation to be healthy.
REQUIRE_HEALTHY_BROKER_STATE = True

# Require the US stock market to be open before stock BUY execution.
REQUIRE_MARKET_OPEN_FOR_BUYS = False

# Block all new BUY entries when the broker state is WARNING.
BLOCK_BUYS_ON_BROKER_WARNING = True

# Block every BUY and SELL when the broker state is CRITICAL.
BLOCK_ALL_ON_BROKER_CRITICAL = True

# Prevent real-money execution during the development phase.
# This must remain True until the production-live phase is approved.
REQUIRE_ALPACA_PAPER_ENVIRONMENT = True

# Backward compatibility (DO NOT REMOVE)
STOP_LOSS_PERCENT = STOP_LOSS
TAKE_PROFIT_PERCENT = TAKE_PROFIT