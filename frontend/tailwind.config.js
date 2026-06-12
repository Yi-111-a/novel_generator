/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        serif: ['Georgia', 'Songti SC', 'Noto Serif SC', 'serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      colors: {
        // 数据色彩编码：真相 / 扭曲 / open / paid_off / structural / decision
        truth: '#34d399',
        distort: '#f59e0b',
        ironic: '#a78bfa',
        danger: '#f43f5e',
      },
      keyframes: {
        breathe: { '0%,100%': { opacity: '0.35' }, '50%': { opacity: '1' } },
      },
      animation: { breathe: 'breathe 1.8s ease-in-out infinite' },
    },
  },
  plugins: [],
};
