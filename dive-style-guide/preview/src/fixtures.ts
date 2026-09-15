/**
 * Rows pulled once from `sample_data.nyc.taxi` (November 2022) so the preview
 * renders without a MotherDuck token. Counts are BigInt, matching what the Dive
 * runtime returns for a DuckDB BIGINT — the dive sources have to convert them
 * the same way they would in production.
 */

export const SUMMARY = [
	{
		trips: 3252620n,
		revenue: 71785093,
		avg_fare: 22.07,
		tip_rate: 18.8,
		avg_miles: 6.35,
	},
];

const DAILY_ROWS: [string, bigint, number][] = [
	['2022-11-01', 120506n, 2654262],
	['2022-11-02', 126271n, 2739747],
	['2022-11-03', 131484n, 2934702],
	['2022-11-04', 130682n, 2871259],
	['2022-11-05', 130401n, 2728856],
	['2022-11-06', 104570n, 2400124],
	['2022-11-07', 112135n, 2548831],
	['2022-11-08', 121121n, 2540425],
	['2022-11-09', 128700n, 2814806],
	['2022-11-10', 132727n, 2999507],
	['2022-11-11', 117728n, 2655692],
	['2022-11-12', 130944n, 2740374],
	['2022-11-13', 114269n, 2626213],
	['2022-11-14', 82278n, 1880651],
	['2022-11-15', 90728n, 2027395],
	['2022-11-16', 92660n, 2085448],
	['2022-11-17', 95344n, 2185740],
	['2022-11-18', 95454n, 2121568],
	['2022-11-19', 94932n, 2018649],
	['2022-11-20', 81125n, 1798234],
	['2022-11-21', 108526n, 2334026],
	['2022-11-22', 114546n, 2506905],
	['2022-11-23', 105640n, 2273213],
	['2022-11-24', 69663n, 1438893],
	['2022-11-25', 86607n, 1791288],
	['2022-11-26', 99513n, 2181767],
	['2022-11-27', 90529n, 2128857],
	['2022-11-28', 106488n, 2483880],
	['2022-11-29', 119372n, 2630170],
	['2022-11-30', 117677n, 2643610],
];

export const DAILY = DAILY_ROWS.map(([day, trips, revenue]) => ({
	day,
	trips,
	revenue,
}));

const PAYMENT_ROWS: [string, bigint, number, number, number, number][] = [
	['Credit card', 2483475n, 57107002, 79.6, 22.99, 23.3],
	['Cash', 598061n, 10918316, 15.2, 18.26, 0],
	['Unknown', 121959n, 3573825, 5.0, 29.3, 16.2],
	['No charge', 17791n, 134922, 0.2, 7.58, 0.3],
	['Dispute', 31334n, 51029, 0.1, 1.63, 4.5],
];

export const PAYMENTS = PAYMENT_ROWS.map(
	([payment_method, trips, revenue, revenue_share, avg_fare, tip_rate]) => ({
		payment_method,
		trips,
		revenue,
		revenue_share,
		avg_fare,
		tip_rate,
	}),
);

export const FIXTURES: Record<string, unknown[]> = {
	summary: SUMMARY,
	daily: DAILY,
	payments: PAYMENTS,
};
