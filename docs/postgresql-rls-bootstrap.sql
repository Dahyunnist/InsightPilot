-- Run as the owner of the approved tables. Add and backfill tenant_id first.
-- Grant membership in insightpilot_reader to the application's login role.
DO $$
BEGIN
    CREATE ROLE insightpilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE insightpilot_reader NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
END
$$;

GRANT USAGE ON SCHEMA public TO insightpilot_reader;
GRANT SELECT ON TABLE public.orders, public.refunds, public.products,
    public.campaign_attributions, public.campaigns, public.campaign_spend_events,
    public.refund_reversals, public.data_meta TO insightpilot_reader;

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

DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.orders;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.refunds;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.products;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.campaign_attributions;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.campaigns;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.campaign_spend_events;
DROP POLICY IF EXISTS insightpilot_tenant_isolation ON public.refund_reversals;

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
