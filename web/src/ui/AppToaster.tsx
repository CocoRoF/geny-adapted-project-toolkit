import { Toaster } from "sonner";

import { useTheme } from "@/app/providers/theme-context";

/** App-wide sonner toaster, bound to GAPT's theme toggle so toasts
 * flip light/dark with the rest of the chrome. Mounted once in App. */
export function AppToaster() {
  const { resolved } = useTheme();
  return (
    <Toaster
      theme={resolved}
      position="bottom-right"
      richColors
      closeButton
      toastOptions={{ duration: 4000 }}
    />
  );
}
