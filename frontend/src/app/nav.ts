/**
 * Global information architecture (diff §6): 8 navigation groups — the 4 industrial
 * pillars plus Backbone, Overview, Documents and Admin. Module screens register under
 * these groups as they are built.
 */
export const NAV_GROUP_IDS = [
  "overview",
  "logistics",
  "execution",
  "defenseClaims",
  "controlsInsights",
  "backbone",
  "documents",
  "admin",
] as const;

export type NavGroupId = (typeof NAV_GROUP_IDS)[number];

export interface NavGroup {
  id: NavGroupId;
  /** Route path of the group's landing page. */
  path: string;
  /** One of the 4 industrial pillars. */
  pillar: boolean;
}

export const NAV_GROUPS: readonly NavGroup[] = [
  { id: "overview", path: "/", pillar: false },
  { id: "logistics", path: "/logistics", pillar: true },
  { id: "execution", path: "/execution", pillar: true },
  { id: "defenseClaims", path: "/defense-claims", pillar: true },
  { id: "controlsInsights", path: "/controls-insights", pillar: true },
  { id: "backbone", path: "/backbone", pillar: false },
  { id: "documents", path: "/documents", pillar: false },
  { id: "admin", path: "/admin", pillar: false },
];

/** Route of the project lobby: find, request or join a project (diff §4.1; screens T-0041). */
export const LOBBY_PATH = "/lobby";

/**
 * Navigation items per group: the mapping of diff §6.1. An item shows in the navigation only
 * once its screen exists, i.e. once it has a `path`; the module task that ships the screen
 * adds it. Items may move between groups later (diff §6.1).
 */
export const NAV_ITEMS = [
  { group: "overview", id: "dashboards" },
  { group: "overview", id: "myWork" },
  { group: "backbone", id: "projects" },
  { group: "backbone", id: "assetHierarchy" },
  { group: "backbone", id: "items" },
  { group: "backbone", id: "teams" },
  { group: "execution", id: "inspections" },
  { group: "execution", id: "itps" },
  { group: "execution", id: "fieldTime" },
  { group: "execution", id: "progress" },
  { group: "execution", id: "lookAhead" },
  { group: "logistics", id: "materials" },
  { group: "logistics", id: "equipment" },
  { group: "logistics", id: "storeman" },
  { group: "documents", id: "documents" },
  { group: "documents", id: "transmittals" },
  { group: "documents", id: "cdrl" },
  { group: "documents", id: "drawings" },
  { group: "defenseClaims", id: "tqs" },
  { group: "defenseClaims", id: "variations" },
  { group: "defenseClaims", id: "nodEot" },
  { group: "defenseClaims", id: "claims" },
  { group: "controlsInsights", id: "p6Schedule" },
  { group: "controlsInsights", id: "reporting" },
  { group: "controlsInsights", id: "moneyMirror" },
  { group: "admin", id: "users" },
  { group: "admin", id: "personas" },
  { group: "admin", id: "system" },
  { group: "admin", id: "accessRequests" },
] as const satisfies readonly { group: NavGroupId; id: string }[];

export type NavItemId = (typeof NAV_ITEMS)[number]["id"];

export interface NavItem {
  id: NavItemId;
  group: NavGroupId;
  /** Route of the item's screen; absent while the screen is not built yet. */
  path?: string;
}

/** The items of `group` whose screens exist, in mapping order. */
export function registeredNavItems(
  group: NavGroupId,
  items: readonly NavItem[] = NAV_ITEMS,
): (NavItem & { path: string })[] {
  return items.filter(
    (entry): entry is NavItem & { path: string } =>
      entry.group === group && entry.path !== undefined,
  );
}
