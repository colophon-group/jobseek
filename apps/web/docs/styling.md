# Styling Guidelines

The web app uses Tailwind v4 utilities backed by CSS custom properties in
`apps/web/app/globals.css`. Start with semantic Tailwind classes in JSX, and
keep global CSS limited to theme tokens, resets, and shared prose/rendering
surfaces that cannot reasonably live on a single component.

## Design tokens

`globals.css` is the source of truth for the visual vocabulary:

- `:root` defines the light color values.
- `.dark` defines the dark color values.
- `@theme` exposes those variables to Tailwind classes such as `bg-surface`,
  `text-muted`, `border-border-soft`, `bg-success-bg`, and `text-error`.
- Radius and font tokens are also exported through `@theme`.

Prefer semantic token classes over raw colors. If a color, radius, or surface is
part of the product theme, add or update the token in `globals.css` first, then
consume it through a Tailwind class. Keep raw hex values out of component JSX
unless the value represents fixed external artwork or a third-party brand color.

## Component styling

1. Use Tailwind utilities directly in `className` for layout, spacing, type,
   borders, state, and responsive behavior.
2. Extract repeated class groups into small components when the same visual
   pattern appears in more than one place.
3. Keep component-specific selectors out of `globals.css`. Global classes are
   reserved for cross-cutting rendered content such as job descriptions, blog
   prose, resets, fonts, and tokens.
4. Prefer token classes like `bg-surface`, `text-foreground`, `text-muted`,
   `border-divider`, and `hover:bg-border-soft` over one-off values.
5. Use Tailwind state variants (`hover:`, `focus-visible:`, `disabled:`,
   `data-[state=open]:`, `dark:`) instead of adding imperative style state.
6. Keep class strings readable. If a component becomes hard to scan, split the
   component or move repeated variants behind a typed helper.

## Theme handling

`next-themes` sets the active theme and applies `.dark` on `<html>` before the
app paints. Build most components with semantic token classes so light and dark
mode follow the variables automatically. Reach for explicit `dark:` variants
only when the layout or contrast needs a distinct dark-mode treatment that a
token cannot express.

Components that need the current resolved theme, such as charts or artwork,
should read it through `useTheme()` in a client component and still prefer the
same token values or CSS variables where possible.

## UI primitives and icons

Use Radix primitives for interactions that need accessibility semantics and
keyboard behavior: dialogs, alert dialogs, dropdown menus, and tooltips. Style
Radix parts with Tailwind classes and data-state variants.

Use `lucide-react` for icons. Icon-only controls need an accessible label, and
less obvious controls should have a tooltip. Keep icons sized with Tailwind
utilities or the component's `size` prop so hit targets stay stable.

## Text and translations

Visible UI copy must follow `apps/web/docs/i18n.md`. Use Lingui `<Trans>`,
`t()`, or `plural()` with explicit IDs and comments. When styled inline JSX is
part of translated copy, keep the element structure simple and avoid splitting a
sentence into separately translated fragments just to apply styling.

## Example

```tsx
import { Search } from "lucide-react";

export function SearchField({
  label,
  placeholder,
}: {
  label: string;
  placeholder: string;
}) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-medium">
      <span>{label}</span>
      <span className="flex items-center gap-2 rounded-md border border-border-soft bg-surface px-3 py-2 focus-within:border-primary">
        <Search size={14} className="shrink-0 text-muted" aria-hidden="true" />
        <input
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
          placeholder={placeholder}
        />
      </span>
    </label>
  );
}
```
