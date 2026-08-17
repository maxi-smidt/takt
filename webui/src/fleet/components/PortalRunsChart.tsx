// @ts-nocheck
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatDate, formatStopwatch } from "../formatters";

interface PortalRun {
  id: number;
  session_date: string;
  started_at: string;
  total_time_ms: number;
}

interface PortalRunsChartProps {
  runs: PortalRun[];
  bestTotalMs: number | null;
}

export function PortalRunsChart({ runs, bestTotalMs }: PortalRunsChartProps) {
  const points = [...runs]
    .sort((a, b) => a.started_at.localeCompare(b.started_at))
    .map((run) => ({
      id: run.id,
      label: formatDate(run.session_date),
      seconds: run.total_time_ms / 1000,
    }));

  if (!points.length) {
    return <div className="chart-empty">Keine Läufe im gewählten Zeitraum.</div>;
  }

  return (
    <div className="portal-chart">
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={points} margin={{ top: 10, right: 18, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={{ stroke: "var(--line)" }} tickLine={false} />
          <YAxis
            tick={{ fill: "var(--muted)", fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(seconds: number) => formatStopwatch(seconds * 1000)}
            width={70}
          />
          <Tooltip
            contentStyle={{ background: "#101918", border: "1px solid var(--line)", color: "var(--text)" }}
            labelStyle={{ color: "var(--muted)" }}
            formatter={(seconds: number) => [formatStopwatch(seconds * 1000), "Gesamtzeit"]}
          />
          {bestTotalMs != null && (
            <ReferenceLine
              y={bestTotalMs / 1000}
              stroke="var(--green)"
              strokeDasharray="4 4"
              label={{ value: `BESTZEIT ${formatStopwatch(bestTotalMs)}`, position: "insideTopRight", fill: "var(--green)", fontSize: 12 }}
            />
          )}
          <Line type="monotone" dataKey="seconds" stroke="var(--green)" strokeWidth={2} dot={{ r: 3, fill: "var(--green)" }} activeDot={{ r: 5 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
