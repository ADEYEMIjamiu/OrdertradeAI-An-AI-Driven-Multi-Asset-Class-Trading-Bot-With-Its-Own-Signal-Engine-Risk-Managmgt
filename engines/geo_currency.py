"""
IP-address -> country -> currency resolution for regional subscription
pricing (added 2026-09-15, per the user's explicit request: someone in
the UK should see/pay GBP 39, the Eurozone EUR 39, the US/most of the
rest of the world USD 39, Australia AUD 39, Singapore SGD 39, Canada
CAD 39 -- but countries without one of those currencies, notably most
of Asia (Malaysia, Indonesia, Thailand, Vietnam, India, Pakistan, etc.)
and all of South America, should always see plain USD rather than a
currency-converted figure).

DELIBERATE DESIGN CHOICES
--------------------------
1. Fixed amount per currency, not FX conversion. "39 pounds" and
   "39 euros" are literal -- the same round number in each currency,
   like most SaaS pricing pages -- not a live FX-converted equivalent
   of $39 that would drift with exchange rates. This is why
   engines/billing_engine.py needs one real Stripe Price object per
   supported currency (see STRIPE_PRICE_ID_<CCY> in .env.example)
   rather than Stripe's Adaptive Pricing / currency_options feature,
   which does live FX conversion and would fight this requirement.

2. A small explicit country->currency map, not a general geo/currency
   library. Only 5 non-USD currencies are in scope (GBP, EUR, AUD, SGD,
   CAD); everything else -- including regions the user explicitly
   called out (Malaysia, Indonesia, Thailand, Vietnam, India, Pakistan,
   all of South America) -- intentionally falls through to the
   FALLBACK_CURRENCY (USD) rather than trying to cover every currency
   in the world.

3. "Europe" is interpreted as the Eurozone (countries that actually use
   EUR), not the whole continent -- a Swedish or Swiss visitor doesn't
   use euros day to day, so defaulting them to USD (like most non-
   Eurozone countries here) is more honest than showing a currency
   they don't actually spend. The UK is handled separately as GBP.
   Worth revisiting if the platform later wants SEK/NOK/CHF/PLN prices
   of their own.

4. Fails safe. Any failure -- no IP, geolocation API down, timeout,
   unrecognized country code -- returns FALLBACK_CURRENCY (USD), never
   raises. A pricing page that silently shows the wrong (but valid)
   currency is a minor annoyance; one that crashes the billing gate
   would block a paying customer entirely.

5. No dependency on Streamlit. Like every other module in engines/,
   this stays plain Python so it's importable from a non-Streamlit
   context too -- saas_app.py is responsible for extracting the
   visitor's IP out of st.context/request headers and passing it in
   (see its _get_client_ip() / _get_display_currency() helpers).
"""

import requests

FALLBACK_CURRENCY = "USD"

# ISO 4217 currency amount to display/charge, in the currency's own
# minor-unit-free "39" form -- i.e. literally 39.00, not a converted
# equivalent. Kept here (not just in billing_engine.py) since the
# landing page / billing gate need the same number to display before
# any Stripe interaction happens.
PRICE_AMOUNT = 39

_CURRENCY_SYMBOLS = {
    "USD": "$",
    "GBP": "£",
    "EUR": "€",
    "AUD": "A$",
    "SGD": "S$",
    "CAD": "C$",
}

# Countries that actually use each of the 5 non-USD currencies this
# platform prices in. ISO 3166-1 alpha-2 codes, upper-case.
_GBP_COUNTRIES = {"GB"}

_EUR_COUNTRIES = {
    # Eurozone member states (countries where EUR is the official
    # currency) as of 2026 -- 20 members, including Croatia (joined
    # 2023). Non-Euro EU/European countries (Sweden, Poland, Denmark,
    # Switzerland, Norway, etc.) deliberately fall through to USD --
    # see module docstring point 3.
    "AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES",
}

_AUD_COUNTRIES = {"AU"}
_SGD_COUNTRIES = {"SG"}
_CAD_COUNTRIES = {"CA"}

# Explicitly documented, not just "everything else" -- these are the
# regions the user specifically named as USD-only despite not using
# dollars natively (kept here purely as a readable record; they'd fall
# through to FALLBACK_CURRENCY even without being listed, since they
# aren't in any of the sets above).
_EXPLICITLY_USD_ONLY_EXAMPLES = {
    "MY", "ID", "TH", "VN", "IN", "PK",  # named Asian countries
    "BR", "AR", "CL", "CO", "PE",  # South America
}

_COUNTRY_TO_CURRENCY = {}
for _code in _GBP_COUNTRIES:
    _COUNTRY_TO_CURRENCY[_code] = "GBP"
for _code in _EUR_COUNTRIES:
    _COUNTRY_TO_CURRENCY[_code] = "EUR"
for _code in _AUD_COUNTRIES:
    _COUNTRY_TO_CURRENCY[_code] = "AUD"
for _code in _SGD_COUNTRIES:
    _COUNTRY_TO_CURRENCY[_code] = "SGD"
for _code in _CAD_COUNTRIES:
    _COUNTRY_TO_CURRENCY[_code] = "CAD"

_GEOLOCATION_TIMEOUT_SECONDS = 3
_GEOLOCATION_API_URL = "https://ipwho.is/{ip}"


def currency_symbol(currency_code):
    return _CURRENCY_SYMBOLS.get(currency_code, "$")


def country_code_from_ip(ip_address):
    """
    Resolves a country code from an IP address via ipwho.is (free, no
    API key, HTTPS). Returns None on any failure -- bad IP, network
    error, timeout, rate limit, unexpected response shape -- rather
    than raising, so a geolocation hiccup never blocks the billing
    page. Deliberately a short timeout: this runs inline while
    rendering a page a paying customer is looking at, and USD (the
    fallback) is always a safe, valid answer.
    """
    if not ip_address or ip_address in ("127.0.0.1", "::1", "localhost"):
        return None
    try:
        response = requests.get(
            _GEOLOCATION_API_URL.format(ip=ip_address),
            timeout=_GEOLOCATION_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("success", True):
            return None
        code = data.get("country_code")
        return code.upper() if code else None
    except Exception:
        return None


def currency_for_country(country_code):
    """Pure lookup, no network -- always returns a valid currency code."""
    if not country_code:
        return FALLBACK_CURRENCY
    return _COUNTRY_TO_CURRENCY.get(country_code.upper(), FALLBACK_CURRENCY)


def currency_for_ip(ip_address):
    """Convenience wrapper: IP -> country -> currency, USD on any failure."""
    return currency_for_country(country_code_from_ip(ip_address))
