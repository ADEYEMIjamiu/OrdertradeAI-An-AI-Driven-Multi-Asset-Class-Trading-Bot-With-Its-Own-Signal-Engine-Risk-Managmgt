"""
engines/asset_class_utils.py -- small, dependency-free asset-class helpers
shared across app.py's execution functions and engines/rotation_engine.py.

Split out during the #118 rotation-engine extraction (2026) purely to break
a circular import: rotation_engine.py needs filter_by_asset_class() and
_ETORO_ASSET_CLASS_TICKERS, but both used to live in app.py, and app.py is
what imports rotation_engine.py -- so rotation_engine.py importing app.py
back would be circular. Neither symbol has any Streamlit/session-state
dependency, so lifting them out to a leaf module app.py and rotation_engine.py
both import from is the same pattern already used for every other engines/
module in this project. No logic changed from the original app.py versions.
"""

# Which of this project's own FOREX/COMMODITIES/INDICES tickers (from
# data/asset_universe.py) belong to which asset class -- needed here to
# enforce MAX_FOREX_POSITIONS/MAX_COMMODITIES_POSITIONS/MAX_INDICES_
# POSITIONS independently per class (same reasoning as MAX_CRYPTO_
# POSITIONS in config.py: each class gets its own budget so one doesn't
# starve the other). Kept as an explicit list rather than reading
# ASSET_UNIVERSE directly so this doesn't silently start trying to route
# a newly-added ticker through eToro before its symbol mapping (see
# etoro_broker.resolve_project_ticker) has actually been verified live.
#
# INDICES added 2026-09-18 (task #390) -- INCLUDED HERE DESPITE this
# module's own "not yet verified live" caution above, because rotation_
# engine.py's own get_currently_held() call site already resolves the
# symbol via etoro_broker.resolve_project_ticker() regardless of whether
# a ticker is listed here; leaving it out would just silently break
# rotation-candidate comparisons for INDICES (never seeing an open
# position as "held"), not add any actual safety. The real "don't trade
# real money on an unverified symbol" gate lives in etoro_broker.py's/
# mt_broker.py's own ticker-override tables and their docstrings, not
# here.
_ETORO_ASSET_CLASS_TICKERS = {
    "FOREX": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
    "COMMODITIES": ["GC=F", "CL=F", "SI=F"],
    "INDICES": ["^GSPC", "^IXIC", "^DJI", "^FTSE", "^GDAXI", "^N225"],
}


def filter_by_asset_class(df, asset_class):
    """
    Safely filter a signals DataFrame by Asset Class.

    (DataFrame.get("Asset Class", default) does NOT safely broadcast a
    default value into a row-wise boolean comparison when the column is
    missing -- it returns the raw default scalar instead of a Series,
    which breaks df[...] indexing. This does it correctly.)
    """
    if df is None or df.empty:
        return df
    if "Asset Class" not in df.columns:
        # No asset class info at all -- treat as US_STOCKS by default,
        # since that's every row this app has historically dealt with.
        return df if asset_class == "US_STOCKS" else df.iloc[0:0]
    return df[df["Asset Class"] == asset_class].copy()
