import type { NextConfig } from "next";

// The site is served under openmarket.systems/tape until it has a domain of
// its own: build with BASE_PATH=/tape and rewrite /tape/:path* to this
// deployment from the openmarket project. Unset, it serves from the root.
const config: NextConfig = {
  basePath: process.env.BASE_PATH || undefined,
  poweredByHeader: false,
};

export default config;
