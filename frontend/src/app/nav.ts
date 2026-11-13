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
