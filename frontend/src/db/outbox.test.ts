import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { KerpDb, OUTBOX_TABLE, type NewOutboxMutation } from "./db";
import { enqueueMutation, listPendingMutations } from "./outbox";

// Synthetic identifiers only.
const TENANT = "00000000-0000-7000-8000-000000000001";
const USER = "00000000-0000-7000-8000-000000000002";

function mutation(clientOpId: string, overrides: Partial<NewOutboxMutation> = {}): NewOutboxMutation {
  return {
    client_op_id: clientOpId,
    tenant_id: TENANT,
    user_id: USER,
    entity_name: "synthetic_entity",
    entity_id: "00000000-0000-7000-8000-0000000000aa",
    operation: "UPDATE",
    payload: { note: "synthetic" },
    ...overrides,
  };
}

describe("KerpDb offline outbox", () => {
  let db: KerpDb;

  beforeEach(async () => {
    db = new KerpDb(`kerp-test-${crypto.randomUUID()}`);
    await db.open();
  });

  afterEach(async () => {
    db.close();
    await db.delete();
  });

  it("declares the outbox table with client_op_id as its primary key", () => {
    const table = db.table(OUTBOX_TABLE);
    expect(OUTBOX_TABLE).toBe("outbox_mutations");
    expect(table.schema.primKey.keyPath).toBe("client_op_id");
    expect(table.schema.primKey.auto).toBe(false);
  });

  it("stores an enqueued mutation as pending with zero attempts", async () => {
    const stored = await enqueueMutation(db, mutation("op-1"));

    expect(stored.status).toBe("pending");
    expect(stored.attempts).toBe(0);
    expect(stored.created_at_utc).toMatch(/Z$/);
    expect(await db.outbox_mutations.get("op-1")).toEqual(stored);
  });

  it("is idempotent on client_op_id: re-enqueueing keeps the original entry", async () => {
    const first = await enqueueMutation(db, mutation("op-dup", { payload: { v: 1 } }));
    const second = await enqueueMutation(db, mutation("op-dup", { payload: { v: 2 } }));

    expect(second).toEqual(first);
    expect(await db.outbox_mutations.count()).toBe(1);
    expect((await db.outbox_mutations.get("op-dup"))?.payload).toEqual({ v: 1 });
  });

  it("rejects a raw duplicate insert on the client_op_id key", async () => {
    await enqueueMutation(db, mutation("op-raw"));
    const again = await db.outbox_mutations.get("op-raw");
    expect(again).toBeDefined();
    await expect(db.outbox_mutations.add(again!)).rejects.toThrow();
  });

  it("lists pending mutations oldest first", async () => {
    await enqueueMutation(db, mutation("op-b"), () => new Date("2026-01-01T00:00:02.000Z"));
    await enqueueMutation(db, mutation("op-a"), () => new Date("2026-01-01T00:00:01.000Z"));
    await enqueueMutation(db, mutation("op-c"), () => new Date("2026-01-01T00:00:03.000Z"));
    await db.outbox_mutations.update("op-c", { status: "failed" });

    const pending = await listPendingMutations(db);
    expect(pending.map((m) => m.client_op_id)).toEqual(["op-a", "op-b"]);
  });

  it("requires a non-empty client_op_id", async () => {
    await expect(enqueueMutation(db, mutation(""))).rejects.toThrow(/client_op_id/);
  });
});
