/**
 * TypeScript port of clearance_core/verdict.py::decide_verdict, used in offline mode so the
 * bundled sample reports get their verdicts from the same rules as the server. Only code sets verdicts.
 */
import type { LegFeasibility, Verdict, VerdictCheck } from "../types";

export interface VerdictInputs {
  legs: LegFeasibility[];
  flagged_thresholds?: string[];
  success_rate?: number | null;
  fact_ids?: { legs?: string[]; thresholds?: string[]; success?: string[] };
  changes?: Record<string, unknown>[];
}

export const DEFAULT_VERDICT_CONFIG = { success_min: 0.9, density: "normal", bound: "conservative" };

const pctStr = (x: number) => `${Math.round(x * 100)}%`;

function checks(inp: VerdictInputs, smin: number, assumeMeasured = false): VerdictCheck[] {
  const blocked = inp.legs.filter((l) => l.reachable === false);
  const unknown = inp.legs.filter((l) => l.reachable === null);
  const flagged = inp.flagged_thresholds ?? [];
  let legsOk: boolean | null;
  if (blocked.length) legsOk = false;
  else if (unknown.length && !assumeMeasured) legsOk = null;
  else legsOk = inp.legs.length > 0;
  const sr = inp.success_rate ?? null;
  return [
    {
      rule: "every task leg feasible on the conservative geometry bound",
      passed: legsOk,
      value: `${inp.legs.length - blocked.length - unknown.length}/${inp.legs.length} legs`,
      threshold: null,
      fact_ids: inp.fact_ids?.legs ?? [],
    },
    {
      rule: "no unresolved flagged_manual thresholds on the route",
      passed: !flagged.length || assumeMeasured ? true : null,
      value: flagged.join(", ") || "none",
      threshold: null,
      fact_ids: inp.fact_ids?.thresholds ?? [],
    },
    {
      rule: `simulated success at normal traffic (conservative bound) >= ${smin.toFixed(2)}`,
      passed: sr === null ? null : sr >= smin,
      value: sr,
      threshold: smin,
      fact_ids: inp.fact_ids?.success ?? [],
    },
  ];
}

const cleared = (c: VerdictCheck[]) => c.every((x) => x.passed === true);

function mainReason(inp: VerdictInputs, smin: number): string {
  for (const leg of inp.legs) {
    if (leg.reachable === false) {
      if (leg.blocking_threshold) return `threshold ${leg.blocking_threshold} on ${leg.from_wp}->${leg.to_wp} exceeds the robot's limit`;
      if (leg.narrowest_gap_m != null && leg.required_gap_m != null)
        return `no path ${leg.from_wp}->${leg.to_wp}: narrowest gap ${leg.narrowest_gap_m.toFixed(2)} m vs ${leg.required_gap_m.toFixed(2)} m needed`;
      return `no path ${leg.from_wp}->${leg.to_wp}` + (leg.reason ? `: ${leg.reason}` : "");
    }
  }
  const flagged = inp.flagged_thresholds ?? [];
  if (flagged.length) return `threshold ${flagged.join(", ")} must be measured manually`;
  if (inp.legs.some((l) => l.reachable === null)) return "route feasibility unknown";
  if (inp.success_rate == null) return "not simulated at normal traffic on the conservative bound";
  if (inp.success_rate < smin) return `success ${pctStr(inp.success_rate)} at normal traffic (needs ${pctStr(smin)})`;
  return "meets every rule";
}

export function decideVerdict(
  robotId: string,
  asIs: VerdictInputs,
  withChanges: VerdictInputs | null = null,
  config: { success_min: number } & Record<string, unknown> = DEFAULT_VERDICT_CONFIG,
): Verdict {
  const smin = Number(config.success_min);
  const asIsChecks = checks(asIs, smin);
  if (cleared(asIsChecks)) return { robot_id: robotId, label: "Cleared", main_reason: "meets every rule as-is", as_is: asIsChecks, with_changes: [], required_changes: [], config };

  const candidates: [VerdictInputs, boolean][] = [];
  if (asIs.flagged_thresholds?.length) candidates.push([asIs, true]);
  if (withChanges) {
    candidates.push([withChanges, false]);
    if (withChanges.flagged_thresholds?.length) candidates.push([withChanges, true]);
  }
  for (const [inp, measured] of candidates) {
    const c = checks(inp, smin, measured);
    if (!cleared(c)) continue;
    const isWc = inp === withChanges;
    const required: Record<string, unknown>[] = isWc ? [...(inp.changes ?? [])] : [];
    if (measured) for (const t of inp.flagged_thresholds ?? []) required.push({ measure_threshold: t });
    const parts: string[] = [];
    if (isWc && inp.changes?.length) parts.push(`${inp.changes.length} layout change(s)`);
    if (measured) parts.push(`measuring threshold ${(inp.flagged_thresholds ?? []).join(", ")} (cleared only if it is within the robot's limit)`);
    return {
      robot_id: robotId,
      label: "Cleared with changes",
      main_reason: `as-is: ${mainReason(asIs, smin)}; cleared after ${parts.join(" and ")}`,
      as_is: asIsChecks,
      with_changes: c,
      required_changes: required,
      config,
    };
  }
  return {
    robot_id: robotId,
    label: "Not cleared",
    main_reason: mainReason(asIs, smin),
    as_is: asIsChecks,
    with_changes: withChanges ? checks(withChanges, smin) : [],
    required_changes: [],
    config,
  };
}
