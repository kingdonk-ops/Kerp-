// Accounts of Admin → Users (T-0014 API; screen T-0331, diff §4.1). System admins only.
import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";
import { AdminApiError } from "./api";

export type Account = components["schemas"]["UserRead"];

/** Every account of the deployment, by name (403 for everyone but System admins). */
export async function fetchAccounts(api: ApiClient): Promise<Account[]> {
  const { data, response } = await api.GET("/api/v1/admin/users");
  if (!data) throw new AdminApiError(response.status);
  return [...data].sort((a, b) => a.display_name.localeCompare(b.display_name));
}

/** Create an account without credentials; `duplicate`: the e-mail has one already (409). */
export async function createAccount(
  api: ApiClient,
  email: string,
  displayName: string,
): Promise<"ok" | "duplicate"> {
  const { response } = await api.POST("/api/v1/admin/users", {
    body: { email: email.trim(), display_name: displayName.trim() },
  });
  if (response.ok) return "ok";
  if (response.status === 409) return "duplicate";
  throw new AdminApiError(response.status);
}

/** Deactivate or reactivate; `last`: the last active System admin cannot be deactivated (409). */
export async function setAccountActive(
  api: ApiClient,
  userId: string,
  active: boolean,
): Promise<"ok" | "last"> {
  const { response } = await api.PATCH("/api/v1/admin/users/{user_id}", {
    params: { path: { user_id: userId } },
    body: { is_active: active },
  });
  if (response.ok) return "ok";
  if (response.status === 409) return "last";
  throw new AdminApiError(response.status);
}
