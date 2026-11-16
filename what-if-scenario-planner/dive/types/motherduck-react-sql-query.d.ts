// The parts of the Dive runtime's @motherduck/react-sql-query that this Dive
// uses (the runtime provides the module; this file is only for `npm run typecheck`).
declare module "@motherduck/react-sql-query" {
  export type QueryResult<T = any[]> = {
    data: T | undefined;
    isLoading: boolean;
    isSuccess: boolean;
    isError: boolean;
    error: Error | null;
    refetch: () => void;
  };
  export function useSQLQuery(sql: string, opts?: { enabled?: boolean }): QueryResult<any[]>;
  export function useDiveState<T>(key: string, initial: T): [T, (value: T) => void];
  export function useRunFlight(flightId: string): { runAsync: (config: Record<string, string>) => Promise<{ runNumber: number | string }> };
  export function useFlightRun(flightId: string, runNumber?: number, opts?: { onFinished?: (run: any) => void }): { data?: any; error?: any };
}
