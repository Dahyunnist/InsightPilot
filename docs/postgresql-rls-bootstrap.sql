-- Run as the owner of the approved tables. Add and backfill tenant_id first.
-- Grant membership in insightpilot_reader to the application's login role.
DO $$
BEGIN
    CREATE ROLE insightpilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE insightpilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
END
$$;

GRANT USAGE ON SCHEMA public TO insightpilot_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC, insightpilot_reader;
REVOKE ALL PRIVILEGES ON TABLE public.orders, public.refunds, public.products,
    public.customers, public.campaign_attributions, public.campaigns,
    public.campaign_spend_events, public.refund_reversals, public.data_meta
    FROM PUBLIC, insightpilot_reader;
GRANT SELECT (tenant_id, order_id, product_id, region, channel, ordered_at, status, paid_amount_cents)
    ON TABLE public.orders TO insightpilot_reader;
GRANT SELECT (tenant_id, refund_id, order_id, refunded_at, recorded_at, amount_cents, status)
    ON TABLE public.refunds TO insightpilot_reader;
GRANT SELECT (tenant_id, product_id, category) ON TABLE public.products TO insightpilot_reader;
GRANT SELECT (tenant_id, order_id, campaign_id, weight)
    ON TABLE public.campaign_attributions TO insightpilot_reader;
GRANT SELECT (tenant_id, campaign_id, campaign_name) ON TABLE public.campaigns TO insightpilot_reader;
GRANT SELECT (tenant_id, event_id, business_event_id, revision, recorded_at,
              spend_cents, event_status, campaign_id, spent_at)
    ON TABLE public.campaign_spend_events TO insightpilot_reader;
GRANT SELECT (tenant_id, refund_id, recorded_at, amount_cents)
    ON TABLE public.refund_reversals TO insightpilot_reader;
GRANT SELECT (key, value) ON TABLE public.data_meta TO insightpilot_reader;

ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders FORCE ROW LEVEL SECURITY;
ALTER TABLE public.refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.refunds FORCE ROW LEVEL SECURITY;
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_attributions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_attributions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_spend_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_spend_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.refund_reversals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.refund_reversals FORCE ROW LEVEL SECURITY;
ALTER TABLE public.customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.customers FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.orders;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.refunds;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.products;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.campaign_attributions;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.campaigns;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.campaign_spend_events;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.refund_reversals;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.customers;

CREATE POLICY insightpilot_tenant_isolation ON public.orders TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY insightpilot_tenant_isolation ON public.refunds TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY insightpilot_tenant_isolation ON public.products TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY insightpilot_tenant_isolation ON public.campaign_attributions TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY insightpilot_tenant_isolation ON public.campaigns TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY insightpilot_tenant_isolation ON public.campaign_spend_events TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY insightpilot_tenant_isolation ON public.refund_reversals TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY insightpilot_tenant_isolation ON public.customers TO insightpilot_reader
    USING (tenant_id = current_setting('app.tenant_id', true));
