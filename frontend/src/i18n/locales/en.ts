/** English (default) message catalogue. Every user-visible string lives here. */
export const en = {
  app: {
    name: "KERP",
    skipToContent: "Skip to main content",
    loading: "Loading…",
    loadError: "Could not reach the server.",
    retry: "Try again",
  },
  auth: {
    login: {
      title: "Sign in",
      email: "Email",
      password: "Password",
      submit: "Sign in",
      submitting: "Signing in…",
      invalid: "Email or password is incorrect.",
      locked: "Too many attempts. Please wait and try again.",
      failed: "Sign-in failed. Please try again.",
    },
    signedInAs: "Signed in as {{name}}",
    logout: "Sign out",
  },
  nav: {
    label: "Main navigation",
    pillar: "Pillar",
    groups: {
      overview: { title: "Overview", description: "Your work and project status at a glance." },
      logistics: {
        title: "Logistics",
        description: "Deliveries, gate receipt, materials and inventory.",
      },
      execution: {
        title: "Execution",
        description: "Workpacks, look-ahead, daily workface logs and progress.",
      },
      defenseClaims: {
        title: "Defense & Claims",
        description: "Delays, variations, notices and commercial defense.",
      },
      controlsInsights: {
        title: "Controls & Insights",
        description: "Quality, inspections, dashboards and analytics.",
      },
      backbone: {
        title: "Backbone",
        description: "Asset hierarchy, tags and reference data.",
      },
      documents: { title: "Documents", description: "Drawings, certificates and turnover packages." },
      admin: { title: "Admin", description: "Users, roles, modules and configuration." },
    },
    placeholder: "No screens in this area yet.",
  },
  scope: {
    label: "Project",
    allProjects: "All projects (portfolio)",
    noProjects: "No projects available",
    projectOption: "{{code}} — {{name}}",
    current: "Scope: {{scope}}",
  },
  theme: {
    outdoor: "Outdoor high contrast",
  },
  home: {
    welcome: "Welcome, {{name}}.",
  },
  shells: {
    register: {
      quickFilter: "Filter",
      columnFilter: "Filter {{column}}",
      columns: "Columns",
      count: "{{shown}} of {{total}} records",
      empty: "No records match the current filters.",
      exportCsv: "Export CSV",
      savedViews: {
        label: "Saved view",
        none: "Current view",
        name: "View name",
        save: "Save view",
        delete: "Delete view",
      },
    },
    detail: {
      tabs: "Sections",
      gate: {
        title: "Gate status",
        summary: "{{state}} — {{label}}",
        states: {
          open: "Open",
          passed: "Passed",
          hold: "On hold",
          blocked: "Blocked",
        },
      },
      audit: {
        tab: "Audit log",
        empty: "No audit entries yet.",
        loadError: "Could not load the audit log.",
        source: {
          ai: "AI",
        },
      },
    },
    form: {
      submit: "Submit",
      requiredMark: "required",
      errors: {
        required: "This field is required.",
        ruleError: "This rule could not be evaluated. Check the related fields.",
      },
    },
  },
  notFound: {
    title: "Page not found",
    body: "The page you requested does not exist.",
    backHome: "Back to home",
  },
} as const;
