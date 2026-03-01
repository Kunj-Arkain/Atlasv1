import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Arkain — Agentic Real Estate Intelligence",
  description: "Enterprise AI pipeline for real estate investment and gaming analysis",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
