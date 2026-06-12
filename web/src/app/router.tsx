import { Navigate, Route, Routes } from "react-router-dom";

import { AppShellLayout } from "@/app/layouts/AppShellLayout";
import { RequireAuth } from "@/app/RequireAuth";
import { Cost } from "@/routes/Cost";
import { Environments } from "@/routes/Environments";
import { Login } from "@/routes/Login";
import { Performance } from "@/routes/Performance";
import { ProjectDetail } from "@/routes/ProjectDetail";
import { ProjectsIndex } from "@/routes/ProjectsIndex";
import { SessionDetail } from "@/routes/SessionDetail";
import { SessionsHistory } from "@/routes/SessionsHistory";
import { Settings } from "@/routes/Settings";
import { WorkspaceChatPopup } from "@/routes/WorkspaceChatPopup";
import { WorkspaceIde } from "@/routes/WorkspaceIde";

/** Single source of truth for paths. `/login` is the only unauth
 * route — there's no magic-link callback anymore; login is a single
 * POST that sets the session cookie. */
export function AppRouter() {
  return (
    <Routes>
      {/* Public */}
      <Route
        path="/login"
        element={
          <AppShellLayout>
            <Login />
          </AppShellLayout>
        }
      />

      {/* Auth-required */}
      <Route
        path="/projects"
        element={
          <RequireAuth>
            <AppShellLayout>
              <ProjectsIndex />
            </AppShellLayout>
          </RequireAuth>
        }
      />
      <Route
        path="/projects/:pid"
        element={
          <RequireAuth>
            <AppShellLayout>
              <ProjectDetail />
            </AppShellLayout>
          </RequireAuth>
        }
      />
      <Route
        path="/projects/:pid/environments"
        element={
          <RequireAuth>
            <AppShellLayout>
              <Environments />
            </AppShellLayout>
          </RequireAuth>
        }
      />
      <Route
        path="/projects/:pid/w/:wid"
        element={
          <RequireAuth>
            <AppShellLayout>
              <WorkspaceIde />
            </AppShellLayout>
          </RequireAuth>
        }
      />
      {/* Devtools-undock style chat popup — chrome-less on purpose
          (no AppShellLayout): the window IS the chat panel. */}
      <Route
        path="/projects/:pid/w/:wid/chat"
        element={
          <RequireAuth>
            <WorkspaceChatPopup />
          </RequireAuth>
        }
      />
      <Route
        path="/projects/:pid/sessions"
        element={
          <RequireAuth>
            <AppShellLayout>
              <SessionsHistory />
            </AppShellLayout>
          </RequireAuth>
        }
      />
      <Route
        path="/projects/:pid/sessions/:sid"
        element={
          <RequireAuth>
            <AppShellLayout>
              <SessionDetail />
            </AppShellLayout>
          </RequireAuth>
        }
      />
      <Route
        path="/cost"
        element={
          <RequireAuth>
            <AppShellLayout>
              <Cost />
            </AppShellLayout>
          </RequireAuth>
        }
      />
      <Route
        path="/performance"
        element={
          <RequireAuth>
            <AppShellLayout>
              <Performance />
            </AppShellLayout>
          </RequireAuth>
        }
      />
      <Route
        path="/settings/*"
        element={
          <RequireAuth>
            <AppShellLayout>
              <Settings />
            </AppShellLayout>
          </RequireAuth>
        }
      />

      {/* Fallbacks */}
      <Route path="/" element={<Navigate to="/projects" replace />} />
      <Route path="*" element={<Navigate to="/projects" replace />} />
    </Routes>
  );
}
