import { create } from 'zustand';

interface PythonStrategyBacktestResult {
  status: string;
  symbol: string;
  metrics: Record<string, any>;
  optimization: Record<string, any>;
  tearsheet_url: string;
  parameters: Record<string, any>;
}

interface PythonStrategyBacktestState {
  result: PythonStrategyBacktestResult | null;
  isLoading: boolean;
  error: string | null;
  setResult: (result: PythonStrategyBacktestResult) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  clearResult: () => void;
}

export const usePythonStrategyBacktestStore = create<PythonStrategyBacktestState>((set) => ({
  result: null,
  isLoading: false,
  error: null,
  setResult: (result) => set({ result, error: null }),
  setLoading: (isLoading) => set({ isLoading }),
  setError: (error) => set({ error }),
  clearResult: () => set({ result: null, error: null }),
}));
