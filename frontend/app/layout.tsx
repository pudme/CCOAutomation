import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/layout/Sidebar";
import { NavigationProgress } from "@/components/shared/NavigationProgress";
import { TopBar } from "@/components/layout/TopBar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "sonner";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "CCOA",
  description: "Chief Compliance Officer Assistant",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <TooltipProvider>
          <NavigationProgress />
          <Toaster richColors position="top-right" closeButton />
          <div className="flex min-h-screen">
            <Sidebar />
            <main className="flex min-h-screen flex-1 flex-col">
              <TopBar />
              <div className="flex-1 p-6">{children}</div>
            </main>
          </div>
        </TooltipProvider>
      </body>
    </html>
  );
}
