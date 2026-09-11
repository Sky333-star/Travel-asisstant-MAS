import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Wayfarer -- Multi-Agent Travel Assistant',
  description:
    'Plan a trip by voice or text. A team of specialist agents analyses your '
    + 'request, checks real weather, shortlists destinations, and builds a '
    + 'validated day-by-day plan from open data.',
  keywords: ['travel planner', 'multi-agent', 'LangGraph', 'MCP', 'open data'],
  authors: [{ name: 'Wayfarer' }],
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fafbfd' },
    { media: '(prefers-color-scheme: dark)', color: '#11141a' },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/*
          Applied before first paint so the page never flashes light before
          switching to dark. It has to be inline and synchronous to beat the
          renderer, which is why it is a raw script rather than a component.
        */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function () {
                try {
                  var stored = localStorage.getItem('wayfarer-theme');
                  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                  if (stored === 'dark' || (!stored && prefersDark)) {
                    document.documentElement.classList.add('dark');
                  }
                } catch (e) {}
              })();
            `,
          }}
        />
      </head>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
