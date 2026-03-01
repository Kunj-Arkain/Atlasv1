/** @type {import('next').NextConfig} */
const config = {
  output: "standalone",

  // Proxy API calls to FastAPI when in fastapi mode
  async rewrites() {
    const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
    if (process.env.NEXT_PUBLIC_API_MODE === "fastapi" && apiBase) {
      return [
        {
          source: "/api/v1/:path*",
          destination: `${apiBase}/api/v1/:path*`,
        },
      ];
    }
    return [];
  },
};

export default config;
