import type { Metadata } from 'next';
import './globals.css';
import Nav from '../components/Nav';
import Onboarding from '../components/Onboarding';
import TopBar from '../components/TopBar';
import GlossaryFab from '../components/GlossaryFab';

export const metadata: Metadata = {
  title: 'Premarket Hunter',
  description: 'Live premarket momentum, catalyst and BUY-signal research dashboard',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <Onboarding />
        <Nav />
          <div className="main">
            <TopBar />
            <main className="content">{children}</main>
            <GlossaryFab />
          </div>
        </div>
      </body>
    </html>
  );
}
