# OrderTrade AI — Launch Content

All copy below is drawn directly from the live product (ordertradeai.com) and its current
early-access scope: bring-your-own-broker (Alpaca paper / Binance testnet / eToro demo),
paper/demo trading only, $39/month with a 7-day free trial. Nothing here claims live trading
or results that the product doesn't actually deliver yet — keep it that way in any edits.

---

## 1. Launch announcement (blog post / newsletter / email to your network)

**Title: Introducing OrderTrade AI — AI trade signals for the broker account you already have**

I've spent the last several months building OrderTrade AI, and it's now live at
[ordertradeai.com](https://ordertradeai.com).

Here's the problem it's solving: most retail trading tools either want you to hand over your
money to a black box, or they dump raw signals on you with no risk management attached. I
wanted something in between — an AI that actually scores opportunities across multiple asset
classes, shows its reasoning, and then gets out of the way so you place the trade yourself, on
your own account.

**How it works:**

1. **Connect your own broker.** Alpaca, Binance, or eToro — using your own API keys, encrypted
   at rest. OrderTrade AI never pools or holds anyone's funds; every trade executes directly on
   your account.
2. **The AI scores opportunities.** It reads live market data across whichever asset classes
   you enable (US stocks, crypto, forex, commodities), and produces a graded, ranked list —
   sized against your actual connected-account balance, not a hypothetical.
3. **You review, then execute.** Preview shows exactly what would be bought and why —
   confidence score, risk/reward, grade. Nothing is placed until you check the box and click
   Execute yourself.

**What's under the hood once a position is open:** stop-loss and take-profit set from real
price structure (not a fixed percentage guess), a trailing-profit lock with partial
profit-taking, a hard time-based exit, a portfolio-level exposure cap, and a single kill switch
that stops all automated activity across every connected broker immediately.

**Where it stands today:** early access, paper and demo trading only, on purpose — there's no
control anywhere in the product to switch on live trading yet. That's deliberate. I'd rather you
see exactly how the signal engine and risk management behave with real market data and your real
account settings before there's ever real money involved.

7-day free trial, $39/month after, cancel anytime. Would genuinely appreciate anyone willing to
connect a broker and kick the tires — feedback shapes what gets built next.

[Start your free trial →](https://ordertradeai.com)

---

## 2. X / Twitter thread

**Tweet 1 (hook):**
Most trading bots want your money. Mine doesn't want anything except to show you what it would
buy — you place the trade yourself, on your own broker account.

Built OrderTrade AI over the past few months. It's live. 🧵

**Tweet 2:**
The setup: connect your own Alpaca (stocks), Binance (crypto), or eToro (forex/commodities)
account with your own API keys. Nothing pooled, nothing custodied — every trade hits your
account directly.

**Tweet 3:**
The AI scores opportunities across whatever asset classes you enable, ranks them, and shows you
confidence + risk/reward + a letter grade. Sized against your real account balance, not a demo
number.

**Tweet 4:**
Preview shows exactly what it would buy and why. Nothing executes until you tick a box and hit
Execute yourself. No black box, no auto-pilot you can't see inside.

**Tweet 5:**
Risk management runs the whole time a position is open, not just at entry: real stop-loss/
take-profit from price structure, a trailing-profit lock, a hard time exit, a portfolio exposure
cap, and one kill switch that stops everything across every broker instantly.

**Tweet 6:**
Right now it's paper/demo trading only, on purpose — early access, and I want the signal engine
proven out before real money is anywhere near it. 7-day free trial, $39/mo after.

**Tweet 7 (CTA):**
If you trade stocks, crypto, forex, or commodities and want to see what an AI would do with your
actual account before risking anything — this is for you.

https://ordertradeai.com

---

## 3. LinkedIn post

Over the past several months I've been building OrderTrade AI, and it's now live.

The idea started from a frustration: most AI trading tools ask you to either hand over custody
of your funds, or they hand you raw signals with no risk framework attached. I wanted neither —
an AI that scores real opportunities across stocks, crypto, forex, and commodities, shows its
full reasoning, and then steps back so *you* place the trade on *your own* broker account.

A few things I focused on getting right:

→ **Bring-your-own-broker.** Connect Alpaca, Binance, or eToro with your own API keys. Encrypted
at rest, decrypted only to act on your account. Funds never leave your broker.

→ **Transparent signals.** Every recommendation comes with a confidence score, a risk/reward
ratio, and a grade — sized against your real connected-account balance.

→ **Manual confirmation, always.** The AI never trades without you. Preview shows exactly what
it would do; you decide whether to execute.

→ **Risk management that doesn't stop at entry.** Stop-loss/take-profit from real price
structure, a trailing-profit lock, a hard time-based exit, a portfolio-level exposure cap, and a
single kill switch across every connected broker.

It's currently in early access — paper and demo trading only, deliberately, while the signal
engine proves itself out with real market data before any real capital is involved.

If you or someone in your network trades and is curious what an AI-scored signal engine looks
like with real risk controls attached (not just backtested marketing screenshots), I'd love for
you to try it — and I'd value the feedback even more.

https://ordertradeai.com

#fintech #AI #trading #startup #buildinpublic

---

## 4. Reddit posts

**Read this first:** Reddit trading/finance subreddits (r/algotrading especially) are strict
about self-promotion and their exact rules shift over time — I can't fetch Reddit's live rules
page from here to confirm the current wording, so check the sidebar/rules of each subreddit
yourself immediately before posting. General norms that hold almost everywhere: disclose
up front that you're the builder, don't post the identical text to multiple subreddits back to
back (space them out, tailor each one), and lead with substance over sales pitch — these two
drafts already lean that direction, but read the room in each community first. Some subreddits
(r/algotrading in particular) restrict self-promotion to a specific weekly thread rather than a
standalone post — check for that before posting standalone.

### r/algotrading (technical audience — lead with architecture, not sales)

**Title:** Built a bring-your-own-broker signal engine across stocks/crypto/forex/commodities —
looking for feedback from people who actually build this stuff

Been building this for a few months, wanted to get it in front of people who'd actually poke
holes in it rather than just try to sell it.

The core idea: instead of pooling funds or running fully automated execution, it connects to
your own broker (Alpaca paper, Binance testnet, eToro demo right now) with your own API keys,
scores opportunities across whatever asset classes you enable, and shows a full preview —
confidence, risk/reward, grade — before you manually confirm execution.

Risk management runs continuously once a position is open: stop-loss/take-profit derived from
actual price structure rather than a fixed %, a trailing-profit lock with partial profit-taking,
a hard time-based exit, a portfolio-level exposure cap checked before every trade, and a kill
switch that halts everything across every connected broker at once.

It's paper/demo-only right now, deliberately — early access, and I'd rather have the risk
management and signal quality proven out before real capital touches it.

Genuinely interested in critique on the approach, not just "does it work" — sizing logic,
exposure caps, anything that looks naive to people who've built this longer than I have.
ordertradeai.com if anyone wants to poke at it directly.

### r/SideProject or r/EntrepreneurRideAlong (builder-story audience)

**Title:** Launched my AI trading signal tool after months of solo building — bring-your-own-
broker, not another "give us your money" bot

Wanted to share something I've been heads-down on: OrderTrade AI, live now at
ordertradeai.com.

The short version: it's an AI that scores trade signals across stocks, crypto, forex, and
commodities, but instead of custodying anyone's money, it connects to your own Alpaca/Binance/
eToro account with your own API keys and only ever executes what you manually confirm.

Biggest design decision was making the risk management as much a feature as the signals
themselves — stop-loss/take-profit from real price structure, trailing-profit locks, exposure
caps, a kill switch — because "AI picks stocks" is easy, "AI picks stocks and doesn't blow up
your account" is the actual hard part.

It's early access and deliberately paper/demo-trading only for now. 7-day free trial if anyone
wants to try connecting a broker and see what it surfaces. Would love feedback, especially on
anything that feels confusing or untrustworthy from a first-time-user perspective — that's the
stuff I can't see myself anymore after staring at it for months.
