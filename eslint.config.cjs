const eslint = require("@eslint/js");
const tseslint = require("typescript-eslint");

const { FlatCompat } = require("@eslint/eslintrc");
const js = require("@eslint/js");

const compat = new FlatCompat({
    baseDirectory: __dirname,                  // optional; default: process.cwd()
    resolvePluginsRelativeTo: __dirname,       // optional
    recommendedConfig: js.configs.recommended, // optional unless using "eslint:recommended"
    allConfig: js.configs.all,                 // optional unless using "eslint:all"
});

module.exports = tseslint.config(
  // Built artifacts — minified Vite output that ships inside the
  // service container so FastAPI's StaticFiles can serve the SPA at
  // /. Don't lint these; the rules don't apply to bundler output.
  {
    ignores: [
      "**/static/assets/**",
      "**/dist/**",
      "**/build/**",
      "**/node_modules/**",
    ],
  },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  ...compat.extends("next/core-web-vitals"),
  {
    rules: {
      "react/react-in-jsx-scope": "off",
      "react/jsx-uses-react": "off"
    }
  }
);

