-- ============================================
-- Stan's Options Trading Dashboard — Schema
-- Run in Supabase SQL Editor
-- ============================================

-- 1. Daily scan metadata (one row per day)
CREATE TABLE daily_scans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_date DATE NOT NULL UNIQUE,
    vix NUMERIC,
    risk_free_rate NUMERIC,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Individual option rows from each scan
CREATE TABLE scan_options (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id UUID NOT NULL REFERENCES daily_scans(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    name TEXT,
    iv_rank NUMERIC,
    dte INTEGER,
    delta NUMERIC,
    exp_date DATE,
    pop NUMERIC,
    p50 NUMERIC,
    strike NUMERIC,
    bid NUMERIC,
    ask NUMERIC,
    bid_ask_spread NUMERIC,
    put_price NUMERIC,
    earnings DATE,
    underlying_price NUMERIC,
    selected BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Open positions (created when checkbox is ticked)
CREATE TABLE positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_option_id UUID REFERENCES scan_options(id),
    symbol TEXT NOT NULL,
    name TEXT,
    option_type TEXT DEFAULT 'Put',
    strike NUMERIC NOT NULL,
    exp_date DATE NOT NULL,
    price_paid NUMERIC,
    quantity INTEGER DEFAULT 1,
    direction TEXT DEFAULT 'Short',
    opened_at TIMESTAMPTZ DEFAULT now(),
    closed_at TIMESTAMPTZ,
    status TEXT DEFAULT 'open'
);

-- 4. Daily P&L snapshots for open positions
CREATE TABLE position_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id UUID NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    dte INTEGER,
    share_price NUMERIC,
    option_price NUMERIC,
    difference NUMERIC,
    pl NUMERIC,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(position_id, snapshot_date)
);

-- 5. Scanner config (replaces Google Sheets Config tab)
CREATE TABLE config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================
-- Indexes for performance
-- ============================================
CREATE INDEX idx_scan_options_scan_id ON scan_options(scan_id);
CREATE INDEX idx_scan_options_symbol ON scan_options(symbol);
CREATE INDEX idx_scan_options_selected ON scan_options(selected) WHERE selected = TRUE;
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_position_snapshots_position_id ON position_snapshots(position_id);
CREATE INDEX idx_position_snapshots_date ON position_snapshots(snapshot_date);

-- ============================================
-- Insert default config (same as current Google Sheets Config tab)
-- ============================================
INSERT INTO config (key, value) VALUES
    ('symbols', '["MU","SNOW","ORCL","BIDU","CRM","AVGO","ADBE","BABA","MRVL","LULU","VST","NVDA","META","MSFT","TSLA"]'),
    ('delta_min', '-0.30'),
    ('delta_max', '-0.15'),
    ('dte_min', '30'),
    ('dte_max', '60');

-- ============================================
-- Enable Row Level Security (RLS)
-- ============================================
ALTER TABLE daily_scans ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_options ENABLE ROW LEVEL SECURITY;
ALTER TABLE positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE position_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE config ENABLE ROW LEVEL SECURITY;

-- Allow service role full access (used by our Python scripts)
CREATE POLICY "Service role full access" ON daily_scans FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON scan_options FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON positions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON position_snapshots FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON config FOR ALL USING (true) WITH CHECK (true);

-- Allow anon read access (for Streamlit dashboard)
CREATE POLICY "Anon read access" ON daily_scans FOR SELECT USING (true);
CREATE POLICY "Anon read access" ON scan_options FOR SELECT USING (true);
CREATE POLICY "Anon read access" ON positions FOR SELECT USING (true);
CREATE POLICY "Anon read access" ON position_snapshots FOR SELECT USING (true);
CREATE POLICY "Anon read access" ON config FOR SELECT USING (true);

-- Allow anon to update selected column (checkbox toggle from dashboard)
CREATE POLICY "Anon update selected" ON scan_options FOR UPDATE USING (true) WITH CHECK (true);
