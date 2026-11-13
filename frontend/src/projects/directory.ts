/** A project the signed-in user can switch to. */
export interface ProjectSummary {
  id: string;
  code: string;
  name: string;
}

/**
 * Source of the user's projects. The seam exists because the projects/membership API is
 * not built yet (T-0014, T-0010); until then `pendingProjectDirectory` lists none.
 */
export interface ProjectDirectory {
  listMyProjects(): Promise<ProjectSummary[]>;
}

export const pendingProjectDirectory: ProjectDirectory = {
  listMyProjects: () => Promise.resolve([]),
};

export function staticProjectDirectory(projects: ProjectSummary[]): ProjectDirectory {
  return { listMyProjects: () => Promise.resolve(projects) };
}
