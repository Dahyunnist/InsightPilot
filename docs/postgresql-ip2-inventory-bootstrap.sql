DO $$
BEGIN
    CREATE ROLE insightpilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE insightpilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
END
$$;

GRANT USAGE ON SCHEMA public TO insightpilot_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC, insightpilot_reader;
REVOKE ALL PRIVILEGES ON TABLE public.data_meta,
    public.inventory_daily_snapshots, public.inventory_cogs_events
    FROM PUBLIC, insightpilot_reader;
GRANT SELECT (key, value) ON TABLE public.data_meta TO insightpilot_reader;
GRANT SELECT (tenant_id, snapshot_id, category, snapshot_at, local_date, inventory_value_cents)
    ON TABLE public.inventory_daily_snapshots TO insightpilot_reader;
GRANT SELECT (tenant_id, event_id, category, sold_at, cogs_cents)
    ON TABLE public.inventory_cogs_events TO insightpilot_reader;

ALTER TABLE public.inventory_daily_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_daily_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_cogs_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_cogs_events FORCE ROW LEVEL SECURITY;

CREATE POLICY insightpilot_tenant_isolation ON public.inventory_daily_snapshots TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY insightpilot_tenant_isolation ON public.inventory_cogs_events TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
