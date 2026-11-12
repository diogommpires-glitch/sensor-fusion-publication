export type MethodId = 'M1' | 'M2' | 'M3' | 'M4' | 'M5' | 'M6' | 'M7' | 'M8';
export type RunStatus =
  | 'starting'
  | 'queued'
  | 'running'
  | 'finalizing'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';
export function isActive(status: RunStatus) {
  return ['starting', 'queued', 'running', 'finalizing'].includes(status);
}
export interface FaultConfig {
  kind: 'none' | 'dropout' | 'range_bias' | 'delay' | 'imu_bias' | 'radar_shift';
  level: number;
  seed: number;
  start_s: number | null;
  duration_s: number;
  axis: 0 | 1 | 2;
  sensor_id: string | null;
}
export interface RunConfig {
  dataset_id: 'fixture' | 'roboloc-g' | 'miluv';
  session_id: string;
  method: MethodId;
  radar_sensor: 'AOP' | 'ISK0' | 'ISK1';
  calibration_id: string | null;
  q_cv: number;
  acceleration_noise: number;
  bias_diffusion: number;
  gate_probability: number;
  wls_window_s: number;
  radar_cluster_radius_m: number;
  radar_min_samples: number;
  output_hz: number;
  duration_s: number;
  seed: number;
  fault: FaultConfig;
  factors?: { propagation: 'cv' | 'inertial'; radar: boolean; gating: boolean } | null;
}
export interface Dataset {
  id: RunConfig['dataset_id'];
  name: string;
  status: string;
  reason?: string | null;
  sessions: { id: string; label: string; partition: string; status: string }[];
  [key: string]: unknown;
}
export interface Catalog {
  datasets: Dataset[];
  methods: { id: MethodId; name: string; modalities: string[] }[];
  calibrations: Record<string, unknown>[];
  limitations: string[];
  default_config: RunConfig;
}
export interface Run {
  run_id: string;
  status: RunStatus;
  progress?: number;
  message?: string;
  config: RunConfig;
  metrics?: Record<string, unknown> | null;
  provenance?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string;
  completed_at?: string | null;
}
export type Vector = [number, number, number];
export type VectorSample = [number | null, number | null, number | null] | null;
export function finiteVector(vector: VectorSample | undefined): vector is Vector {
  return (
    Array.isArray(vector) &&
    vector.length === 3 &&
    vector.every((value) => typeof value === 'number' && Number.isFinite(value))
  );
}
export interface Trajectory {
  times: number[];
  positions: VectorSample[];
  reference_positions: VectorSample[];
  covariance_diagonal: VectorSample[];
  position_covariances: (number[][] | null)[];
  confidence_ellipsoid_axes: (number[][] | null)[];
  confidence_ellipse_xy_axes: (number[][] | null)[];
  confidence_ellipse_xz_axes: (number[][] | null)[];
  confidence_probability: number;
  position_errors: (number | null)[];
  velocities: VectorSample[];
  biases: VectorSample[];
  anchors: { id: string; position: Vector }[];
  decimated: boolean;
  total_points: number;
}
export interface Innovation {
  t: number;
  modality: string;
  sensor_id: string;
  value: number[] | number;
  predicted: number[] | number;
  residual: number[] | number;
  nis: number | null;
  accepted: boolean;
  reason?: string;
}
export interface Diagnostics {
  innovations: Innovation[];
  metadata: Record<string, unknown>;
}
export interface RadarFrame {
  time: number;
  points: Vector[];
  centroid: Vector | null;
  sensor_id: string;
  frame: string;
}
export interface Studies {
  studies: { id: string; name?: string; status?: string; [key: string]: unknown }[];
  protocol: Record<string, unknown>;
  blockers: string[];
}

export interface Investigation {
  id: string;
  status: string;
  kind: 'exploratory_investigation';
  protocol_hash: string;
  protocol: Record<string, unknown>;
  runs: { key: string; session: string; condition: string; run_id: string; status: string }[];
  results: {
    macro_scope: string;
    macro_contrasts: {
      factors: string;
      order: number;
      contrast_rmse_m: number | null;
      complete_cells: boolean;
    }[];
    interpretation: Record<string, unknown>;
  } | null;
}

export interface HardwareCatalog {
  kind: 'processed_radar_range';
  dimension: 1;
  length_unit: 'm';
  physical_inventory_verified: boolean;
  blocking_reasons: string[];
  capabilities: Record<string, unknown>[];
  trials: {
    record_id: string;
    status: string;
    received_packets: number;
    fresh_observations: number;
  }[];
  studies: Record<string, unknown>[];
  schedule: {
    trial_id: string;
    session: string;
    nominal_distance_m: number | null;
    condition: string;
    duration_s: number;
    settling_s: number;
  }[];
  scope: string;
}

export interface ResearchCatalog {
  investigations: Investigation[];
  hardware: HardwareCatalog;
  historical_v1: Record<string, unknown>;
}

export interface HardwareReplay {
  metadata: {
    record_id: string;
    kind: 'processed_radar_range';
    dimension: 1;
    synthetic: boolean;
    reference_ready: boolean;
    reference: Record<string, unknown>;
    capability: Record<string, unknown>;
    scope: string;
  };
  observations: {
    time_s: number;
    distance_m: number | null;
    age_dist_ms: number | null;
    new_observation: boolean;
    clock_basis: string;
    reason: string;
  }[];
  dimension: 1;
  length_unit: 'm';
  scope: string;
}
