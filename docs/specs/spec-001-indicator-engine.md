# Spec 001 - Indicator Engine

## Objective

Build a deterministic engine that receives a MarketSnapshot and calculates a set of technical and risk indicators.

The engine must not fetch market data, call external services, use LLMs, or generate investment recommendations.

## Input

A valid MarketSnapshot containing daily PriceBar objects ordered chronologically.

Each PriceBar provides:

- timestamp
- open
- high
- low
- close
- volume

## Output

An IndicatorSnapshot associated with the same asset and market snapshot.

The output must include:

- latest_close
- sma_20
- sma_50
- sma_200
- return_1d
- return_30d
- return_90d
- return_ytd
- annualized_volatility
- maximum_drawdown
- rsi_14
- generated_at

Indicators that cannot be calculated because there is insufficient historical data must return None.

## Indicator definitions

### Simple Moving Average

SMA is the arithmetic mean of the closing prices over a defined number of observations.

Required periods:

- SMA 20
- SMA 50
- SMA 200

### Return

Return measures the percentage change between an earlier closing price and the latest closing price.

Required periods:

- 1 trading day
- 30 trading days
- 90 trading days
- year to date

Formula:

(current_close / previous_close) - 1

### Annualized volatility

Calculate daily returns from consecutive closing prices.

Calculate the sample standard deviation of those daily returns.

Annualize using:

daily_standard_deviation * square_root(252)

Use 252 as the assumed number of trading days per year.

### Maximum drawdown

Maximum drawdown measures the largest percentage decline from a historical peak to a subsequent trough.

For each closing price:

drawdown = (current_close / previous_peak) - 1

Return the most negative drawdown observed.

### RSI 14

Use a 14-period Relative Strength Index.

Separate positive and negative changes in consecutive closing prices.

Use the simple average of gains and losses for the first implementation.

Formula:

RS = average_gain / average_loss

RSI = 100 - (100 / (1 + RS))

Edge cases:

- If average loss is zero and average gain is greater than zero, RSI is 100.
- If average gain and average loss are both zero, RSI is 50.

## Architecture

Create:

src/investment_committee/indicators/
    __init__.py
    models.py
    engine.py

The IndicatorEngine receives a MarketSnapshot and returns an IndicatorSnapshot.

The engine may use Python standard-library mathematics and statistics.

Do not use pandas, numpy, TA-Lib, or external indicator libraries in this Epic.

## Validation

Raise a domain-specific IndicatorCalculationError when:

- the MarketSnapshot contains no PriceBars;
- PriceBars are not ordered chronologically;
- closing prices are invalid for the calculation.

Insufficient data for an individual indicator is not an exception. Return None for that indicator.

## Tests

Tests must cover:

- valid calculation with sufficient history;
- SMA 20, SMA 50, and SMA 200;
- 1-day, 30-day, and 90-day returns;
- YTD return;
- annualized volatility;
- maximum drawdown;
- RSI 14;
- insufficient history;
- flat prices;
- rising prices;
- unordered PriceBars;
- empty MarketSnapshot behavior;
- no network access.

## Out of scope

- Buy, Hold, or Sell recommendations
- Fundamental analysis
- News analysis
- LLMs
- TradingAgents
- Portfolio management
- Backtesting
- Persistence
- CLI changes
- Charts