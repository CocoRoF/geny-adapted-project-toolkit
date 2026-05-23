import { Navigate, Route, Routes } from "react-router-dom";

import { AppShellLayout } from "@/app/layouts/AppShellLayout";
import { RequireAuth } from "@/app/RequireAuth";
import { AuthCallback } from "@/routes/AuthCallback";
import { Login } from "@/routes/Login";
import { ProjectDetail } from "@/routes/ProjectDetail";
import { ProjectsIndex } from "@/routes/ProjectsIndex";
import { Settings } from "@/routes/Settings";
import { WorkspaceIde } from "@/routes/WorkspaceIde";

/** Single source of truth for paths. Keep `/login`, `/auth/callback`
 * outside the auth-gated shell so an unauthenticated session can still
 * complete sign-in without redirect loops. */
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
      <Route
        path="/auth/callback"
        element={
          <AppShellLayout>
            <AuthCallback />
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
        path="/projects/:pid/w/:wid"
        element={
          <RequireAuth>
            <AppShellLayout>
              <WorkspaceIde />
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
