# While Asia sleeps, Hyperliquid prices the next open

Study run 2026-11-05. Code: `research/reopen.py` (data, scoring) and `research/reopen_charts.py` (charts). Rerun
both to refresh every number here.

## The question

trade[XYZ]'s perps on Asian chip and AI stocks keep trading when Seoul, Tokyo, Shanghai and Hong Kong are shut. In
those hours there is no external price, and the oracle follows Hyperliquid's own order book
([oracle docs](https://docs.trade.xyz/perpetuals/mechanics/oracle-price.md), "internal pricing"). Most US news
about the AI supply chain lands in exactly those hours: Micron's and Nvidia's results, hyperscaler capex guidance,
export-control headlines. So: **when the home market reopens, how much of its opening gap had Hyperliquid already
priced?**

## Method

For every closure of each market:

- **ref:** Hyperliquid's price when external pricing stopped. Until that moment, the oracle tracks the home market.
- **pre:** Hyperliquid's price at a cut-off before the home market's own pre-open discovery becomes visible. That
  means before Nextrade's 08:00 pre-market and KRX's auction (Korea), Japannext's 08:20 open (Japan), the 09:15 call
  auction (Shanghai), and the 09:00 pre-opening session (Hong Kong). The move is Hyperliquid's alone.
- **open:** the home exchange's official opening price (Yahoo Finance), converted to USD at the hourly FX rate.

Hyperliquid's move is `m = ln(pre/ref)` and the gap is `g = ln(open/ref)`. **Share priced** is `1 − Σ(g−m)² / Σg²`:
the share of the gap's variance that Hyperliquid's overnight price removes, against the naive forecast of no move.
100% means Hyperliquid called every open exactly; 0% means it did no better than assuming nothing happened.

The baseline is a market priced on an external venue through the same hours: SK hynix's US ADS, Micron, Sandisk or
CoreWeave. It is scaled by its in-sample beta, which is generous to it. The question is whether Hyperliquid knows
more than the obvious US-listed stand-in.

## Results

| Market | Nights | Share priced | Direction right (gaps ≥1%) | Avg gap / avg miss | Stand-in, same nights | Hyperliquid, same nights | Both |
|---|---|---|---|---|---|---|---|
| SK hynix (KRX) | 137 | **72%** | 88% (of 112) | 3.4% / 1.6% | 68% SK hynix ADS (78 nights) | 79% | 80% |
| Samsung (KRX) | 137 | **72%** | 88% (of 99) | 2.7% / 1.5% | 57% Micron | 72% | 73% |
| CXMT (Shanghai STAR) | 66 | **54%** | 78% (of 49) | 2.3% / 1.6% | 49% Micron | 54% | 58% |
| Kioxia (Tokyo) | 25 | 69% | 78% (of 18) | 2.8% / 1.6% | 79% Sandisk | 69% | 79% |
| MiniMax (HKEX) | 95 | **53%** | 80% (of 74) | 2.5% / 1.7% | none | — | — |
| Zhipu (HKEX) | 94 | **43%** | 88% (of 67) | 2.7% / 1.9% | none | — | — |
| Neocloud ETF (NCLD) | 62 | 60% | 89% (of 46) | 2.5% / 1.6% | 77% CoreWeave | 60% | 77% |
| US names, weekends (9 names pooled) | 229 | 18% | 65% (of 158) | 2.3% / 2.2% | — | — | — |

Closures run from 2026-04-14 (Korea), 2026-06-22 (Hong Kong), 2026-07-28 (CXMT, from its listing), 2026-08-07
(NCLD) and 2026-09-30 (Kioxia) to 2026-11-05.

**Robustness.** Measuring the gap from the official close instead of Hyperliquid's price (so no Hyperliquid price
appears in both the move and the gap) gives similar shares: SK hynix 71%, Samsung 72%, CXMT 63%, Kioxia 83%, MiniMax
42%, Zhipu 36%. Hyperliquid's price at the official close matches the FX-converted close to within about 1% at
the median for every market. One Kioxia night was dropped: Hyperliquid's price was 3× the Tokyo close, a contract
change rather than a gap.

## What it says

1. **For Korean memory, Hyperliquid is where the overnight price is set.** While KRX is shut, Hyperliquid prices
   about 72% of SK hynix's and Samsung's opening gap and calls the direction of 88% of gaps over 1%. It beats the
   US-listed stand-ins over the same hours: 79% against 68% for SK hynix's own ADS, and 72% against 57% for Micron
   on Samsung. The biggest night in the sample, 31 July 2026, had SK hynix up 14% on Hyperliquid overnight and
   opening +22%.
2. **For CXMT and the Chinese AI labs, Hyperliquid is the only overnight price there is.** CXMT trades only onshore,
   and MiniMax and Zhipu have no US listing. Hyperliquid priced 54% of CXMT's opening gap and 43–53% of the labs',
   and called the direction 78–88% of the time.
3. **Where a liquid US proxy exists, Hyperliquid adds little.** Sandisk explains Kioxia's gap better than
   Hyperliquid's Kioxia market does, and CoreWeave explains the neocloud ETF's. Hyperliquid's edge is concentrated
   where nothing else trades.
4. **Weekends are noise.** For US names, Hyperliquid's Friday-night-to-Sunday-night move explains 18% of Monday's
   opening gap. The "first price on weekend news" story does not hold in general.
5. **It is a forecast, not a free trade.** After a big overnight move (2% or more), the gap usually went less far
   than Hyperliquid had in the Asian markets: 30–45% of the time it went further, and on average it fell 0.4–1.2 points short. The very
   largest nights were the exception, where the gap outran Hyperliquid. Trading the perp toward the open is not an
   edge on these numbers.

## Limits

- Six to seven months for Korea; three to four for CXMT, the labs and NCLD; five weeks for Kioxia. One volatile
  regime for memory. The numbers will move as the sample grows.
- Hourly candles before mid-September, 15-minute after. Prices are each candle's last trade, so a thin hour reads
  stale.
- The cut-offs are deliberately early: 08:00 KST against a 09:00 open, and 04:00 ET against 09:30 for NCLD. That
  costs Hyperliquid the information that arrives in between, so the shares are conservative.
- "Share priced" scores against assuming no move. It is a measure of information, not of trading profit.

## Draft post

> While Seoul sleeps, Hyperliquid sets the price of Korean memory.
>
> Over 137 nights since April, Hyperliquid's overnight move priced 72% of SK hynix's and Samsung's opening gap on
> KRX, and called the direction of 88% of gaps over 1%. That beats SK hynix's own US ADS over the same hours (79% vs
> 68%).
>
> For CXMT, which only trades onshore, and the Chinese labs MiniMax and Zhipu, it is the only overnight price at
> all: 43–54% of the gap, direction right ~80% of the time.
>
> Where a liquid US proxy exists (Kioxia vs Sandisk), it adds little. Weekends are noise.
>
> Method and data: [link]

Charts: `share.png` (all markets against their stand-ins) and `seoul.png` (every Korean night).

## Next

The live version is a card each evening, US time: Hyperliquid's move in each Asian chip and lab name since its
home market closed, against its US stand-in, and how many hours remain until the open. "SK hynix +4.1% on
Hyperliquid since KRX closed on Micron's guide; Seoul opens in 9h."
