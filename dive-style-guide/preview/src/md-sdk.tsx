/**
 * Fixture stand-in for `@motherduck/react-sql-query`.
 *
 * The dive sources in `src/dives/` are real Dive code: they call `useSQLQuery`
 * with fully qualified SQL against `sample_data.nyc.taxi` and could be saved to
 * MotherDuck as they are. Locally, this shim reads the `-- @fixture <name>` tag
 * on the first line of each query and returns pre-fetched rows, so rendering
 * the five styles needs no token and produces byte-identical screenshots.
 *
 * For live data, swap this file for the `md-sdk.tsx` in the MotherDuck dives
 * guide (`get_dive_guide`), which talks to `@motherduck/wasm-client`.
 */
import { FIXTURES } from './fixtures';

export type UseSQLQueryResult = {
	data: unknown[] | undefined;
	isLoading: boolean;
	isSuccess: boolean;
	isError: boolean;
	error: Error | null;
};

export function useSQLQuery(sql: string): UseSQLQueryResult {
	const tag = /--\s*@fixture\s+(\w+)/.exec(sql)?.[1];
	const data = tag ? FIXTURES[tag] : undefined;
	if (!data) {
		throw new Error(
			`No fixture for query. Add a "-- @fixture <name>" tag matching a key in fixtures.ts. Got: ${tag ?? 'none'}`,
		);
	}
	return {
		data,
		isLoading: false,
		isSuccess: true,
		isError: false,
		error: null,
	};
}
