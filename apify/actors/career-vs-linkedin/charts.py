"""
Generate marketing charts for the Career Page vs LinkedIn coverage analysis.
Run: python3 charts.py
Outputs: charts/ directory with PNG files.

Key finding: ~97% of jobs on company career pages never appear on LinkedIn at all.
Timing comparison is NOT claimed — Wayback Machine resolution (weeks) cannot
capture the real gap (hours-to-days). Coverage gap is the credible finding.
"""

import os, json, statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.ticker import MaxNLocator
import numpy as np

# ── Load data ─────────────────────────────────────────────────────────────────

DATASET = 'storage/datasets/default/'

matches = []
summaries = []

for fname in sorted(os.listdir(DATASET)):
    with open(os.path.join(DATASET, fname)) as f:
        d = json.load(f)
    if d.get('_type') == 'job-comparison' and d.get('verdict') == 'career_first' and d.get('lagDays'):
        matches.append(d)
    if d.get('_type') == 'research-summary':
        summaries.append(d)

summaries.sort(key=lambda x: x['company'])

# Only keep credible short leads (≤90 days) for timing charts
credible_matches = [m for m in matches if m['lagDays'] <= 90]

os.makedirs('charts', exist_ok=True)

# ── Colour palette ─────────────────────────────────────────────────────────────
CAREER_COLOR   = '#2563EB'   # blue  – career page
LINKEDIN_COLOR = '#0A66C2'   # LinkedIn blue
MISS_COLOR     = '#E5E7EB'   # light grey – jobs LinkedIn missed
HIT_COLOR      = '#2563EB'   # blue – jobs LinkedIn found
ACCENT         = '#10B981'   # green – positive stat
WARN_COLOR     = '#EF4444'   # red
BG             = '#FAFAFA'
GRID           = '#E5E7EB'
TEXT           = '#111827'
SUBTEXT        = '#6B7280'
DARK_BG        = '#0F172A'

COMPANY_COLORS = {
    'OpenAI':   '#10A37F',
    'Notion':   '#374151',
    'Figma':    '#A259FF',
}

# ── Chart 1: Coverage — career page jobs vs LinkedIn-indexed jobs ─────────────
# Core claim: LinkedIn indexes only ~2-4% of all career page jobs

fig, ax = plt.subplots(figsize=(11, 6))
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)

companies = [s['company'] for s in summaries]
total_career = [s['totalCareerJobs'] for s in summaries]
on_linkedin  = [s['totalBoardJobs'] for s in summaries]
missed       = [t - l for t, l in zip(total_career, on_linkedin)]
pct_missed   = [100 * m / t for m, t in zip(missed, total_career)]

x = np.arange(len(companies))
bar_w = 0.55

bars_miss = ax.bar(x, missed, bar_w, color=MISS_COLOR, label='Never appeared on LinkedIn', zorder=3)
bars_hit  = ax.bar(x, on_linkedin, bar_w, bottom=missed, color=HIT_COLOR,
                   label='Found on LinkedIn', zorder=3, alpha=0.9)

# % miss label inside grey zone
for i, (m, t, pct) in enumerate(zip(missed, total_career, pct_missed)):
    ax.text(x[i], m / 2, f'{pct:.0f}%\nnever on\nLinkedIn',
            ha='center', va='center', fontsize=11, fontweight='bold',
            color=TEXT, linespacing=1.4)
    # total label on top
    ax.text(x[i], t + 4, f'{t} jobs total', ha='center', va='bottom',
            fontsize=9, color=SUBTEXT)

ax.set_xticks(x)
ax.set_xticklabels(companies, fontsize=13, color=TEXT, fontweight='bold')
ax.set_ylabel('Job postings (2021–2024)', fontsize=10, color=SUBTEXT, labelpad=10)
ax.set_title('LinkedIn indexes only a fraction of company career page jobs\n'
             'The vast majority of openings never reach the platform',
             fontsize=14, fontweight='bold', color=TEXT, pad=16)
ax.set_ylim(0, max(total_career) * 1.15)
ax.spines[['top', 'right']].set_visible(False)
ax.spines[['left', 'bottom']].set_color(GRID)
ax.yaxis.grid(True, color=GRID, zorder=0)
ax.set_axisbelow(True)
ax.tick_params(axis='y', colors=SUBTEXT, labelsize=9)

ax.legend(loc='upper right', fontsize=10, framealpha=0.9)

fig.text(0.01, 0.01,
         'Source: Greenhouse ATS (career page) vs Wayback Machine LinkedIn archive · 2021–2024',
         fontsize=7, color=SUBTEXT)

plt.tight_layout(rect=[0, 0.03, 1, 1])
plt.savefig('charts/01_coverage_gap.png', dpi=180, bbox_inches='tight')
plt.close()
print('Saved charts/01_coverage_gap.png')

# ── Chart 2: Hero stat — coverage miss rate ───────────────────────────────────

total_all  = sum(s['totalCareerJobs'] for s in summaries)
indexed_all = sum(s['totalBoardJobs'] for s in summaries)
missed_all = total_all - indexed_all
pct_missed_all = round(100 * missed_all / total_all)

fig, ax = plt.subplots(figsize=(10, 5.5))
fig.patch.set_facecolor(DARK_BG)
ax.set_facecolor(DARK_BG)
ax.axis('off')

ax.text(0.5, 0.84, f'{pct_missed_all}%', transform=ax.transAxes,
        fontsize=72, fontweight='bold', ha='center', va='center', color='#34D399')
ax.text(0.5, 0.63, 'of jobs on company career pages\nnever appeared on LinkedIn',
        transform=ax.transAxes, fontsize=16, ha='center', va='center',
        color='white', linespacing=1.5)

stats = [
    (0.2,  'career page jobs tracked', str(total_all)),
    (0.5,  'found on LinkedIn',         str(indexed_all)),
    (0.8,  'companies analysed',        str(len(summaries))),
]
for x_pos, label, value in stats:
    ax.text(x_pos, 0.30, value, transform=ax.transAxes,
            fontsize=26, fontweight='bold', ha='center', color='#60A5FA')
    ax.text(x_pos, 0.15, label, transform=ax.transAxes,
            fontsize=10, ha='center', color='#94A3B8')

ax.text(0.5, 0.02,
        'OpenAI · Notion · Figma — Greenhouse ATS vs Wayback Machine LinkedIn archive · 2021–2024',
        transform=ax.transAxes, fontsize=8, ha='center', color='#475569')

plt.tight_layout()
plt.savefig('charts/02_hero_coverage.png', dpi=180, bbox_inches='tight')
plt.close()
print('Saved charts/02_hero_coverage.png')

# ── Chart 3: Donut — what share of jobs reach LinkedIn ───────────────────────

fig, axes = plt.subplots(1, len(summaries), figsize=(12, 5))
fig.patch.set_facecolor(BG)
if len(summaries) == 1:
    axes = [axes]

for ax, s in zip(axes, summaries):
    ax.set_facecolor(BG)
    company = s['company']
    total   = s['totalCareerJobs']
    found   = s['totalBoardJobs']
    missed  = total - found
    pct_found = round(100 * found / total) if total else 0

    wedges, _ = ax.pie(
        [found, missed],
        colors=[HIT_COLOR, MISS_COLOR],
        startangle=90,
        wedgeprops=dict(width=0.52, edgecolor='white', linewidth=2),
    )
    # Centre text
    ax.text(0, 0.08, f'{100 - pct_found}%', ha='center', va='center',
            fontsize=26, fontweight='bold', color=TEXT)
    ax.text(0, -0.22, 'missed\nby LinkedIn', ha='center', va='center',
            fontsize=9, color=SUBTEXT, linespacing=1.4)
    ax.set_title(f'{company}\n{total} career jobs · {found} on LinkedIn',
                 fontsize=11, fontweight='bold', color=TEXT, pad=10)

patch_found  = mpatches.Patch(color=HIT_COLOR, label='Found on LinkedIn')
patch_missed = mpatches.Patch(color=MISS_COLOR, label='Not on LinkedIn')
fig.legend(handles=[patch_found, patch_missed], loc='lower center',
           ncol=2, fontsize=10, framealpha=0.9, bbox_to_anchor=(0.5, -0.02))

fig.suptitle('Career page coverage vs LinkedIn — per company',
             fontsize=14, fontweight='bold', color=TEXT, y=1.01)
fig.text(0.5, -0.06, 'Source: Greenhouse ATS vs Wayback Machine LinkedIn archive · 2021–2024',
         ha='center', fontsize=8, color=SUBTEXT)

plt.tight_layout()
plt.savefig('charts/03_donut_coverage.png', dpi=180, bbox_inches='tight')
plt.close()
print('Saved charts/03_donut_coverage.png')

print(f'\nDone. Summary:')
print(f'  Total career jobs: {total_all}')
print(f'  Found on LinkedIn: {indexed_all} ({100-pct_missed_all}%)')
print(f'  Never on LinkedIn: {missed_all} ({pct_missed_all}%)')
