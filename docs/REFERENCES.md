# Authentic chart references

Research date: 2026-10-25. Public pages and article figures only; no login, paywall bypass, paid API or Glassnode dataset is used.

- [Glassnode: Pressure Points — Liquidation Heatmaps & Market Bias](https://research.glassnode.com/liquidation-heatmaps/) (2025-09-23).
- [Official BTC Hyperliquid dashboard](https://studio.glassnode.com/dashboards/hyperliquid?a=BTC). The public shell is accessible; private Studio data was not accessed.
- [Glassnode derivative endpoint documentation](https://docs.glassnode.com/basic-api/endpoints/derivatives): actual `liquidation_heatmap` metric exists and requires an API key. It is not called by this project.
- [Public heatmap article figure](https://storage.ghost.io/c/25/7e/257e6d23-09bb-4d1c-b3ed-16ff70eb3f9f/content/images/2025/09/Captura-de-pantalla-2025-09-15-a-las-13.49.52.png) (linked only; not redistributed). Copyright Glassnode; not an application asset.
- [Public walls figure](https://storage.ghost.io/c/25/7e/257e6d23-09bb-4d1c-b3ed-16ff70eb3f9f/content/images/2025/09/image-1.png) (linked only; not redistributed). Copyright Glassnode; not an application asset.
- [CoinGlass model2 documentation](https://docs.coinglass.com/reference/liquidation-aggregate-heatmap-model2) was checked for brand distinction. CoinGlass is a different product; its paid API is not used.

## Visual observations

The authentic public figure has a dark blue plotting field, fine horizontal concentration traces, yellow/orange peaks with purple lower-intensity regions, green/red candles sharing a numeric price axis, date labels below and a vertical intensity legend to the right. Its published figure has a white outer frame, rather than a full dark dashboard screenshot. We therefore cannot substantiate an exact dark Studio control layout from that figure.

Our dashboard applies the requested #0b0e14 surface and exact user-specified seven-stop colormap, green/red trade candles, shared UTC/price coordinates, compact controls and an adjacent side profile. The cumulative profile and bottom CLLD oscillator are additional requested views. The Glassnode reference shows a spot-price candle track; ours is explicitly labeled perpetual trade candles from one selected venue. Our green/red profile follows long/short semantics. Fonts and controls are an independent design; no Glassnode branding or watermark is copied.

Leverage and exchange controls, intensity threshold, zoom, cumulative mode and percentile normalization are implemented from the assignment; their exact visual/behavioral equivalence to private Studio controls is unverified. Saved figures permit qualitative comparison, not pixel-perfect or statistical equivalence. Automated visual tests use a live-data tolerance and a same-session screenshot stability check, not pixel comparison between different markets/dates.
