-- Structured run metadata (e.g. the coverage a cohort set was computed at).
alter table runs add column meta jsonb not null default '{}';
