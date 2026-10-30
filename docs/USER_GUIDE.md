# Reading LiquidHeat

## What is a liquidation heatmap?

Imagine stacking vulnerable futures positions into price shelves. Choose **Hyperliquid positions** for sampled public account-reported thresholds, or **CEX estimates** for inferred private leverage. The horizontal axis is time; the vertical axis is BTC price. Each cell represents modeled USD notional in a price range at that historical moment. Bright shelves indicate relatively concentrated vulnerability under the model's assumptions.

Centralized exchanges do not publish everyone's collateral and leverage. LiquidHeat estimates these shelves from public observations. It is **not an order book**, a collection of known stop orders, or a promise that the market must visit a bright level.

## A practical first look

Start with 24H and $100 buckets. In Hyperliquid mode, **Fit liquidity** shows all bands. Account observations begin at collection time; older price candles do not provide historical position states. In CEX mode you can compare all three venues. Click **Fit price** so candles are visible. Pan around current price and inspect the side profile. Use $25 for finer detail and $200 for a broader view. Compare the underlying USD amounts: color normalization changes with the selected data.

A smaller bucket does not improve the quality of unavailable information. It changes display resolution. Summing four $25 buckets approximately produces one $100 bucket; aggregation is not four new observations.

## Controls

| Control | Behavior |
|---|---|
| Method | Separate reported Hyperliquid sample or inferred CEX exposure; never combined |
| Fit liquidity | Show the full liquidation price range |
| Lookback | Requested historical view; empty/gapped coverage remains honest |
| Bucket | Width of each price interval in USD |
| Exchange | Which venue exposure estimates contribute to the displayed matrix |
| Leverage | Which modeled leverage scenarios contribute |
| Intensity | Hide amounts below a percentage of the displayed normalization scale |
| Mark price | Toggle the margin reference-price overlay |
| Bands / Cumulative | Per-price profile versus progressively accumulated profile |
| Export | Download the current model response as JSON |
| Fit price | Fit candle wicks in the visible time range, without resetting time zoom |
| Price + / − | Zoom the vertical price range |
| Reset view | Full selected time window and fitted candles |

Drag the chart up/down to pan price and left/right to pan time. Wheel over the heatmap zooms time; Shift + wheel zooms price. Wheel over the right profile zooms price. Vertical and bottom sliders provide another way to pan/resize ranges. Incoming live updates preserve your chosen view.

The candle venue is labeled in the footer. It is a selected exchange's trade series, not a synthetic blended candle. In Hyperliquid mode, the headline is its BTC/USDC mark. In CEX mode, the headline mark is a selected-venue mean; do not assume it is identical to one venue's mark. UTC timestamps avoid ambiguity across viewers.

## Glossary with examples

| Term | Explanation |
|---|---|
| BTC/USDT | Bitcoin priced in the USDT stablecoin; USD labels approximate USDT as USD |
| Perpetual / perp | A futures-like derivative with no fixed expiry; funding helps tether it to the underlying market |
| Notional | Position size at current/reference price: 0.1 BTC × $80,000 = $8,000, not $8,000 collateral |
| Collateral / margin | Assets supporting a position; losses and requirements determine whether it can remain open |
| Cross margin | Shared account collateral supports multiple positions, making account-specific triggers unknowable from public trade data |
| Maintenance margin | Minimum margin required to keep a position; risk brackets can depend on position size |
| Bankruptcy price | A different price concept from the initial liquidation trigger; some reports publish this value |
| Mark price | Reference used for margin/liquidation evaluation; last-traded price may briefly differ |
| OHLCV / candle | Open, high, low, close and traded volume over an interval; the wick spans high to low |
| 1m / 5m | One-minute / five-minute intervals |
| Bucket | A fixed-width price bin. With $100 buckets, neighboring bins cover successive $100 ranges |
| Open interest / OI | Outstanding contracts. Each contract has both counterparties; it is not a net count of bullish positions |
| Taker | The trade side that accepts existing quoted liquidity; taker buying does not prove a new long opened |
| Funding | Periodic payments between sides; a potential input to positioning inference, not an exposure census |
| Volume profile | Observed executed volume distributed across traded prices, not resting order-book depth |
| VWAP | Volume-weighted average executed price; a candle's typical-price approximation is not true VWAP |
| Leverage tier | A modeled scenario such as 10× or 50×. Public liquidation reports usually do not reveal actual leverage |
| Gaussian projection | Smoothly distributes uncertain threshold locations over nearby buckets; integrated mass is normalized |
| Sweep | A mark-price move through an estimated vulnerability zone, reducing surviving modeled exposure |
| Decay | A prior for exposure becoming less relevant over time as positions close/change |
| Overhang | Remaining estimated vulnerable notional, not already-realized forced-liquidation flow |
| CLLD | Long modeled USD minus short modeled USD. Positive = more long overhang, negative = more short overhang |
| Normalized CLLD | `(long − short) / (long + short)`, bounded −1 to +1 when total exposure is positive |
| Magnet / concentration zone | A local modeled cluster ranked for inspection; no validated attraction mechanism is implied |
| Z-score | Distance above the current distribution's mean, measured in standard deviations; not a trading success probability |
| Percentile | Rank within a set. The color ceiling is the 98th percentile for Hyperliquid; CEX uses the 98.5th percentile of significant positive cells |
| Prior | An assumption used when observations do not identify a quantity; alpha and leverage weights are examples |
| Checkpoint | Saved ingestion/model progress allowing recovery without starting from zero |
| Backfill | Download genuinely available older observations; not recreation of unavailable private history |
| Baseline | A simple reference forecast that a learned model must improve upon |
| Candidate / champion | A fixed model under future evaluation / a model promoted after passing that evaluation |
| Holdout / out-of-sample | Data not used to fit the candidate; here, predictions are issued before the future outcome |
| Audit gap | Missing or insufficient transport evidence; affected learning outcomes are excluded |
| Calibration | Checking predictions against appropriate outcomes. A trained model is not automatically calibrated |
| Docker image / container / volume | Packaged software / a running instance / persistent storage that survives replacing that instance |

## Examples that prevent common mistakes

- A $20 million band is modeled **notional**, not $20 million of collateral, orders or guaranteed liquidations.
- A positive CLLD does not prove bullish price direction. It means the current model contains more vulnerable long than short notional.
- A bright 125× band does not identify actual 125× users. It comes from the assumed tier mixture. OKX's unsupported 125× tier contributes zero.
- A band shrinking is not necessarily an observed forced liquidation. Actual reported events are stored separately from modeled survival changes.
- A zero reported outcome is valid for learning only when its full window passes the feed audit. Missing coverage must not silently become zero activity.
- A model passing its future test demonstrates evidence for the specific reported-flow target during that period. It does not validate private-account reconstruction or profits.

## Coverage and history

Displayed history is compressed to about 288 time columns for efficient rendering. Longer views select the last saved state in each display interval; candles share the interval-end grid. Underlying five-minute states remain in the database during retention. Live mark/profile updates can arrive between finalized states.

Exchange APIs differ: candles can reach 30 days where available; OKX open-interest history is more restricted. Live aggregate trades build real entry profiles after collection starts. The chart never fills missing historical positions with invented data. Diagnostics carry the provenance and limitations.

## Public-account discovery is a sample

The free leaderboard provides large-account candidates. A public BTC trade stream adds both counterparties of fills ≥$50,000. A fill can close exposure, so only an actual subsequent position query adds a liquidation threshold. We rank at most 1,000 BTC positions among discovered accounts checked within 15 minutes. This does not establish the largest 1,000 positions in the whole market. Diagnostics expose current sample size, checked/discovered counts, sampled gross OI fraction, null/out-of-grid prices and discovery health. See [methodology](HYPERLIQUID.md).


## Compare CEX assumptions

In **CEX estimates**, the **Model** menu starts at Original. **Narrow kernels** concentrates each inferred entry cohort more tightly while preserving Original leverage weights, decay and extra collateral scenarios. **Replay reference** uses the original math on the same captured observations as the alternatives. Three separate sensitivity scenarios test no extra collateral, higher leverage weights and faster decay; they are not combined into a supposedly optimized model. Their history is hourly to conserve disk, while their current side profiles use the latest five-minute checkpoints. Narrow history keeps five-minute detail for seven days and 15-minute samples for older states.

Changing model assumptions can change surviving exposure and CLLD, not just the picture. Sharper bands do not prove that liquidation amounts or future prices are more accurate. Hyperliquid has no CEX Model menu and keeps its own reported thresholds. See [the experiment details](CEX_EXPERIMENTS.md).
