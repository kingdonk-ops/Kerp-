/** English (default) message catalogue. Every user-visible string lives here. */
export const en = {
  app: {
    name: "KERP",
    skipToContent: "Skip to main content",
  },
  home: {
    title: "Field Operations Platform",
    intro: "Select a project to get started.",
    queryStatus: {
      loading: "Loading…",
      ready: "Ready",
    },
  },
  notFound: {
    title: "Page not found",
    body: "The page you requested does not exist.",
    backHome: "Back to home",
  },
} as const;
