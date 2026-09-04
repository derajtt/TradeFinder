import type { Metadata } from 'next';
import './globals.css';
import Nav from '../components/Nav';
import Onboarding from '../components/Onboarding';
import TopBar from '../components/TopBar';
import { ModeProvider } from '../lib/mode';
import { StatusProvider } from '../lib/status';

export const metadata: Metadata = {
  title: 'TradeFinder',
  description: 'Premarket scanner, paper-traded strategies and tracked picks — research only, no orders are placed',
};

/** One ModeProvider (Simple/Advanced) and one StatusProvider (one /api/status
 *  poll + one EventSource) for the whole app. The glossary FAB is gone; the
 *  TopBar "?" opens GlossaryPanel. */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-mode="simple" suppressHydrationWarning>
      <body>
        <ModeProvider>
          <StatusProvider>
            <div className="shell">
              <Onboarding />
              <Nav />
              <div className="main">
                <TopBar />
                <main className="content">{children}</main>
              </div>
            </div>
          </StatusProvider>
        </ModeProvider>
      </body>
    </html>
  );
}
