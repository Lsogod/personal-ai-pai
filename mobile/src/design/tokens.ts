import { Platform } from "react-native";

export const colors = {
  /* ── Primary ── */
  primary: "#4f6ef7",
  primaryDark: "#3b5de7",
  primaryLight: "#ebeffe",
  primaryMuted: "rgba(79,110,247,0.08)",

  /* ── Accent / Success ── */
  accent: "#10b981",
  accentDark: "#059669",
  accentLight: "#d1fae5",

  /* ── Warm ── */
  warning: "#f59e0b",
  warningLight: "#fef3c7",

  /* ── Danger ── */
  danger: "#ef4444",
  dangerLight: "#fee2e2",

  /* ── Surfaces ── */
  bg: "#f4f5fa",
  surface: "#ffffff",
  surfaceDim: "#f8f9fc",

  /* ── Borders ── */
  border: "#e2e6f0",
  borderLight: "#eef0f6",

  /* ── Typography ── */
  text: "#1a1d26",
  text2: "#4a5064",
  text3: "#8a90a4",
  text4: "#bfc4d2",

  /* ── Misc ── */
  notification: "rgba(26, 29, 38, 0.94)",
  iconBgPrimary: "#ebeffe",
  iconBgPink: "#fce7f3",
  iconBgGreen: "#d1fae5",
  iconBgOrange: "#fff4e5",
  iconBgPurple: "#f3e8ff",

  /* ── Gradient helpers (used as overlapping view layers) ── */
  gradientStart: "#5b7bf9",
  gradientEnd: "#8b5cf6",
};

export const radii = {
  xs: 8,
  sm: 12,
  md: 18,
  lg: 24,
  xl: 32,
  full: 999,
};

export const spacing = {
  pageX: 18,
  pageY: 16,
};

export const shadowSm = Platform.select({
  ios: {
    shadowColor: "#1a1d26",
    shadowOpacity: 0.06,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
  },
  android: {
    elevation: 3,
  },
  default: {},
});

export const shadowMd = Platform.select({
  ios: {
    shadowColor: "#1a1d26",
    shadowOpacity: 0.1,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 8 },
  },
  android: {
    elevation: 6,
  },
  default: {},
});

export const shadowLg = Platform.select({
  ios: {
    shadowColor: "#1a1d26",
    shadowOpacity: 0.14,
    shadowRadius: 28,
    shadowOffset: { width: 0, height: 12 },
  },
  android: {
    elevation: 10,
  },
  default: {},
});

export const surfaceCard = {
  backgroundColor: colors.surface,
  borderRadius: radii.lg,
  borderWidth: 1,
  borderColor: colors.borderLight,
  ...shadowSm,
} as const;
