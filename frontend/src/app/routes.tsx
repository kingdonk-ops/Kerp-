import type { RouteObject } from "react-router";

import { AccessRequestsPage } from "../admin/AccessRequestsPage";
import { CommsBroadcastsPage } from "../admin/CommsBroadcastsPage";
import { CommsDeliveryPage } from "../admin/CommsDeliveryPage";
import { CommsTemplatesPage } from "../admin/CommsTemplatesPage";
import { AdminDevicesPage, MyDevicesPage } from "../admin/DevicesPage";
import { MembersPage } from "../admin/MembersPage";
import { ProjectsPage } from "../admin/ProjectsPage";
import { SystemAuditPage } from "../admin/SystemAuditPage";
import { SystemDeploymentPage } from "../admin/SystemDeploymentPage";
import { SystemGrantsPage } from "../admin/SystemGrantsPage";
import { SystemQueuesPage } from "../admin/SystemQueuesPage";
import { SystemSettingsPage } from "../admin/SystemSettingsPage";
import { SystemUploadsPage } from "../admin/SystemUploadsPage";
import { UsersPage } from "../admin/UsersPage";
import { RequireAuth } from "../auth/RequireAuth";
import { SSO_RETURN_PATH } from "../auth/sso";
import { InvitePage } from "../invite/InvitePage";
import { INVITE_PATH } from "../invite/pendingInvite";
import { FORGOT_PASSWORD_PATH, PasswordEmailPage } from "../password/PasswordEmailPage";
import { RESET_PASSWORD_PATH, ResetPasswordPage } from "../password/ResetPasswordPage";
import { HomePage } from "../pages/HomePage";
import { LobbyPage } from "../pages/LobbyPage";
import { LoginPage } from "../pages/LoginPage";
import { NavGroupPage } from "../pages/NavGroupPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { SsoReturnPage } from "../pages/SsoReturnPage";
import { UNSUBSCRIBE_PATH, UnsubscribePage } from "../pages/UnsubscribePage";
import { Layout, MY_DEVICES_PATH } from "./Layout";
import { LOBBY_PATH, NAV_GROUPS } from "./nav";

export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  { path: SSO_RETURN_PATH, element: <SsoReturnPage /> },
  // Invite and password screens (T-0053): outside the guard, they serve signed-out users too.
  { path: INVITE_PATH, element: <InvitePage /> },
  { path: FORGOT_PASSWORD_PATH, element: <PasswordEmailPage /> },
  { path: RESET_PASSWORD_PATH, element: <ResetPasswordPage /> },
  // Broadcast opt-out link of the e-mails (T-0229, TD-601): no sign-in.
  { path: UNSUBSCRIBE_PATH, element: <UnsubscribePage /> },
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
          { path: "admin/users", element: <UsersPage /> },
          { path: "admin/access-requests", element: <AccessRequestsPage /> },
          { path: "admin/system/deployment", element: <SystemDeploymentPage /> },
          { path: "admin/system/queues", element: <SystemQueuesPage /> },
          { path: "admin/system/uploads", element: <SystemUploadsPage /> },
          { path: "admin/system/devices", element: <AdminDevicesPage /> },
          { path: "admin/system/grants", element: <SystemGrantsPage /> },
          { path: "admin/system/settings", element: <SystemSettingsPage /> },
          { path: "admin/system/audit", element: <SystemAuditPage /> },
          { path: "admin/communications/broadcasts", element: <CommsBroadcastsPage /> },
          { path: "admin/communications/delivery", element: <CommsDeliveryPage /> },
          { path: "admin/communications/templates", element: <CommsTemplatesPage /> },
          { path: "admin/projects/:projectId/members", element: <MembersPage /> },
          { path: "backbone/projects", element: <ProjectsPage /> },
          { path: MY_DEVICES_PATH.slice(1), element: <MyDevicesPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
