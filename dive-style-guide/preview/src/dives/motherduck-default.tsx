/**
 * MotherDuck defaults — no `dives` Guide installed.
 *
 * Follows the built-in design guidance verbatim: #0777b3 primary, #f8f8f8 page,
 * #231f20 text, four KPIs across, no card chrome, dashed grid, linear line.
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
		{ label: 'Total Revenue', value: `$${(N(totals.revenue) / 1e6).toFixed(1)}M` },
		{ label: 'Trips', value: `${(N(totals.trips) / 1e6).toFixed(2)}M` },
		{ label: 'Avg Fare', value: `$${N(totals.avg_fare).toFixed(2)}` },
		{ label: 'Tip Rate', value: `${N(totals.tip_rate).toFixed(1)}%` },
	];

	return (
		<div className="p-6" style={{ background: '#f8f8f8', minHeight: '100vh' }}>
			<h1 className="text-2xl font-semibold" style={{ color: '#231f20' }}>
				Yellow Taxi Revenue
			</h1>
			<p className="text-sm mb-6" style={{ color: '#6a6a6a' }}>
				November 2022
			</p>

			<div className="grid grid-cols-4 gap-8 mb-6">
				{kpis.map((kpi) => (
					<div key={kpi.label}>
						<p className="text-5xl font-bold" style={{ color: '#231f20' }}>
							{kpi.value}
						</p>
						<p className="text-sm mt-2" style={{ color: '#6a6a6a' }}>
							{kpi.label}
						</p>
					</div>
				))}
			</div>

			<h2 className="text-sm font-semibold mb-2" style={{ color: '#231f20' }}>
				Daily revenue
			</h2>
			<ResponsiveContainer width="100%" height={190}>
				<LineChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
					<CartesianGrid strokeDasharray="3 3" stroke="#eee" />
					<XAxis dataKey="day" fontSize={11} stroke="#6a6a6a" interval={4} />
					<YAxis
						fontSize={11}
						stroke="#6a6a6a"
						tickFormatter={(value) => `$${(value / 1e6).toFixed(1)}M`}
					/>
					<Line
						type="linear"
						dataKey="revenue"
						stroke="#0777b3"
						strokeWidth={2}
						dot={false}
					/>
				</LineChart>
			</ResponsiveContainer>

			<h2 className="text-sm font-semibold mt-6 mb-2" style={{ color: '#231f20' }}>
				Revenue by payment method
			</h2>
			<table className="w-full text-sm" style={{ color: '#231f20' }}>
				<thead>
					<tr className="text-left" style={{ color: '#6a6a6a' }}>
						<th className="font-normal py-1">Payment method</th>
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
		</div>
	);
}
