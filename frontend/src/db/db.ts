import Dexie, { type EntityTable } from "dexie";

/** Name of the offline outbox table (master spec §1.3 `outbox_mutations`). */
export const OUTBOX_TABLE = "outbox_mutations";

export const DEFAULT_DB_NAME = "kerp";

export type OutboxOperation = "CREATE" | "UPDATE" | "DELETE";

export type OutboxStatus = "pending" | "sending" | "failed";

/**
 * One queued offline mutation. Keyed on the client-generated `client_op_id`
 * (consolidation §6) so that enqueue and server replay are idempotent.
 * Drain / replay, vector clocks and conflict handling arrive with T-0126/T-0127.
 */
export interface OutboxMutation {
  client_op_id: string;
  tenant_id: string;
  user_id: string;
  entity_name: string;
  entity_id: string;
  operation: OutboxOperation;
  payload: Record<string, unknown>;
  /** ISO-8601 UTC timestamp of when the mutation was queued. */
  created_at_utc: string;
  status: OutboxStatus;
  attempts: number;
  last_error: string | null;
}

/** Fields the caller supplies; bookkeeping fields are set by `enqueueMutation`. */
export type NewOutboxMutation = Omit<
  OutboxMutation,
  "created_at_utc" | "status" | "attempts" | "last_error"
>;

export class KerpDb extends Dexie {
  outbox_mutations!: EntityTable<OutboxMutation, "client_op_id">;

  constructor(name: string = DEFAULT_DB_NAME) {
    super(name);
    this.version(1).stores({
      // Primary key first (non-auto, unique), then secondary indexes.
      [OUTBOX_TABLE]: "client_op_id, status, created_at_utc, [status+created_at_utc]",
    });
  }
}

let shared: KerpDb | undefined;

/** Lazily created application-wide database instance. */
export function getDb(): KerpDb {
  shared ??= new KerpDb();
  return shared;
}
