"use client";

import { create } from "zustand";

export type AnalysisDocumentEntry = {
  filename: string;
  status: "pending" | "complete" | "error" | "skipped";
};

type AnalysisProgressState = {
  isAnalyzing: boolean;
  completed: number;
  total: number;
  newLinks: number;
  startedAt: number | null;
  message: string;
  lastDocument: string | null;
  recentProcessed: AnalysisDocumentEntry[];
  setProgress: (payload: Partial<AnalysisProgressState>) => void;
  pushProcessed: (entry: AnalysisDocumentEntry) => void;
  clear: () => void;
};

const initialState = {
  isAnalyzing: false,
  completed: 0,
  total: 0,
  newLinks: 0,
  startedAt: null,
  message: "",
  lastDocument: null,
  recentProcessed: [] as AnalysisDocumentEntry[],
};

export const useAnalysisProgressStore = create<AnalysisProgressState>()((set) => ({
  ...initialState,
  setProgress: (payload) =>
    set((state) => ({
      ...state,
      ...payload,
    })),
  pushProcessed: (entry) =>
    set((state) => ({
      ...state,
      recentProcessed: [entry, ...state.recentProcessed].slice(0, 10),
      lastDocument: entry.filename,
    })),
  clear: () => set({ ...initialState }),
}));

export function rehydrateAnalysisProgressStore() {
  // No-op: runtime-only Zustand store for navigation persistence.
}

