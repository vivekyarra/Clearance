/**
 * Robot catalog in offline mode: the real catalog/robots/*.json specs (with their cited vendor sources and review
 * status), as served by GET /catalog/robots?all=1 when the fixtures were generated.
 */
import type { RobotSpec } from "../types";
import { INDEX } from "./fixtures";

export const CATALOG_ROBOTS: RobotSpec[] = INDEX.robots;

export const robotWidth = (r: RobotSpec) => 2 * Math.max(0.2, ...(r.footprint_polygon_m ?? []).map(([, y]) => Math.abs(y)));
