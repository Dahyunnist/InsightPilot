-- Empty synthetic schema used only by the opt-in IP-3 reliability Compose matrix.
DO $$
BEGIN
    CREATE ROLE insightpilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE insightpilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
END
$$;

GRANT USAGE ON SCHEMA public TO insightpilot_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC, insightpilot_reader;
REVOKE ALL PRIVILEGES ON TABLE public.orders, public.refunds, public.products, public.data_meta
    FROM PUBLIC, insightpilot_reader;
GRANT SELECT (tenant_id, order_id, product_id, region, channel, ordered_at, status, paid_amount_cents)
    ON TABLE public.orders TO insightpilot_reader;
GRANT SELECT (tenant_id, refund_id, order_id, refunded_at, recorded_at, amount_cents, status)
    ON TABLE public.refunds TO insightpilot_reader;
GRANT SELECT (tenant_id, product_id, category) ON TABLE public.products TO insightpilot_reader;
GRANT SELECT (key, value) ON TABLE public.data_meta TO insightpilot_reader;

ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders FORCE ROW LEVEL SECURITY;
ALTER TABLE public.refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.refunds FORCE ROW LEVEL SECURITY;
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products FORCE ROW LEVEL SECURITY;

CREATE POLICY ip3_reliability_tenant_policy ON public.orders TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY ip3_reliability_tenant_policy ON public.refunds TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY ip3_reliability_tenant_policy ON public.products TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
