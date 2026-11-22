// Synthetic System console payloads (T-0224, T-0225 tests). No real client data.
import type { AuditEntry } from "./audit";
import type {
  FailedJob,
  QuarantineCount,
  QuarantineFile,
  QueueOverview,
  SystemHealth,
  SystemStats,
} from "./system";

export const PROJECT_A = "00000000-0000-4000-8000-0000000000a1";
export const PROJECT_B = "00000000-0000-4000-8000-0000000000b2";

export const STATS: SystemStats = {
  projects: { active: 12, soft_deleted: 2 },
  users: { active: 40, deactivated: 3 },
  devices: { active: 25, revoked: 4 },
  modules: { asset_management: { asset_nodes: 7 }, synthetic_module: { widgets: 5 } },
  object_store: {
    as_of: "2026-11-20T06:00:00Z",
    refreshing: false,
    storage_classes: [
      { storage_class: "STANDARD", objects: 120, bytes: 5 * 1024 * 1024 },
      { storage_class: "DEEP_ARCHIVE", objects: 3, bytes: 2048 },
    ],
  },
};

export const HEALTH: SystemHealth = {
  status: "degraded",
  checked_at: "2026-11-21T08:00:00Z",
  probes: [
    { name: "postgresql", status: "ok", latency_ms: 2.4 },
    { name: "redis", status: "ok", latency_ms: 1 },
    { name: "object_store", status: "degraded", latency_ms: 950 },
    { name: "malware_scanner", status: "down", latency_ms: null },
  ],
};

export const QUEUES: QueueOverview = {
  queues: [
    { name: "interactive", queued: 3, active: 1, scheduled: 0, failed: 1, oldest_queued_age_s: 12.5 },
    { name: "batch", queued: 0, active: 2, scheduled: 4, failed: 1, oldest_queued_age_s: null },
  ],
  cron_jobs: [
    {
      name: "uploads.sweep_orphans",
      schedule: "*/15 * * * *",
      last_run_at: "2026-11-21T07:45:00Z",
      last_finished_at: "2026-11-21T07:45:03Z",
      last_outcome: "succeeded",
      next_run_at: "2026-11-21T08:00:00Z",
    },
    {
      name: "jobs.sweep",
      schedule: "*/5 * * * *",
      last_run_at: null,
      last_finished_at: null,
      last_outcome: null,
      next_run_at: null,
    },
  ],
};

export const FAILED_JOB: FailedJob = {
  id: "00000000-0000-4000-8000-00000000f001",
  kind: "synthetic.render_pdf",
  queue: "batch",
  project_id: PROJECT_A,
  project_code: "SYN-A",
  attempts: 3,
  error_class: "TimeoutError",
  message: "Synthetic failure message",
  failed_at: "2026-11-21T07:00:00Z",
};

export const FAILED_TENANT_JOB: FailedJob = {
  ...FAILED_JOB,
  id: "00000000-0000-4000-8000-00000000f002",
  kind: "admin.object_store_totals",
  queue: "interactive",
  project_id: null,
  project_code: null,
  error_class: "Aborted",
  message: "Aborted by an administrator",
};

export function quarantineFile(extra: Partial<QuarantineFile> = {}): QuarantineFile {
  return {
    id: "00000000-0000-4000-8000-00000000c001",
    project_id: PROJECT_A,
    project_code: "SYN-A",
    module: "documents",
    filename: "synthetic-drawing.pdf",
    content_type: "application/pdf",
    size_bytes: 4096,
    sha256: "0".repeat(64),
    status: "verifying",
    reason: null,
    scan_signature: null,
    uploader_id: null,
    object_deleted: false,
    created_at: "2026-11-21T06:00:00Z",
    updated_at: "2026-11-21T06:30:00Z",
    ...extra,
  };
}

export const STUCK_FILE = quarantineFile();
export const INFECTED_FILE = quarantineFile({
  id: "00000000-0000-4000-8000-00000000c002",
  project_id: PROJECT_B,
  project_code: "SYN-B",
  filename: "synthetic-photo.jpg",
  content_type: "image/jpeg",
  status: "rejected",
  reason: "infected",
  scan_signature: "Synthetic.Test.Signature",
  object_deleted: true,
});

export const COUNTS: QuarantineCount[] = [
  { project_id: PROJECT_A, project_code: "SYN-A", status: "verifying", reason: null, count: 2 },
  { project_id: PROJECT_A, project_code: "SYN-A", status: "expired", reason: null, count: 1 },
  { project_id: PROJECT_B, project_code: "SYN-B", status: "rejected", reason: "infected", count: 4 },
  { project_id: PROJECT_B, project_code: "SYN-B", status: "rejected", reason: "size_mismatch", count: 1 },
];

// --- audit explorer (T-0225) ------------------------------------------------------------------

export const ACTOR = "00000000-0000-4000-8000-00000000e001";
export const DEVICE_ID = "00000000-0000-4000-8000-00000000e002";

export function auditEntry(extra: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: "00000000-0000-4000-8000-00000000a001",
    project_id: PROJECT_A,
    chain_key: "inspections:synthetic-001",
    seq: 4,
    created_at: "2026-11-21T07:00:00Z",
    actor_id: ACTOR,
    device_id: DEVICE_ID,
    source: "api",
    payload: { event: "inspection_signed", sod_warning: true },
    prev_hash: "a".repeat(64),
    content_hash: "b".repeat(64),
    hash_version: 1,
    ...extra,
  };
}

export const PROJECT_ENTRY = auditEntry();
export const TENANT_ENTRY = auditEntry({
  id: "00000000-0000-4000-8000-00000000a002",
  project_id: null,
  chain_key: "admin-system:synthetic",
  seq: 9,
  device_id: null,
  payload: { event: "legal_hold_set", reason: "Synthetic dispute" },
  prev_hash: "c".repeat(64),
  content_hash: "d".repeat(64),
});
