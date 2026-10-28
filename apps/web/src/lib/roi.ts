/**
 * Port of the ROI rule in PLAN 7.11 (agents/report/roi.py is the source of truth in live mode).
 *   automatable share = success rate (nominal, normal traffic) x fraction of task legs feasible
 *   monthly saving    = staff cost/h x task hours/day x automatable share x days/month
 *   payback months    = price / (monthly saving - monthly running cost)
 */
import type { BusinessInputs, RoiResult } from "../types";

export const DAYS_PER_MONTH_FACTOR = 52 / 12;

export function computeRoi(b: BusinessInputs, price: number | null, monthlyLease: number | null, successRate: number, feasibleLegFrac: number): RoiResult {
  const share = Math.max(0, successRate) * Math.max(0, Math.min(1, feasibleLegFrac));
  const daysPerMonth = b.days_per_week * DAYS_PER_MONTH_FACTOR;
  const saving = b.staff_cost_per_h * b.task_hours_per_day * share * daysPerMonth;
  const maint = price != null ? (price * b.maintenance_pct_per_year) / 100 / 12 : 0;
  const running = maint + (monthlyLease ?? 0);
  const net = saving - running;
  let payback: number | null = null;
  if (price != null && net > 0) payback = price / net;
  const formula =
    price != null
      ? "payback_months = price / (staff_cost_per_h x task_hours_per_day x automatable_share x days_per_month - price x maintenance_pct / 100 / 12)"
      : monthlyLease != null
        ? "net_monthly = staff_cost_per_h x task_hours_per_day x automatable_share x days_per_month - monthly_lease"
        : "monthly_saving = staff_cost_per_h x task_hours_per_day x automatable_share x days_per_month (price on request: payback needs a quote)";
  return {
    monthly_saving: round2(saving),
    payback_months: payback === null ? null : round2(payback),
    formula,
    inputs: {
      staff_cost_per_h: b.staff_cost_per_h,
      task_hours_per_day: b.task_hours_per_day,
      days_per_week: b.days_per_week,
      days_per_month: round2(daysPerMonth),
      success_rate: successRate,
      feasible_leg_frac: feasibleLegFrac,
      automatable_share: round2(share),
      price,
      monthly_lease: monthlyLease,
      maintenance_pct_per_year: b.maintenance_pct_per_year,
      monthly_running_cost: round2(running),
      net_monthly: round2(net),
      currency: b.currency,
    },
  };
}

const round2 = (v: number) => Math.round(v * 100) / 100;
