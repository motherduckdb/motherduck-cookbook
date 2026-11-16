import { useCallback, useEffect, useRef, useState } from "react";
import { useSQLQuery } from "@motherduck/react-sql-query";

export const DB = "scenario_planner";
export const N = (v: unknown): number => (v == null ? 0 : Number(v));

/**
 * Imperative SQL on top of useSQLQuery (the Dive runtime exposes no connection
 * object). One statement in flight at a time. From the Univer Dive: resolve only
 * after this dispatch was observed loading, so a stale success from the
 * previous statement is never returned as this one's result.
 */
export function useImperativeSql(): (sql: string) => Promise<any[]> {
  const [sql, setSql] = useState<string | null>(null);
  const resolver = useRef<((rows: any[]) => void) | null>(null);
  const rejecter = useRef<((err: Error) => void) | null>(null);
  const loadingSeen = useRef(false);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const result = useSQLQuery(sql ?? "SELECT 1", { enabled: sql != null });

  useEffect(() => {
    if (sql == null) return;
    if (result.isLoading) {
      loadingSeen.current = true;
      return;
    }
    if (!loadingSeen.current) return;
    const resolve = resolver.current;
    const reject = rejecter.current;
    if (result.isSuccess && result.data !== undefined) {
      resolver.current = rejecter.current = null;
      loadingSeen.current = false;
      setSql(null);
      resolve?.(result.data as any[]);
    } else if (result.isError) {
      resolver.current = rejecter.current = null;
      loadingSeen.current = false;
      setSql(null);
      reject?.(result.error ?? new Error("SQL query failed"));
    }
  }, [sql, result.isLoading, result.isSuccess, result.isError, result.data, result.error]);

  return useCallback((next: string) => {
    const run = () =>
      new Promise<any[]>((resolve, reject) => {
        resolver.current = resolve;
        rejecter.current = reject;
        loadingSeen.current = false;
        setSql(next);
      });
    const p = queue.current.then(run, run);
    queue.current = p.catch(() => undefined);
    return p;
  }, []);
}
