-- Начальная схема ClickHouse для Investment Assistant.
CREATE TABLE IF NOT EXISTS portfolios (
    portfolio_id String,
    ticker String,
    quantity Float64,
    avg_price Float64,
    sector String,          -- нефтегаз|финансы|металлургия|IT|ритейл|другое
    instrument_type String, -- акция|облигация
    currency String,        -- RUB|USD|EUR
    updated_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (portfolio_id, ticker);

CREATE TABLE IF NOT EXISTS price_history (
    ticker String,
    date Date,
    open Float64,
    high Float64,
    low Float64,
    close Float64,
    volume Float64
) ENGINE = MergeTree()
ORDER BY (ticker, date)
PARTITION BY toYYYYMM(date);

CREATE TABLE IF NOT EXISTS bond_details (
    ticker String,
    duration Float64,  -- дюрация в годах
    coupon_rate Float64, -- % годовых
    ytm Float64,  -- доходность к погашению
    maturity_date Date
) ENGINE = MergeTree()
ORDER BY ticker;

