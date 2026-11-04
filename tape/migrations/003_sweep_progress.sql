-- A sweep can now span several invocations (Vercel functions stop after
-- maxDuration). Each address-dex pair records the hour it was last swept, so a
-- resumed sweep takes only the pairs not yet done.
alter table address_dexes add column swept_hour timestamptz;
