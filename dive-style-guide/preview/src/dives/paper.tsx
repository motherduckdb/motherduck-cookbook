/**
 * Paper — written from `styles/paper.sql`.
 *
 * Tufte's data-ink argument in an Anthropic-ish cream-and-clay palette. Serif
 * throughout, no gridlines, a mean reference line, and a marginal note instead
 * of a conclusion in the title.
 */
import {
	Line,
	LineChart,
	ReferenceLine,
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

const PAPER = '#faf9f5';
const INK = '#191919';
const CLAY = '#c96442';
const MUTED = '#6b6658';
const RULE = '#ddd8c9';

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
	const mean =
		series.length === 0
			? 0
			: series.reduce((sum, point) => sum + point.revenue, 0) / series.length;

	const kpis = [
		{ label: 'revenue', value: `$${(N(totals.revenue) / 1e6).toFixed(1)}M` },
		{ label: 'trips', value: `${(N(totals.trips) / 1e6).toFixed(2)}M` },
		{ label: 'tip rate', value: `${N(totals.tip_rate).toFixed(1)}%` },
	];

	return (
		<div
			className="p-8 font-serif"
			style={{ background: PAPER, color: INK, minHeight: '100vh' }}
		>
			<h1 className="text-3xl">Yellow taxi revenue</h1>
			<p className="text-base italic mt-1" style={{ color: MUTED }}>
				New York City, November 2022
			</p>

			<div className="flex gap-16 mt-6 mb-6">
				{kpis.map((kpi) => (
					<div key={kpi.label}>
						<p className="text-4xl tabular-nums">{kpi.value}</p>
						<p className="text-sm mt-1" style={{ color: MUTED }}>
							{kpi.label}
						</p>
					</div>
				))}
			</div>

			<p className="text-base">Revenue per day, USD millions</p>
			<ResponsiveContainer width="100%" height={160}>
				<LineChart data={series} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
					<XAxis
						dataKey="day"
						interval={6}
						tickLine={false}
						axisLine={false}
						tick={{ fontSize: 11, fill: MUTED, fontFamily: 'serif' }}
					/>
					<YAxis
						width={30}
						tickCount={3}
						tickLine={false}
						axisLine={false}
						tick={{ fontSize: 11, fill: MUTED, fontFamily: 'serif' }}
						tickFormatter={(value) => (value / 1e6).toFixed(1)}
					/>
					<ReferenceLine
						y={mean}
						stroke={MUTED}
						strokeDasharray="2 4"
						label={{
							value: `mean ${(mean / 1e6).toFixed(1)}`,
							position: 'insideTopLeft',
							fill: MUTED,
							fontSize: 11,
							fontFamily: 'serif',
						}}
					/>
					<Line
						type="linear"
						dataKey="revenue"
						stroke={CLAY}
						strokeWidth={1.5}
						dot={false}
					/>
				</LineChart>
			</ResponsiveContainer>
			<p className="text-sm italic mt-2" style={{ color: MUTED }}>
				The low point is Thanksgiving, 24 November.
			</p>

			<p className="text-base mt-5">Revenue by payment method</p>
			<table className="w-full text-sm tabular-nums mt-2">
				<thead>
					<tr
						className="text-left"
						style={{ color: MUTED, borderBottom: `1px solid ${RULE}` }}
					>
						<th className="font-normal italic py-1">Method</th>
						<th className="font-normal italic py-1 text-right">Revenue</th>
						<th className="font-normal italic py-1 text-right">Share</th>
						<th className="font-normal italic py-1 text-right">Avg fare</th>
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
			<div style={{ borderTop: `1px solid ${RULE}` }} className="mt-1" />
		</div>
	);
}
