// @ts-nocheck
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatDate, formatStopwatch } from "../formatters";

export interface PortalRun {
  id: number;
  run_number: number;
  session_date: string;
  started_at: string;
  actual_time_ms: number;
  added_time_ms: number;
  total_time_ms: number;
  updated_at?: string;
}

interface PortalRunsChartProps {
  runs: PortalRun[];
  bestTotalMs: number | null;
}

function PortalRunsChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="portal-chart-tooltip">
      <div className="portal-chart-tooltip-label">{label}</div>
      <div>
        <span className="portal-chart-tooltip-swatch" style={{ background: "var(--green)" }} />
        Ist-Zeit {formatStopwatch(point.actualMs)}
      </div>
      <div>
        <span className="portal-chart-tooltip-swatch" style={{ background: "var(--amber)" }} />
        Zuschlag {formatStopwatch(point.addedMs)}
      </div>
      <div className="portal-chart-tooltip-total">Gesamtzeit {formatStopwatch(point.totalMs)}</div>
    </div>
  );
}

export function PortalRunsChart({ runs, bestTotalMs }: PortalRunsChartProps) {
  const points = [...runs]
    .sort((a, b) => a.started_at.localeCompare(b.started_at))
    .map((run) => ({
      id: run.id,
      label: formatDate(run.session_date),
      actualSeconds: run.actual_time_ms / 1000,
      addedSeconds: run.added_time_ms / 1000,
      actualMs: run.actual_time_ms,
      addedMs: run.added_time_ms,
      totalMs: run.total_time_ms,
    }));

  if (!points.length) {
    return <div className="chart-empty">Keine Läufe im gewählten Zeitraum.</div>;
  }

  return (
    <div className="portal-chart">
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={points} margin={{ top: 10, right: 18, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={{ stroke: "var(--line)" }} tickLine={false} />
          <YAxis
            tick={{ fill: "var(--muted)", fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(seconds: number) => formatStopwatch(seconds * 1000)}
            width={70}
          />
          <Tooltip cursor={{ fill: "var(--line)", opacity: 0.4 }} content={<PortalRunsChartTooltip />} />
          <Legend
            verticalAlign="top"
            align="right"
            height={28}
            iconType="circle"
            formatter={(value: string) => <span style={{ color: "var(--muted)" }}>{value}</span>}
          />
          {bestTotalMs != null && (
            <ReferenceLine
              y={bestTotalMs / 1000}
              stroke="var(--green)"
              strokeDasharray="4 4"
              label={{ value: `BESTZEIT ${formatStopwatch(bestTotalMs)}`, position: "insideTopRight", fill: "var(--green)", fontSize: 12 }}
            />
          )}
          <Bar dataKey="actualSeconds" stackId="run" name="Ist-Zeit" fill="var(--green)" />
          <Bar dataKey="addedSeconds" stackId="run" name="Zuschlag" fill="var(--amber)" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
