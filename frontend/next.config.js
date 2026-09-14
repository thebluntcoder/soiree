// This app only hosts the Swiggy OAuth callback (see app/auth/callback/
// and app/callback/) — it calls the backend with an absolute URL, not a
// relative /api/* path, so no rewrite/proxy config is needed here.

/** @type {import('next').NextConfig} */
const nextConfig = {}

module.exports = nextConfig
