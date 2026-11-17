import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        // The wordmark's typeface (brand/README.md); loaded in main.tsx.
        brand: ["Fraunces", "Georgia", "serif"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      keyframes: {
        // The AI-ready tick in the presentation corner: pops in, draws, goes.
        "check-draw": { from: { strokeDashoffset: "24" }, to: { strokeDashoffset: "0" } },
        "pop-fade": {
          "0%": { opacity: "0", transform: "scale(0.6)" },
          "15%": { opacity: "1", transform: "scale(1)" },
          "70%": { opacity: "1", transform: "scale(1)" },
          "100%": { opacity: "0", transform: "scale(0.9)" },
        },
        // AI assist: a gentle yellow glow on the slide the band seems to be
        // on. Box-shadow only, so the tile itself never moves.
        "assist-pulse": {
          "0%, 100%": { boxShadow: "0 0 0 2px rgba(250, 204, 21, 0.55), 0 0 10px 1px rgba(250, 204, 21, 0.25)" },
          "50%": { boxShadow: "0 0 0 3px rgba(250, 204, 21, 0.95), 0 0 22px 5px rgba(250, 204, 21, 0.45)" },
        },
        "assist-pulse-light": {
          "0%, 100%": { boxShadow: "0 0 0 1px rgba(250, 204, 21, 0.25)" },
          "50%": { boxShadow: "0 0 0 2px rgba(250, 204, 21, 0.7), 0 0 12px 2px rgba(250, 204, 21, 0.3)" },
        },
      },
      animation: {
        // "Somewhere else": slow and strong. "Next slide is due": quick and light.
        "assist-pulse": "assist-pulse 1.8s ease-in-out infinite",
        "assist-pulse-light": "assist-pulse-light 0.9s ease-in-out infinite",
        "check-draw": "check-draw 350ms ease-out 150ms both",
        "pop-fade": "pop-fade 1600ms ease-out forwards",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
