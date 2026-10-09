import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Shobotics — Mind, Machine & Media',
  description: 'Architecting intelligent systems across mind, machine and media.'
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
