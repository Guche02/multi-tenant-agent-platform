import type { NextConfig } from "next";

// The browser only ever talks to this server: /api/* is passed through to FastAPI, so the
// session cookies (httpOnly, SameSite) stay same-origin and no CORS is needed.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const config: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  // Compression would buffer the chat's Server-Sent Events, so tokens wouldn't stream.
  compress: false,
  // The dev-mode badge sits over the message box on a phone.
  devIndicators: false,
};

export default config;
