import type { RouteObject } from "react-router";

import { RequireAuth } from "../auth/RequireAuth";
import { HomePage } from "../pages/HomePage";
import { LoginPage } from "../pages/LoginPage";
import { NavGroupPage } from "../pages/NavGroupPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { Layout } from "./Layout";
import { NAV_GROUPS } from "./nav";

export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        path: "/",
        element: <Layout />,
        children: [
          { index: true, element: <HomePage /> },
          ...NAV_GROUPS.filter((group) => group.path !== "/").map((group) => ({
            path: group.path.slice(1),
            element: <NavGroupPage group={group.id} />,
          })),
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
