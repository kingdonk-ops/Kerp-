import type { RouteObject } from "react-router";

import { AccessRequestsPage } from "../admin/AccessRequestsPage";
import { AdminDevicesPage, MyDevicesPage } from "../admin/DevicesPage";
import { RequireAuth } from "../auth/RequireAuth";
import { HomePage } from "../pages/HomePage";
import { LobbyPage } from "../pages/LobbyPage";
import { LoginPage } from "../pages/LoginPage";
import { NavGroupPage } from "../pages/NavGroupPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { Layout, MY_DEVICES_PATH } from "./Layout";
import { LOBBY_PATH, NAV_GROUPS } from "./nav";

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
          { path: LOBBY_PATH.slice(1), element: <LobbyPage /> },
          ...NAV_GROUPS.filter((group) => group.path !== "/").map((group) => ({
            path: group.path.slice(1),
            element: <NavGroupPage group={group.id} />,
          })),
          { path: "admin/access-requests", element: <AccessRequestsPage /> },
          { path: "admin/system/devices", element: <AdminDevicesPage /> },
          { path: MY_DEVICES_PATH.slice(1), element: <MyDevicesPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
