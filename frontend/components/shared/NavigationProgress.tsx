"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import NProgress from "nprogress";

export function NavigationProgress() {
  const pathname = usePathname();
  const startedRef = useRef(false);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    NProgress.configure({ showSpinner: false, minimum: 0.08, trickleSpeed: 120 });
  }, []);

  useEffect(() => {
    if (startedRef.current) {
      NProgress.done();
      startedRef.current = false;
    }
  }, [pathname]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const anchor = target?.closest("a[href]") as HTMLAnchorElement | null;
      if (!anchor) return;
      if (anchor.target && anchor.target !== "_self") return;
      if (anchor.hasAttribute("download")) return;

      const href = anchor.getAttribute("href");
      if (!href) return;
      if (href.startsWith("http") || href.startsWith("mailto:") || href.startsWith("tel:")) return;
      if (href.startsWith("#")) return;

      startedRef.current = true;
      NProgress.start();
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = window.setTimeout(() => {
        if (startedRef.current) {
          NProgress.done();
          startedRef.current = false;
        }
      }, 12000);
    };

    window.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("click", onClick, true);
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    };
  }, []);

  return null;
}
