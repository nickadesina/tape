import type { NextConfig } from "next";

// The site serves from the root of its own domain. BASE_PATH (e.g. /tape)
// builds it under a path instead, for serving behind another site's rewrite.
const config: NextConfig = {
  basePath: process.env.BASE_PATH || undefined,
  poweredByHeader: false,
};

export default config;
