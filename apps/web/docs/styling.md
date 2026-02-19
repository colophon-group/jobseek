# Styling Guidelines

## Three layers, strict boundaries

| Layer | Where | What goes here |
| --- | --- | --- |
| `globals.css` | `app/globals.css` | CSS custom properties for non-MUI code, resets, `body` defaults. Nothing component-specific. |
| CSS Modules | `*.module.css` co-located with the component | All component-scoped styles. Automatically scoped, tree-shaken when the component isn't rendered. |
| MUI `sx` prop | Inline on MUI components | Theme-aware values only (spacing, breakpoints, palette tokens). |

## Single source of truth

`src/theme/tokens.ts` owns every design token as a JS object. Two consumers read from it:

- **`createMuiTheme.ts`** — imports `tokens` directly, builds separate light and dark MUI themes with real hex values. `ThemeProvider` swaps the active theme based on `mode` state. The `.dark` class on `<html>` triggers the dark scheme for custom CSS properties.
- **`globals.css`** — mirrors the same values as CSS custom properties (`--background`, `--foreground`, etc.) for CSS Modules and server components that can't access MUI's theme.

When changing a token, update `tokens.ts` first, then update the matching variable in `globals.css`.

## Rules

1. **Never add component styles to `globals.css`.** If a rule only matters when a specific component is rendered, it belongs in a CSS Module.
2. **Co-locate CSS Modules with their component.** `Foo.tsx` → `Foo.module.css`, same directory.
3. **In CSS Modules, use `var(--name)` for colors and tokens** — never hardcode hex values.
4. **Reference global classes from modules with `:global()`** when needed (e.g., `:global(.dark) .light { display: none; }`).
5. **Prefer CSS Modules over `sx` for non-MUI elements.** Reserve `sx` for MUI components that need theme-aware responsive values.
6. **No CSS-in-JS libraries** (Emotion `css`, styled-components) outside of MUI's built-in `sx`/`styled` API.
7. **Never hardcode hex values in `createMuiTheme.ts`.** Import from `tokens.ts`.
8. **Keep `globals.css` minimal.** Token definitions (`:root`, `.dark`) + body reset. No component rules, no utility classes.

## Example

```
src/components/
  ThemedImage.tsx
  ThemedImage.module.css
```

```css
/* ThemedImage.module.css */
.dark {
  display: none;
}

:global(.dark) .light {
  display: none;
}

:global(.dark) .dark {
  display: block;
}
```

```tsx
// ThemedImage.tsx — server component, no "use client"
import styles from "./ThemedImage.module.css";

<Image className={styles.light} ... />
<Image className={styles.dark} ... />
```
