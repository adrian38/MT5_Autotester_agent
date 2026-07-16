ALTER TABLE portfolios
    ADD COLUMN IF NOT EXISTS actual_closed_valley_dd double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS floating_dd_buffer double precision NOT NULL DEFAULT 0;

ALTER TABLE portfolio_allocations
    ADD COLUMN IF NOT EXISTS max_balance_dd_001 double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_equity_dd_001 double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS floating_dd_source text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS standalone_floating_dd double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS recent_net_profit_001 double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS recent_equity_dd_001 double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS has_recent_performance smallint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS final_tick_report_path text,
    ADD COLUMN IF NOT EXISTS full_history_report_path text;

INSERT INTO schema_versions(version) VALUES (4) ON CONFLICT DO NOTHING;
