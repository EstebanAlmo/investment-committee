# Investment basics

Short, beginner-friendly definitions for the concepts behind the domain
model in `src/investment_committee/domain/`. Not investment advice.

## Stock

A share of ownership in a single company. If you own a stock, you own a
small piece of that company and its future profits (or losses). Modeled by
`Asset` with `asset_type=AssetType.STOCK`.

## ETF

An Exchange-Traded Fund: a basket of many stocks (or other assets) bundled
into one tradable instrument, so buying one share gives you exposure to
everything inside the basket. Modeled by `Asset` with
`asset_type=AssetType.ETF`.

## Ticker

The short code used to identify an asset on an exchange, e.g. `AAPL` for
Apple or `SPY` for the S&P 500 ETF. `Asset.ticker` is always normalized to
uppercase.

## Price bar

A summary of how an asset traded over one time period (a day, an hour, etc.):
the price when the period opened (`open`), the highest and lowest prices
reached (`high`, `low`), the price when it closed (`close`), and how many
shares changed hands (`volume`). Modeled by `PriceBar`. A sequence of price
bars for one asset, plus any metrics computed from them, is a
`MarketSnapshot`.

## Portfolio

The full set of assets someone holds at a given time, including how much of
each. Not modeled yet — this project only simulates hypothetical positions
for now (see `docs/roadmap.md`, Phase 4), and does not track real holdings.

## Return

How much an investment gained or lost, usually shown as a percentage of what
was originally invested. A stock bought at $100 and sold at $110 has a 10%
return.

## Risk

The chance that an investment's actual return turns out worse than expected,
including the chance of losing money. Different assets carry different
amounts of risk; a `Recommendation`'s `risk_notes` field is where the
committee records risk-related caveats for a given call.

## Volatility

How much an asset's price moves around over time. A highly volatile asset
swings up and down a lot; a low-volatility asset moves more slowly and
predictably. Volatility is one common way to measure risk, and will be
computed from stored `PriceBar` data once the Evaluation layer exists (see
`docs/roadmap.md`, Phase 5).
