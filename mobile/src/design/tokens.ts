import { Platform } from "react-native";

export const colors = {
  primary: "#4f6ef7",
  primaryDark: "#3b5de7",
  primaryLight: "#ebeffe",
  accent: "#10b981",
  accentLight: "#d1fae5",
  warning: "#f59e0b",
  warningLight: "#fef3c7",
  danger: "#ef4444",
  dangerLight: "#fee2e2",
  bg: "#f6f7fb",
  surface: "#ffffff",
  border: "#e8ecf2",
  borderLight: "#f0f2f7",
  text: "#1a1d26",
  text2: "#5a6070",
  text3: "#9aa0b0",
  text4: "#c5cad5",
  notification: "rgba(26, 29, 38, 0.94)",
  iconBgPrimary: "#ebeffe",
  iconBgPink: "#fce7f3",
  iconBgGreen: "#d1fae5",
  iconBgOrange: "#fff4e5",
  iconBgPurple: "#f3e8ff",
};

export const radii = {
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
    shadowOpacity: 0.05,
    shadowRadius: 10,
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
    shadowOpacity: 0.08,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 8 },
  },
  android: {
    elevation: 5,
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
