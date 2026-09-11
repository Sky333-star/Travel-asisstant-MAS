import type { Config } from 'tailwindcss';

/**
 * A small, deliberate palette.
 *
 * `ink` is the neutral scale used for text and surfaces; `sea` is the single
 * accent. Semantic colours are reserved for validation states so that a green
 * or amber badge always means the same thing anywhere in the UI -- a plan
 * passed, or a plan has a caveat.
 */
const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f6f7f9',
          100: '#eceef2',
          200: '#d5dae2',
          300: '#b0bac9',
          400: '#8695ab',
          500: '#677891',
          600: '#526078',
          700: '#434e62',
          800: '#3a4353',
          900: '#343a47',
          950: '#22262f',
        },
        sea: {
          50: '#eef7ff',
          100: '#d9edff',
          200: '#bce0ff',
          300: '#8ecdff',
          400: '#59b0ff',
          500: '#328eff',
          600: '#1b6ff5',
          700: '#1459e1',
          800: '#1749b6',
          900: '#19418f',
          950: '#142857',
        },
        pass: '#15803d',
        warn: '#b45309',
        block: '#b91c1c',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      animation: {
        'fade-in': 'fadeIn 220ms ease-out',
        'slide-up': 'slideUp 260ms cubic-bezier(0.16, 1, 0.3, 1)',
        'pulse-soft': 'pulseSoft 1.8s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pulseSoft: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
      },
    },
  },
  plugins: [],
};

export default config;
