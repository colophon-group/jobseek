# Icon convention

The codebase uses [lucide-react](https://lucide.dev/) exclusively. Each icon has a stable semantic role that you should preserve when adding new components — pick from the table below before introducing a new one. Unconventional choices fragment the visual vocabulary fast.

If a new role legitimately requires an icon not in this table, add it to the table in the same PR that introduces it.

## Convention table

Grouped by domain. The "Used in" column lists representative consumers — not exhaustive.

### Domain entities

| Icon | Role | Used in (representative) |
|---|---|---|
| `Building2` | Company / employer (avatar, fallback when no logo) | `c/my-jobs/my-job-row.tsx`, `c/search/company-card.tsx`, `c/blog/MdxMentions.tsx` |
| `Briefcase` | Job posting / occupation filter / "My Jobs" nav slot | `c/AppHeader.tsx`, `c/search/search-bar.tsx`, `c/watchlist/watchlist-filter-editor.tsx` |
| `Eye` | Watchlists nav slot / public-watchlist visibility cue | `c/AppHeader.tsx`, `c/blog/MdxMentions.tsx` |
| `MapPin` | Location (filter, display, posting metadata) | `c/search/search-toolbar.tsx`, `c/search/location-pills.tsx` |
| `Code2` | Technology filter (modal, search bar, advanced panel) | `c/search/technology-modal.tsx`, `c/search/search-bar.tsx` |
| `Cpu` | Technology filter (alternate — used inside the watchlist filter editor specifically) | `c/watchlist/watchlist-filter-editor.tsx` |
| `Award` | Seniority filter | `c/watchlist/watchlist-filter-editor.tsx` |
| `DollarSign` | Salary filter / display | `c/search/advanced-search-panel.tsx`, `c/search/job-detail-dialog.tsx` |
| `Clock` | Experience / time-of-posting | `c/search/search-toolbar.tsx`, `c/search/job-detail-dialog.tsx` |
| `CalendarDays` | Date / posted-within filter | `c/search/advanced-search-panel.tsx`, `c/search/job-detail-dialog.tsx` |
| `Globe` | Locale / language switcher / `anyCompany` watchlists | `c/LocaleSwitcher.tsx`, `c/watchlist/watchlist-action-bar.tsx` |
| `Star` | Starred / favorite company | `c/StarredCompaniesProvider.tsx`, `c/my-jobs/quick-actions.tsx` |
| `Lock` | Private watchlist marker | `c/watchlist/watchlist-card.tsx` |
| `Crown` | Pro plan badge | `c/settings/BillingSettings.tsx` |

### Navigation chrome

| Icon | Role | Used in |
|---|---|---|
| `Compass` | Explore nav slot | `c/AppHeader.tsx` |
| `Eye` | Watchlists nav slot (also used for password-toggle "show", below) | `c/AppHeader.tsx` |
| `Briefcase` | "My Jobs" nav slot | `c/AppHeader.tsx` |
| `Settings` | Settings nav slot / icon | `c/AppHeader.tsx`, `a/(app)/settings/...` |
| `Menu` | Mobile menu open | `c/Header.tsx` |
| `X` | Close / dismiss (mobile menu, modals, chips) | `c/MobileMenu.tsx`, `c/ui/upgrade-modal.tsx` |
| `BackLink` (uses `ArrowLeft`) | Back navigation | `c/BackLink.tsx` |
| `ArrowRight` | Forward / submit affordance in inline buttons | `c/search/search-bar.tsx` |
| `ChevronDown` | Disclosure / expand-down (filter panels, dropdowns) | `c/search/advanced-search-panel.tsx` |
| `ChevronUp` | Collapse-up / back-to-top | `c/ui/back-to-top.tsx` |
| `ChevronRight` | Forward continuation in lists | `a/(app)/my-jobs/my-jobs-page.tsx` |
| `ArrowUpDown` | Sort affordance | `c/my-jobs/sort-filter-bar.tsx` |
| `LayoutGrid` | View-mode toggle (grid) | `c/my-jobs/sort-filter-bar.tsx` |
| `ExternalLink` | External-link indicator inside copy | `a/(public)/about/about-content.tsx` |

### Auth / account

| Icon | Role | Used in |
|---|---|---|
| `LogIn` | Sign-in CTA | `c/AppHeader.tsx`, `c/company/similar-companies-strip.tsx` |
| `LogOut` | Sign-out action | `c/AppHeader.tsx` |
| `Mail` | Email-related (verify, reset, contact) | `a/(auth)/check-email/page.tsx` |
| `Eye` | Password "show" toggle (overloaded with the watchlists-nav role; the meaning is disambiguated by surrounding context — input field vs nav bar) | `c/ui/FormField.tsx` |
| `EyeOff` | Password "hide" toggle | `c/ui/FormField.tsx` |

### Actions

| Icon | Role | Used in |
|---|---|---|
| `Plus` | Add (job, keyword, watchlist) | `c/my-jobs/quick-actions.tsx`, `c/watchlist/watchlist-card.tsx` |
| `Pencil` | Edit | `c/watchlist/watchlist-action-bar.tsx`, `a/(app)/[userSlug]/[watchlistSlug]/watchlist-view-page.tsx` |
| `Trash2` | Delete | `c/my-jobs/interview-list.tsx`, `c/watchlist/watchlist-action-bar.tsx` |
| `Copy` | Copy URL / text | `c/watchlist/public-watchlist-search.tsx`, `c/watchlist/watchlist-action-bar.tsx` |
| `Bookmark` | Save (unfilled) | `c/watchlist/watchlist-job-list.tsx` |
| `BookmarkCheck` | Saved (filled) — paired with `Bookmark` via `Icon = saved ? BookmarkCheck : Bookmark` | `c/search/save-button.tsx` |
| `Bell` | Alerts on | `c/watchlist/watchlist-action-bar.tsx` |
| `BellOff` | Alerts off | `c/watchlist/watchlist-action-bar.tsx` |
| `Check` | Confirm / done state | `c/settings/BillingSettings.tsx`, `c/my-jobs/quick-actions.tsx` |
| `Search` | Search input / CTA | `c/AppHeader.tsx`, `c/settings/JobLanguageModal.tsx` |
| `SlidersHorizontal` | Filter controls toggle / "filters" feature | `c/search/advanced-search-panel.tsx`, `c/Features.tsx` |

### Status / feedback

| Icon | Role | Used in |
|---|---|---|
| `AlertTriangle` | Warning / dangerous-action confirmation | `c/ui/upgrade-modal.tsx`, `c/PendingJobWarning.tsx` |
| `Info` | Inline info / disclosure | `c/CookieBanner.tsx`, `c/HowWeIndexContent.tsx` |
| `Lightbulb` | Tip / suggestion | `c/watchlist/watchlist-tip-banner.tsx` |
| `CircleCheck` | Success state (post-action confirmation) | `c/Pricing.tsx`, `a/verify-email/page.tsx` |
| `Loader2` | Loading spinner (`animate-spin`) | `c/InfiniteScrollSentinel.tsx`, `c/search/technology-modal.tsx` |
| `Construction` | "Work in progress" placeholder | `a/(app)/progress/progress-loader.tsx` |

### Theme

| Icon | Role | Used in |
|---|---|---|
| `Sun` | Light theme | `c/ThemeToggleButton.tsx` |
| `Moon` | Dark theme | `c/ThemeToggleButton.tsx` |

### Homepage feature pictograms (`Features.tsx::iconMap`)

These are mapped from `siteConfig.features.sections[*].pointIcons` strings — do not introduce new keys without updating both the `iconMap` and the config types.

| Key | Icon | Meaning |
|---|---|---|
| `source` | `Globe` | Data source (career pages directly) |
| `filters` | `SlidersHorizontal` | Multi-dimensional filtering |
| `alerts` | `Bell` | Email alerts on new matches |
| `tracking` | `GitGraph` | Application-tracker pipeline |
| `interviews` | `ClipboardList` | Interview log |
| `stats` | `BarChart3` | Pipeline analytics |
| `curate` | `Target` | Curate companies into a watchlist |
| `companies` | `Building2` | Companies indexed |
| `share` | `Share2` | Share watchlists publicly |

## Conventions

- **Sizes**: `size={14}` for inline-flow icons inside text, `size={16}` for buttons, `size={18}` for nav icons in the desktop header, `size={20}` for nav icons in the mobile bottom bar. Pull from existing nearby usage rather than inventing.
- **Color**: icons inherit `currentColor`. Set color via the parent's `text-*` class (`text-muted`, `text-primary`, `text-error`, etc.). Avoid hard-coding `color="#..."` props.
- **Aria**: decorative icons get `aria-hidden="true"` (no role, no label). Icons that are the *only* affordance for an action need an accessible label — usually via `aria-label` on the parent button or via a Radix Tooltip wrapper.
- **Animations**: only `Loader2` is animated, via `className="animate-spin"`. Don't apply spin/pulse/etc. to other icons unless adding a new convention here.

## When to reach for a new icon

Before importing a new lucide icon:

1. **Check this table first.** If a role exists, reuse the icon.
2. **Look for visual neighbors.** A close synonym (`Filter` vs `SlidersHorizontal`) is visual fragmentation.
3. **If the role really is new**, add a row here in the same PR. Pick an icon name that's unambiguous in lucide's set; avoid icons whose meaning depends on color (e.g. `Heart` reads as "favorite" only if filled).

## Linting / enforcement

There is no automated lint rule today. Reviewer responsibility: spot icons that drift from the table during PR review. Add a row to the table when introducing a genuine new role; reject the PR otherwise.
