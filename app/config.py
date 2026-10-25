import os

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://heatmap:local-test-only@127.0.0.1:55439/heatmap')
BACKFILL_DAYS = min(30, max(1, int(os.getenv('BACKFILL_DAYS', '30'))))
BASE_BUCKET = 25
VERSION = 'conservation-v1.0'
EXCHANGES = ('binance', 'bybit', 'okx')
LEVERAGES = (10, 25, 50, 100, 125)
