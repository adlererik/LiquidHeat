# Prospective learning and validation

This experiment targets recorded Bybit liquidation flow. It does not learn or modify reported Hyperliquid liquidation thresholds, and it does not calibrate the separate CEX exposure model.

The application automatically collects auditable outcomes, trains small candidate models, records their future predictions, and promotes only candidates that pass a fixed future test. It can also roll back an underperforming promoted model. It runs in the existing Debian engine container under its existing CPU/RAM limits. No new dependencies, accounts, APIs, paid services or containers are required for normal operation.

## What can learn

The target is **reported Bybit BTCUSDT liquidation notional over the next 15 minutes**, separately for long and short liquidations. It is not BTC price direction, true total exchange liquidations, private leverage, or the absolute heatmap exposure. Bybit documents its `allLiquidation.BTCUSDT` feed as an all-liquidation stream, but exchange-side omissions cannot be independently ruled out. Its reported price is a bankruptcy price, not an account's original mark-price trigger. USD means reported BTC quantity × reported price, treating USDT as USD.

The existing heatmap model, alpha=.04, historical matrices and hidden-position priors remain unchanged. A better reported-flow forecast would be evidence of skill for this target only. It would not establish that the heatmap's dollar totals are correct. Sampled Binance/OKX liquidation totals are excluded from model fitting.

The forecast target covers all reported Bybit tiers. Heatmap leverage/exchange filters do not retarget this separate forecast, because reported liquidations do not identify leverage tiers.

Primary reference: [Bybit all-liquidation documentation](https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation). Implementation: `app/feed_audit.py`, `app/learning_math.py`, `app/learning.py`; schema migration `003_learning.sql`.

## Observation audit

- Store actual receipt timestamps for new WebSocket liquidation reports, atomically with deduplicated events. No receipt times are invented for old historical records.
- Record connection/session evidence in minute bins, using actual received messages. A minute qualifies only when one session has an acknowledged liquidation subscription, first receipt within ten seconds of its start, last receipt after second fifty, no observed inter-message gap above fifteen seconds, and no measured server-message delay above ten seconds.
- Every minute in an outcome window must qualify. A transport gap makes the label **ineligible**, even if its recorded event sum is zero. A quiet, audited window can legitimately have a zero reported-flow label.
- These thresholds are conservative transport checks, not a mathematical guarantee that every exchange event was delivered. Clocks, undetected drops, transport granularity and exchange behavior remain limitations.

## Forecasts before outcomes

Each side gets a nonoverlapping 15-minute outcome window. The window starts on a future minute boundary, usually with less than one minute of lead. A database constraint requires issuance before that start and the source model timestamp no later than issuance. The insert additionally checks the live database clock so a slow computation cannot backdate a late forecast.

Features use a current Bybit mark, the last closed heatmap state, mark sweeps known by issuance, nearby modeled exposure within 0.5% and 2%, past volatility/directional movement, past recorded liquidation flow, side concentration and observed-profile availability. Future mark paths are not features. Receipt cutoffs prevent late reports from appearing in earlier input features.

After the window ends, wait two minutes for late delivery, audit its transport, and freeze the outcome, eligibility and score. Later arrivals cannot revise this record. Original events remain separately stored. Historical replay and synthetic software-test fixtures never become live prospective training examples.

## Training and promotion

The fixed learner is ridge regression on `log1p(reported USD / 10000)`, with training-only feature standardization and a fixed penalty of ten. This is a modest nonlinear-in-USD point forecast, not a probability or a calibrated expected USD amount. Numerical bounds prevent negative/infinite forecasts. Coefficients, source dates and protocol version persist in the model registry.

Training needs at least **600 eligible windows per side**, a span of **seven days**, and **30 positive windows**. It uses at most 2,688 eligible observations from the preceding 28 days. Review runs daily; one candidate per side remains fixed during its future test. There is no hyperparameter search over the holdout.

A candidate's future test lasts **14 fixed calendar days**. Its original predictions are compared with two baselines recorded at issuance: the past eligible log-typical outcome and a last-hour reported-flow persistence forecast. The current promoted model is another reference. Promotion requires:

- At least 1,000 eligible nonoverlapping holdout windows, 12 distinct UTC days, 30 positive windows, and at least 80% of issued holdout windows auditable.
- At least 10% lower mean squared log error than the best recorded reference.
- Positive fifth-percentile paired day-block bootstrap improvement (400 deterministic resamples).
- USD mean absolute error no more than 5% worse than the best reference by that metric.

The decision is made once after the fixed horizon, rather than repeatedly extending it until a favorable score appears. An insufficient or failed candidate is rejected. A later candidate trains on then-available past labels and gets a new untouched future test. The engineering thresholds and day-block bounds are conservative selection rules; they are not proof of general future accuracy or a fully calibrated confidence interval.

Earliest promotion is on the order of **three weeks of adequate observations**, often longer because of gaps, quiet markets and daily review timing. There is no promised promotion date. Before enough evidence, numeric baseline records are still collected for evaluation, but the dashboard shows “Collecting” rather than a confident dollar forecast. Baseline display requires at least 30 prior audited outcomes for that side.

## Rollback and operation

Promotions affect the reported-flow forecast panel only. They never rewrite a past forecast, liquidation event or heatmap matrix. Old model artifacts and decisions remain auditable. After a promoted model has run for seven days, daily reviews can roll it back to the baseline if at least 400 eligible recent windows show more than 25% higher log loss than the best recorded baseline, with a negative upper paired day-block improvement bound. Sparse evidence does not trigger a statistical decision. Future accuracy remains uncertain and market changes can invalidate past results.

The **Learning & validation** dashboard panel shows eligible/positive counts, exclusions, future-test dates, active model status and any earned holdout improvement. Stale forecasts are marked unavailable. Details are in `GET /api/v1/learning` and the diagnostics response; compact status changes also use the existing WebSocket.

Daily review and 15-second scheduling run inside the engine and resume after reboot. Durable rows make issuance and decisions idempotent. Gaps around restarts are excluded. Retention: transport and receipt evidence 35 days; immutable forecasts/labels 90 days; inactive model artifacts/decisions one year. Training is bounded to 28 days. Existing database/filesystem capacity guards still apply.

The operator should keep the Debian server and its exchange connections running, inspect the panel for sustained gaps, and judge measured future results rather than colors. No manual parameter tuning is needed to start this pipeline. Do not interpret “candidate trained” as “validated,” or “promoted” as proof about hidden positions.
