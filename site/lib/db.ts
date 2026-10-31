import postgres from "postgres";

// One small pool per server instance. prepare: false so the pooled Neon
// endpoint (PgBouncer in transaction mode) works as well as a direct one.
const globalForSql = globalThis as unknown as { sql?: ReturnType<typeof postgres> };

export const sql =
  globalForSql.sql ??
  postgres(process.env.DATABASE_URL ?? "postgresql://tape:tape@localhost/tape", {
    max: 3,
    prepare: false,
    idle_timeout: 20,
  });

if (process.env.NODE_ENV !== "production") globalForSql.sql = sql;
