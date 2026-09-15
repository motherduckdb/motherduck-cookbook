/**
 * Editorial — written from `styles/editorial.sql`.
 *
 * Financial Times conventions: FT pink stock, oxford blue bars with a claret
 * highlight on the point the headline names, a serif headline stating the
 * finding, a sans dek carrying the measure and units, the scale on the right,
 * and a source line.
 */
import {
	Bar,
	BarChart,
	CartesianGrid,
	Cell,
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

const PAPER = '#fff1e5';
const INK = '#33302e';
const OXFORD = '#0f5499';
const CLARET = '#990f3d';
const MUTED = '#66605c';
const RULE = '#cec6b4';

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
	const methods = (Array.isArray(payments.data) ? payments.data : []).slice(0, 3);
	const lowIndex = series.reduce(
		(low, point, index) => (point.revenue < series[low].revenue ? index : low),
		0,
	);

	const kpis = [
		{ label: 'Revenue', value: `$${(N(totals.revenue) / 1e6).toFixed(1)}M` },
		{ label: 'Trips', value: `${(N(totals.trips) / 1e6).toFixed(2)}M` },
		{ label: 'Average fare', value: `$${N(totals.avg_fare).toFixed(2)}` },
		{ label: 'Tip rate', value: `${N(totals.tip_rate).toFixed(1)}%` },
	];

	return (
		<div
			className="p-8"
			style={{ background: PAPER, color: INK, minHeight: '100vh' }}
		>
			<div style={{ background: CLARET, height: 5, width: 44 }} />
			<h1 className="font-serif text-3xl font-bold mt-3 leading-tight">
				Thanksgiving costs taxis a third of a Thursday
			</h1>
			<p className="text-sm mt-2" style={{ color: MUTED }}>
				New York City yellow taxi revenue, November 2022, USD millions
			</p>

			<div
				className="grid grid-cols-4 gap-6 mt-4 pt-3"
				style={{ borderTop: `1px solid ${RULE}` }}
			>
				{kpis.map((kpi) => (
					<div key={kpi.label}>
						<p className="text-3xl font-semibold tabular-nums" style={{ color: OXFORD }}>
							{kpi.value}
						</p>
						<p className="text-xs mt-1" style={{ color: MUTED }}>
							{kpi.label}
						</p>
					</div>
				))}
			</div>

			<p className="text-sm font-semibold mt-4">Revenue per day</p>
			<p className="text-xs mb-1" style={{ color: MUTED }}>
				Thanksgiving in claret
			</p>
			<ResponsiveContainer width="100%" height={168}>
				<BarChart data={series} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
					<CartesianGrid vertical={false} stroke={RULE} />
					<XAxis
						dataKey="day"
						interval={4}
						tickLine={false}
						axisLine={{ stroke: INK }}
						tick={{ fontSize: 10, fill: MUTED }}
					/>
					<YAxis
						orientation="right"
						width={34}
						tickCount={4}
						tickLine={false}
						axisLine={false}
						tick={{ fontSize: 10, fill: MUTED }}
						tickFormatter={(value) => (value / 1e6).toFixed(1)}
					/>
					<Bar dataKey="revenue" fill={OXFORD}>
						{series.map((point, index) => (
							<Cell
								key={point.day}
								fill={index === lowIndex ? CLARET : OXFORD}
							/>
						))}
					</Bar>
				</BarChart>
			</ResponsiveContainer>

			<p className="text-sm font-semibold mt-4">Revenue by payment method</p>
			<table className="w-full text-sm tabular-nums mt-1">
				<thead>
					<tr
						className="text-left text-xs"
						style={{ color: MUTED, borderBottom: `1px solid ${RULE}` }}
					>
						<th className="font-normal py-1">Method</th>
						<th className="font-normal py-1 text-right">Revenue</th>
						<th className="font-normal py-1 text-right">Share</th>
						<th className="font-normal py-1 text-right">Avg fare</th>
					</tr>
				</thead>
				<tbody>
					{methods.map((row) => (
						<tr key={String(row.payment_method)}>
							<td className="py-1">{String(row.payment_method)}</td>
							<td className="py-1 text-right">
								${(N(row.revenue) / 1e6).toFixed(1)}M
							</td>
							<td className="py-1 text-right">
								{N(row.revenue_share).toFixed(1)}%
							</td>
							<td className="py-1 text-right">${N(row.avg_fare).toFixed(2)}</td>
						</tr>
					))}
				</tbody>
			</table>

			<p
				className="text-xs mt-3 pt-2"
				style={{ color: MUTED, borderTop: `1px solid ${RULE}` }}
			>
				Source: sample_data.nyc.taxi
			</p>
		</div>
	);
}
