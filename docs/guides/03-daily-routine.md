# Guide 3: Your daily routine

[← Back to README](../../README.md) · Previous: [Connect your broker](02-connect-broker.md) · Next: [Using each screen →](04-using-the-screens.md)

A simple, repeatable routine. BlockRadar finds setups; **you** decide whether to trade them.

> This is a way to use the tool, not trading advice. Start with **paper trading** for at least 2–4 weeks before risking real money.

---

## Before the market opens (9:00–9:15)

1. **Start BlockRadar:** double-click `start.bat` (or your desktop shortcut).
2. **Reconnect your broker:** click the **Connect** link in the yellow bar (logins expire overnight).
3. Wait for the progress bar to finish loading.
4. **Turn on alerts** if you haven't: **Settings → Alerts** → tick *Desktop pop-up* and *Play a sound* → **Send test alert** to check.

## First 15 minutes (9:15–9:30): watch, don't trade

The opening is noisy, and the rankings need a few 5-minute candles to settle. Use this time to:
- Look at the **NIFTY** and **BANKNIFTY** cards at the top of the Board: bullish, bearish or choppy?
- Open **X-Ray**: are most stocks bullish or bearish? Which sectors lead or lag?

## Finding a setup (after 9:30)

1. **Go with the market.** If X-Ray shows mostly bearish stocks, focus on the bearish list, and vice versa.
2. **Shortlist 2–3 stocks** from the Board. Look for:
   - **Strength 60 or higher**
   - an **OI tag that agrees**: *Long buildup* for longs, *Short buildup* for shorts
   - **15m ✓** and **1h ✓**: the bigger timeframes agree
   - bonus: an **HTF zone** tag (the zone lines up with a higher-timeframe order block)
3. **Click a stock** to open its chart.

## Waiting for the entry

- The **gold box** on the chart is the **trade zone**. Wait for price to come back into it.
- Don't chase: if the stock has already run far away, the card says *"Reward is smaller than the risk from here"*. Skip it or wait.
- Let alerts do the watching: you'll get a pop-up when a strong stock **enters its zone**.

## Planning the trade (before you enter)

The zone card on the right gives you a plan:
- **Entry:** the edge of the zone
- **Stop:** just beyond the far side of the zone
- **Target:** the next liquidity level, or support/resistance
- **Reward : risk:** skip it if it's below **1.5**

**Position size:** decide the most you'll lose on one trade (many traders use 1% of their capital). Then:

```
Quantity = Maximum loss ÷ (Entry − Stop)
```
For options, the **Options helper** shows **Risk/lot**. Make sure one lot's risk fits your limit.

## Practise with paper trading

- On the chart, click **Buy/Sell now** or **Limit at zone** in the *Paper trade* card.
- Or, in the **Options helper**, click **Paper buy** next to a strike.
- BlockRadar manages the stop, target and the 15:20 square-off automatically.

## After the close (after 15:30)

1. Open **Paper** and look at today's results.
2. Log any **real** trades in the **Journal**, with the setup type and a note on why you took it.
3. Once a week, check **Journal → By setup** and **Paper → Equity curve**. Keep the setups that make money and drop the ones that don't.
4. Close the black window to stop BlockRadar.

---

## Quick checklist

- [ ] Broker connected
- [ ] Market direction checked (X-Ray, NIFTY)
- [ ] Strength 60+, OI agrees, 15m ✓ 1h ✓
- [ ] Price is **in** the zone, not chased
- [ ] Reward : risk ≥ 1.5
- [ ] Size fits my maximum loss
- [ ] Logged in the Journal
