export const FINGERS = [
  "L_THUMB",
  "L_INDEX",
  "L_MIDDLE",
  "L_RING",
  "L_PINKY",
  "R_THUMB",
  "R_INDEX",
  "R_MIDDLE",
  "R_RING",
  "R_PINKY",
] as const;

export type FingerCode = (typeof FINGERS)[number];

export type TreatmentCategory =
  | "SUPPLEMENT_BIOTIN"
  | "TOPICAL_OIL"
  | "STRUCTURAL_REPAIR_WRAP_GLUE"
  | "BUILDER_GEL_OVERLAY"
  | "GROOMING_TRIM"
  | "TRAUMA_NOTE";

export interface ImageLink {
  url: string;
  access: "sas" | "authenticated_route";
  expires_in_seconds: number | null;
}

export interface QualityMetrics {
  laplacian_var: number;
  glare_ratio: number;
  tilt_deg: number | null;
  passed: boolean;
  reasons: string[];
  hand_detected: boolean;
  handedness: string | null;
}

export interface QualityRejection {
  passed: false;
  reasons: string[];
  messages: string[];
  metrics: QualityMetrics;
}

export interface SessionRead {
  user: { id: string; email: string | null; display_name: string | null };
  auth_mode: "dev" | "entra";
  disclaimer: string;
  forecast_horizons_days: number[];
  timeline_slider: { min_days: number; max_days: number; step_days: number };
  quality_thresholds: {
    blur_laplacian_min: number;
    glare_ratio_max: number;
    tilt_deg_max: number;
  };
  ridge_score_insight_threshold: number;
  default_velocity_mm_day: number;
  population_velocity_mm_day: number;
}

export interface FingerSummary {
  finger_code: FingerCode;
  baseline_id: string | null;
  calibrated_width_mm: number | null;
  observed_velocity_mm_day: number | null;
  has_reference_white: boolean;
  baseline_image: ImageLink | null;
}

export interface MeshVertex {
  x: number;
  y: number;
  dx: number;
  dy: number;
  pinned: boolean;
}

export interface TimelineFeature {
  id: string;
  type: "crack" | "leukonychia";
  rel_x: number;
  dist_from_cuticle_mm: number;
  clipped: boolean;
  days_until_clear: number | null;
}

export interface TimelineFrame {
  t_days: number;
  kind: "history" | "today" | "forecast";
  capture_id: string | null;
  image: ImageLink | null;
  mask: ImageLink | null;
  delta_length_mm: number;
  nail_bed_length_mm: number | null;
  free_margin_length_mm: number | null;
  total_length_mm: number | null;
  mesh: MeshVertex[] | null;
  features: TimelineFeature[];
  synthesized_free_margin: boolean;
}

export interface WellnessInsight {
  code: string;
  message: string;
  disclaimer: string;
}

export interface TimelineRead {
  finger_code: FingerCode;
  disclaimer: string;
  insights: WellnessInsight[];
  v_observed_mm_day: number;
  v_effective_mm_day: number;
  alpha_tx: number;
  frames: TimelineFrame[];
}

export interface Treatment {
  id: string;
  user_id: string;
  started_at: string;
  ended_at: string | null;
  category: TreatmentCategory;
  target_fingers: string[];
  velocity_modifier: number;
  notes: string | null;
}

export interface TreatmentDraft {
  started_at: string;
  ended_at?: string | null;
  category: TreatmentCategory;
  target_fingers: string[];
  velocity_modifier: number;
  notes?: string | null;
}

export interface CaptureMeasurement {
  id: string;
  finger_code: FingerCode;
  nail_bed_length_mm: number | null;
  free_margin_length_mm: number | null;
  total_length_mm: number | null;
  mask_source: string | null;
  segmentation_mask: ImageLink | null;
  corrected_mask: ImageLink | null;
  aligned_image: ImageLink | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown,
  ) {
    super(typeof body === "object" && body && "detail" in body ? String((body as { detail: unknown }).detail) : `HTTP ${status}`);
  }
}

export function isQualityRejection(body: unknown): body is QualityRejection {
  if (!body || typeof body !== "object") return false;
  const record = body as Partial<QualityRejection>;
  return record.passed === false && Array.isArray(record.reasons) && Array.isArray(record.messages);
}
