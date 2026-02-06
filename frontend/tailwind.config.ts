import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Space Grotesk", "system-ui", "sans-serif"],
        serif: ["ZCOOL XiaoWei", "serif"]
      },
      colors: {
        ink: "#1b1b1b",
        accent: "#0b7c7c",
        accentDark: "#0a5d5d",
        sand: "#f7f2ea",
        card: "#fffaf4"
      },
      boxShadow: {
        glow: "0 24px 48px rgba(16, 24, 40, 0.08)"
      }
    }
  },
  plugins: []
} satisfies Config;
