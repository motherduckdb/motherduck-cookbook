/**
 * Swiss minimal — written from `styles/swiss-minimal.sql`.
 *
 * White field, black ink, one red accent. Rules instead of cards, uppercase
 * micro-labels, three KPIs, no gridlines.
 */
import { Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { useSQLQuery } from '@motherduck/react-sql-query';
import { DAILY_SQL, PAYMENTS_SQL, SUMMARY_SQL } from '../queries';

export const REQUIRED_DATABASES = [
	{
		type: 'share',
		path: 'md:_share/sample_data/23b0d623-1361-421d-ae77-62d701d471e6',
		alias: 'sample_data',
	},
];

const INK = '#111111';
const RED = '#d8232a';
const MUTED = '#757575';
const RULE = '#d4d4d4';

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
	const methods = (Array.isArray(payments.data) ? payments.data : []).slice(0, 4);

	const kpis = [
		{ label: 'Revenue', value: `$${(N(totals.revenue) / 1e6).toFixed(1)}M` },
		{ label: 'Trips', value: `${(N(totals.trips) / 1e6).toFixed(2)}M` },
		{ label: 'Tip rate', value: `${N(totals.tip_rate).toFixed(1)}%` },
	];

	return (
		<div
			className="p-6 font-sans"
			style={{ background: '#ffffff', color: INK, minHeight: '100vh' }}
		>
			<div style={{ borderTop: `4px solid ${INK}` }} className="pt-3">
				<div className="flex items-baseline justify-between">
					<h1 className="text-3xl font-bold tracking-tight">
						Yellow Taxi Revenue
					</h1>
					<p
						className="text-xs uppercase tracking-widest"
						style={{ color: MUTED }}
					>
						Nov 2022
					</p>
				</div>
			</div>

			<div
				className="grid grid-cols-3 mt-5"
				style={{ borderTop: `1px solid ${RULE}` }}
			>
				{kpis.map((kpi, index) => (
					<div
						key={kpi.label}
						className="pt-3 pb-4"
						style={{
							paddingLeft: index === 0 ? 0 : 24,
							borderLeft: index === 0 ? 'none' : `1px solid ${RULE}`,
						}}
					>
						<p
							className="text-xs uppercase tracking-widest mb-3"
							style={{ color: MUTED }}
						>
							{kpi.label}
						</p>
						<p className="text-4xl font-normal tabular-nums tracking-tight">
							{kpi.value}
						</p>
					</div>
				))}
			</div>

			<div style={{ borderTop: `1px solid ${RULE}` }} className="pt-4">
				<p className="text-xs uppercase tracking-widest mb-2" style={{ color: MUTED }}>
					Revenue per day — USD millions
				</p>
				<ResponsiveContainer width="100%" height={168}>
					<LineChart data={series} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
						<XAxis
							dataKey="day"
							interval={4}
							tickLine={false}
							axisLine={{ stroke: INK }}
							tick={{ fontSize: 10, fill: MUTED }}
						/>
						<YAxis
							width={32}
							tickLine={false}
							axisLine={false}
							tickCount={4}
							tick={{ fontSize: 10, fill: MUTED }}
							tickFormatter={(value) => (value / 1e6).toFixed(1)}
						/>
						<Line
							type="linear"
							dataKey="revenue"
							stroke={RED}
							strokeWidth={1.5}
							dot={false}
						/>
					</LineChart>
				</ResponsiveContainer>
			</div>

			<div className="mt-5">
				<p className="text-xs uppercase tracking-widest mb-2" style={{ color: MUTED }}>
					Revenue by payment method
				</p>
				<table className="w-full text-sm tabular-nums">
					<thead>
						<tr
							className="text-xs uppercase tracking-widest text-left"
							style={{ color: MUTED, borderBottom: `1px solid ${INK}` }}
						>
							<th className="font-normal py-1">Method</th>
							<th className="font-normal py-1 text-right">Revenue</th>
							<th className="font-normal py-1 text-right">Share</th>
							<th className="font-normal py-1 text-right">Avg fare</th>
						</tr>
					</thead>
					<tbody>
						{methods.map((row) => (
							<tr
								key={String(row.payment_method)}
								style={{ borderBottom: `1px solid ${RULE}` }}
							>
								<td className="py-1">{String(row.payment_method)}</td>
								<td className="py-1 text-right">
									${(N(row.revenue) / 1e6).toFixed(1)}M
								</td>
								<td className="py-1 text-right">
									{N(row.revenue_share).toFixed(1)}%
								</td>
								<td className="py-1 text-right">
									${N(row.avg_fare).toFixed(2)}
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>
		</div>
	);
}
