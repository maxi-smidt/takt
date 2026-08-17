// @ts-nocheck
import { healthTone } from "../maintenanceActions.js";
import { timeAgo } from "../formatters";

export function HealthChecks({ healthChecks }) {
  if (!healthChecks?.checks?.length) return null;
  const { summary, checks, collected_at: collectedAt } = healthChecks;
  return (
    <details className="health-panel">
      <summary>
        <span className={`health-dot tone-${healthTone(healthChecks)}`} />
        HEALTH {summary.fail} FAILED · {summary.warn} WARNING · {summary.ok} OK
        <small>{timeAgo(collectedAt)}</small>
      </summary>
      <ul>
        {checks.map((check) => (
          <li key={check.id} className={`tone-${check.status}`}>
            <span>{check.label || check.id}</span>
            <strong>{check.status.toUpperCase()}</strong>
            <small>{check.detail}</small>
          </li>
        ))}
      </ul>
    </details>
  );
}
