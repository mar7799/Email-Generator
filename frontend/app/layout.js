import './globals.css';

export const metadata = {
  title: 'Email Generator',
  description: 'FastAPI + Next.js email campaign runner'
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
