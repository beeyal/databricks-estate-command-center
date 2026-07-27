import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: {
          red: "rgb(var(--color-accent) / <alpha-value>)",
          dark: "rgb(var(--color-brand-dark) / <alpha-value>)",
        },
        accent: "rgb(var(--color-accent) / <alpha-value>)",
        surface: "rgb(var(--color-surface) / <alpha-value>)",
        card: "rgb(var(--color-card) / <alpha-value>)",
        border: "rgb(var(--color-border) / <alpha-value>)",
        neutral: "rgb(var(--color-neutral) / <alpha-value>)",
        success: "#1B8A4A",
        warning: "#F59E0B",
        danger: "#C0392B",
        info: "#8B5CF6",
        // insight-type pill colours (SPEC §2)
        insight: {
          cluster: "#0D9488", // teal
          resize: "#8B5CF6", // purple
          rewrite: "#3B82F6", // blue
          vacuum: "#F59E0B", // amber
          optimize: "#F59E0B", // amber
          convert: "#C0392B", // red
          enable: "#1B8A4A", // green (PO / LC)
        },
      },
      fontFamily: {
        sans: [
          "DM Sans",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgba(16, 24, 40, 0.06), 0 1px 3px rgba(16, 24, 40, 0.1)",
      },
    },
  },
  plugins: [],
};

export default config;
