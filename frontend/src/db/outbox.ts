import { Dexie } from "dexie";

import type { KerpDb, NewOutboxMutation, OutboxMutation } from "./db";

/**
 * Queue a mutation in the offline outbox.
 *
 * Idempotent on `client_op_id`: if an entry with the same id already exists it
 * is returned unchanged and nothing is written.
 */
export async function enqueueMutation(
  db: KerpDb,
  mutation: NewOutboxMutation,
  now: () => Date = () => new Date(),
): Promise<OutboxMutation> {
  if (!mutation.client_op_id) {
    throw new Error("client_op_id is required");
  }
  return db.transaction("rw", db.outbox_mutations, async () => {
    const existing = await db.outbox_mutations.get(mutation.client_op_id);
    if (existing) {
      return existing;
    }
    const entry: OutboxMutation = {
      ...mutation,
      created_at_utc: now().toISOString(),
      status: "pending",
      attempts: 0,
      last_error: null,
    };
    await db.outbox_mutations.add(entry);
    return entry;
  });
}

/** Pending mutations in the order they were queued (oldest first). */
export function listPendingMutations(db: KerpDb): Promise<OutboxMutation[]> {
  return db.outbox_mutations
    .where("[status+created_at_utc]")
    .between(["pending", Dexie.minKey], ["pending", Dexie.maxKey])
    .toArray();
}
