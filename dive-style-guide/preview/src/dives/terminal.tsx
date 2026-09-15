/**
 * Terminal — written from `styles/terminal.sql`.
 *
 * Mission-control console: deep indigo, monospace throughout, bracketed section
 * headers, bordered panels, dotted grid, periwinkle values with violet
 * secondaries and amber for the anomaly, and a status line at the bottom.
 */
import {
	CartesianGrid,
	Line,
	LineChart,
	ResponsiveContainer,
	XAxis,
	YAxis,
} from 'recharts';
import { useSQLQuery } from '@motherduck/react-sql-query';
import { DAILY_SQL, PAYMENTS_SQL, SUMMARY_SQL } from '../queries';

export const REQUIRED_DATABASES = [
	{
		type: 'share',
		path: 'md:_share/sample_data/23b0d623-1361-421d-ae77-62d701d471e6',
		alias: 'sample_data',
	},
];

const BG = '#0a0e23';
const PANEL = '#141936';
const BORDER = '#262c52';
const TEXT = '#d6dcf5';
const DIM = '#8590c4';
const PERIWINKLE = '#a5b4fc';
const VIOLET = '#c084fc';
const AMBER = '#ffb454';

const N = (value: unknown): number => (value == null ? 0 : Number(value));

export default function TaxiRevenueDive() {
	const summary = useSQLQuery(SUMMARY_SQL);
	const daily = useSQLQuery(DAILY_SQL);
	const payments = useSQLQuery(PAYMENTS_SQL);

	const totals = (Array.isArray(summary.data) ? summary.data : [])[0] ?? {};
	const series = (Array.isArray(daily.data) ? daily.data : []).map((row) => ({
		day: String(row.day).slice(8),
		revenue: N(row.revenue),
	}));
	const methods = (Array.isArray(payments.data) ? payments.data : []).slice(0, 5);
	const low = series.reduce(
		(min, point) => (point.revenue < min.revenue ? point : min),
		series[0] ?? { day: '--', revenue: 0 },
	);

	const kpis = [
		{ label: 'revenue', value: `$${(N(totals.revenue) / 1e6).toFixed(2)}M` },
		{ label: 'trips', value: `${(N(totals.trips) / 1e6).toFixed(2)}M` },
		{ label: 'avg_fare', value: `$${N(totals.avg_fare).toFixed(2)}` },
		{ label: 'tip_rate', value: `${N(totals.tip_rate).toFixed(1)}%` },
	];

	return (
		<div
			className="p-6 font-mono text-sm"
			style={{ background: BG, color: TEXT, minHeight: '100vh' }}
		>
			<div className="flex items-baseline justify-between">
				<h1 className="text-base uppercase tracking-wider">
					nyc_taxi / revenue
				</h1>
				<p className="text-xs uppercase" style={{ color: DIM }}>
					2022-11-01 → 2022-11-30
				</p>
			</div>

			<div className="grid grid-cols-4 gap-3 mt-4">
				{kpis.map((kpi) => (
					<div
						key={kpi.label}
						className="p-3"
						style={{ background: PANEL, border: `1px solid ${BORDER}` }}
					>
						<p className="text-xs uppercase" style={{ color: DIM }}>
							{kpi.label}
						</p>
						<p
							className="text-2xl tabular-nums mt-1"
							style={{ color: PERIWINKLE }}
						>
							{kpi.value}
						</p>
					</div>
				))}
			</div>

			<p className="text-xs uppercase mt-5 mb-1" style={{ color: VIOLET }}>
				[ revenue_per_day · usd_millions ]
			</p>
			<div
				className="p-2"
				style={{ background: PANEL, border: `1px solid ${BORDER}` }}
			>
				<ResponsiveContainer width="100%" height={170}>
					<LineChart data={series} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
						<CartesianGrid strokeDasharray="1 3" stroke={BORDER} />
						<XAxis
							dataKey="day"
							interval={4}
							tickLine={false}
							axisLine={{ stroke: BORDER }}
							tick={{ fontSize: 10, fill: DIM, fontFamily: 'monospace' }}
						/>
						<YAxis
							width={30}
							tickCount={4}
							tickLine={false}
							axisLine={false}
							tick={{ fontSize: 10, fill: DIM, fontFamily: 'monospace' }}
							tickFormatter={(value) => (value / 1e6).toFixed(1)}
						/>
						<Line
							type="linear"
							dataKey="revenue"
							stroke={PERIWINKLE}
							strokeWidth={1.5}
							dot={false}
						/>
					</LineChart>
				</ResponsiveContainer>
			</div>
			<p className="text-xs mt-1" style={{ color: AMBER }}>
				! min 11-{low.day} ${(low.revenue / 1e6).toFixed(2)}M
			</p>

			<p className="text-xs uppercase mt-5 mb-1" style={{ color: VIOLET }}>
				[ revenue_by_payment_method ]
			</p>
			<table className="w-full text-xs tabular-nums">
				<thead>
					<tr
						className="text-left uppercase"
						style={{ color: DIM, borderBottom: `1px solid ${BORDER}` }}
					>
						<th className="font-normal py-1">method</th>
						<th className="font-normal py-1 text-right">revenue</th>
						<th className="font-normal py-1 text-right">share</th>
						<th className="font-normal py-1 text-right">avg_fare</th>
					</tr>
				</thead>
				<tbody>
					{methods.map((row) => (
						<tr
							key={String(row.payment_method)}
							style={{ borderBottom: `1px solid ${BORDER}` }}
						>
							<td className="py-1">
								{String(row.payment_method).toLowerCase().replace(' ', '_')}
							</td>
							<td className="py-1 text-right">
								${(N(row.revenue) / 1e6).toFixed(2)}M
							</td>
							<td className="py-1 text-right">
								{N(row.revenue_share).toFixed(1)}%
							</td>
							<td className="py-1 text-right">${N(row.avg_fare).toFixed(2)}</td>
						</tr>
					))}
				</tbody>
			</table>

			<p className="text-xs mt-4" style={{ color: DIM }}>
				src sample_data.nyc.taxi · rows {N(totals.trips).toLocaleString()} · ok
			</p>
		</div>
	);
}
