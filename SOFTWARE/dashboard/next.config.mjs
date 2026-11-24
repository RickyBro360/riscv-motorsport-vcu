/** @type {import('next').NextConfig} */
const nextConfig = {
  // Stage 2c: allow opening the dev server by LAN IP (e.g. http://10.49.218.128:3000)
  // from either laptop. Next 16 blocks cross-origin dev requests otherwise.
  allowedDevOrigins: ['10.49.218.*', '192.168.*.*', '10.*.*.*'],
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
}

export default nextConfig
