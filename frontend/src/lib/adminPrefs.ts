export const ADMIN_PREF_SHOW_EXECUTION_PANEL_KEY = "pai_admin_show_execution_panel";

export function getAdminShowExecutionPanel(): boolean {
  if (typeof window === "undefined") return true;
  const raw = window.localStorage.getItem(ADMIN_PREF_SHOW_EXECUTION_PANEL_KEY);
  if (raw === null) return true;
  const text = String(raw).trim().toLowerCase();
  return text === "1" || text === "true";
}

export function setAdminShowExecutionPanel(enabled: boolean): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ADMIN_PREF_SHOW_EXECUTION_PANEL_KEY, enabled ? "1" : "0");
  window.dispatchEvent(
    new CustomEvent("pai-admin-pref-changed", {
      detail: { key: ADMIN_PREF_SHOW_EXECUTION_PANEL_KEY, value: enabled },
    })
  );
}
