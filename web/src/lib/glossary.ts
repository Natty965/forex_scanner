// Plain-English explanations shown behind the "?" buttons. Short on purpose.

export const GLOSSARY = {
  LIVE: "The price is at most 5 minutes old.",
  DELAYED: "The price is 5 to 30 minutes old: the data source hasn't updated recently.",
  STALE:
    "Older than allowed while the market is open: the source (or the worker) isn't updating. Don't treat it as current.",
  "MARKET CLOSED":
    "Forex closes from Friday 17:00 to Sunday 17:00 New York time. You're seeing the last price before the close.",
  OK: "This analysis comes from current data.",
  price:
    "Indicative mid price from Yahoo Finance (free, unofficial). Not a price you could trade at: real trades include a spread.",
  pip: "A pip is the standard unit of price movement: 0.0001 for most pairs, 0.01 for JPY pairs.",
  change:
    "How far the price moved compared with 1 hour / 24 hours ago, in pips and percent. * means there was no price at that exact time (e.g. the weekend or a gap in the data), so it compares with the last earlier price.",
  trend:
    "UP / DOWN when a best-fit line through the last 50 closed candles moved at least 3 average candle ranges AND the price is on the same side of its average. Otherwise SIDEWAYS. Describes the past, not the future.",
  ema:
    "EMA = exponential moving average. 'Stacked up' means EMA 20 > 50 > 200: recent prices averaged higher than older ones. 'Stacked down' is the reverse. 'Mixed' means no consistent order.",
  rsi:
    "RSI (0-100) compares the size of recent up-moves with recent down-moves. 70+ is often called 'overbought', 30 or less 'oversold'. It is not a signal: in strong trends RSI can stay high or low for a long time.",
  atr: "ATR = average true range: how far a typical candle has moved (high to low, including gaps). It says how much price moves, not which way.",
  worker:
    "The worker is the background process that fetches prices and calculates everything every 5 minutes. The page only shows what it saved.",
  events:
    "Changes since the worker's previous check: trend changes, EMA 20 crossing EMA 50, RSI entering or leaving its 70/30 zones. Usually happen when a new candle closes.",
  trading:
    "Risk warnings: times and situations when beginners usually should NOT open new trades (weekend close, daily rollover, unusually high volatility, prices not current). They never say when to trade: no warnings is not a signal.",
  structure:
    "Swing high/low: a candle whose high (low) beats the 3 candles on each side; confirmed only after those 3 later candles close, so the newest swing is at least 3 candles old. HH/LH = higher/lower high, HL/LL = higher/lower low. Levels (dashed lines, R = resistance above price, S = support below) are price areas where price turned at least twice in the last 300 candles. They show where price reacted before; price often breaks through them. Not signals.",
  conditions:
    "Things worth noticing right now, each with the reason. Market conditions are only shown from current data. Descriptions, not signals.",
  candle:
    "A candle summarises one period (e.g. 1 hour): where price opened, its highest and lowest point (the thin 'wick'), and where it closed. On our charts, green candles closed higher than they opened and red ones closed lower.",
  timeframe:
    "How long each candle lasts: M15 = 15 minutes, H1 = 1 hour, D1 = 1 Forex day (17:00 to 17:00 New York time). Longer timeframes show the bigger picture; shorter ones react faster.",
  forming:
    "The newest candle may still be forming: its high, low and close can change until its period ends. It is drawn faded, and no indicator uses it.",
  warmup:
    "Indicators start from an estimate and need enough past candles before their values can be trusted (EMA 200 needs 1,004). Until then their line is simply not drawn.",
  spread:
    "The difference between the price you could buy at and the price you could sell at. Our prices are mid prices, between the two, so they don't include it.",
  sessions:
    "Forex trades around the clock on weekdays. Activity is usually highest when London (about 08:00-17:00 UTC in winter) and New York (about 13:00-22:00 UTC in winter) are open; both start an hour earlier in UTC during summer time. It is usually quietest late in the Asian session.",
} as const;

export type GlossaryKey = keyof typeof GLOSSARY;
