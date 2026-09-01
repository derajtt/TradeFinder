import type { Metadata } from 'next';
import './globals.css';
import Nav from '../components/Nav';
import TopBar from '../components/TopBar';

export const metadata: Metadata = {
  title: 'Premarket Hunter',
  description: 'Live premarket momentum, catalyst and BUY-signal research dashboard',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <Nav />
          <div className="main">
            <TopBar />
            <main className="content">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
