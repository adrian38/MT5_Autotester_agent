ALTER TABLE candidates ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE candidate_robustness ALTER COLUMN candidate_id DROP NOT NULL;
ALTER TABLE candidate_robustness ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE candidate_final_tick ALTER COLUMN candidate_id DROP NOT NULL;
ALTER TABLE candidate_final_tick ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE candidate_final_tick_6m ALTER COLUMN candidate_id DROP NOT NULL;
ALTER TABLE candidate_final_tick_6m ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE generation_seed_selection ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE portfolio_allocations ALTER COLUMN portfolio_id DROP NOT NULL;
ALTER TABLE portfolio_decision_log ALTER COLUMN portfolio_id DROP NOT NULL;
ALTER TABLE portfolio_members ALTER COLUMN portfolio_id DROP NOT NULL;
ALTER TABLE portfolio_versions ALTER COLUMN portfolio_id DROP NOT NULL;

INSERT INTO schema_versions(version) VALUES (2) ON CONFLICT DO NOTHING;
