/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The backend streams Server-Sent Events. Proxying through Next in dev keeps
  // the browser on one origin, which sidesteps CORS and EventSource quirks.
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';
    return [
      { source: '/proxy/:path*', destination: `${backend}/:path*` },
    ];
  },
  images: { remotePatterns: [{ protocol: 'https', hostname: '**' }] },
};

export default nextConfig;
