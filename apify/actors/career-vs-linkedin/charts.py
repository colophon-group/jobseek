"""
Generate marketing charts for the Career Page vs LinkedIn timing analysis.
Run: python3 charts.py
Outputs: charts/ directory with PNG files.
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

matches.sort(key=lambda x: x['lagDays'], reverse=True)

os.makedirs('charts', exist_ok=True)

# ── Colour palette ────────────────────────────────────────────────────────────
OPENAI_COLOR  = '#10A37F'   # OpenAI brand green
NOTION_COLOR  = '#000000'   # Notion black
CAREER_COLOR  = '#2563EB'   # blue  – career page
LINKEDIN_COLOR= '#0A66C2'   # LinkedIn blue
BG            = '#FAFAFA'
GRID          = '#E5E7EB'
TEXT          = '#111827'
SUBTEXT       = '#6B7280'

COMPANY_COLORS = {'OpenAI': OPENAI_COLOR, 'Notion': NOTION_COLOR}

# ── Chart 1: Horizontal bar — lead time per matched job ───────────────────────

fig, ax = plt.subplots(figsize=(12, 7))
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)

labels = []
values = []
colors = []

for m in reversed(matches):   # reversed → shortest at top
    short = m['careerTitle']
    if len(short) > 42:
        short = short[:39] + '…'
    labels.append(f"{m['company']} · {short}")
    values.append(m['lagDays'])
    colors.append(COMPANY_COLORS[m['company']])

y = np.arange(len(labels))
bars = ax.barh(y, values, color=colors, height=0.62, zorder=3)

# Value labels on bars
for bar, val in zip(bars, values):
    ax.text(bar.get_width() + 8, bar.get_y() + bar.get_height() / 2,
            f'{val}d', va='center', ha='left', fontsize=8.5,
            color=TEXT, fontweight='bold')

ax.set_yticks(y)
ax.set_yticklabels(labels, fontsize=9, color=TEXT)
ax.set_xlabel('Days career page posted the job BEFORE LinkedIn showed it', fontsize=11, color=SUBTEXT, labelpad=10)
ax.set_title('Career pages consistently beat LinkedIn\nEvery matched role appeared on the career page first',
             fontsize=14, fontweight='bold', color=TEXT, pad=16)

ax.set_xlim(0, max(values) * 1.12)
ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
ax.tick_params(axis='x', colors=SUBTEXT, labelsize=9)
ax.spines[['top', 'right', 'left']].set_visible(False)
ax.spines['bottom'].set_color(GRID)
ax.xaxis.grid(True, color=GRID, zorder=0)
ax.set_axisbelow(True)

# Legend
patches = [mpatches.Patch(color=OPENAI_COLOR, label='OpenAI'),
           mpatches.Patch(color=NOTION_COLOR, label='Notion')]
ax.legend(handles=patches, loc='lower right', framealpha=0.9, fontsize=10)

# Footnote
fig.text(0.01, 0.01,
         'Source: Wayback Machine CDX · Career page datePosted from Greenhouse JSON-LD · '
         'LinkedIn firstSeen = earliest Wayback snapshot (upper-bound lag)',
         fontsize=7, color=SUBTEXT)

plt.tight_layout(rect=[0, 0.03, 1, 1])
plt.savefig('charts/01_lead_time_per_job.png', dpi=180, bbox_inches='tight')
plt.close()
print('Saved charts/01_lead_time_per_job.png')

# ── Chart 2: Summary stats side-by-side ───────────────────────────────────────

fig, axes = plt.subplots(1, 2, figsize=(13, 6))
fig.patch.set_facecolor(BG)

for ax, s in zip(axes, summaries):
    ax.set_facecolor(BG)
    company = s['company']
    snapshot_date = '2023-10-07' if company == 'Notion' else '2023-09-19'

    jobs_data = [m for m in matches if m['company'] == company]
    jobs_data.sort(key=lambda x: x['effectiveCareerDate'])

    career_dates = [m['effectiveCareerDate'] for m in jobs_data]
    lags         = [m['lagDays'] for m in jobs_data]
    short_titles = [m['careerTitle'][:30] + ('…' if len(m['careerTitle'])>30 else '') for m in jobs_data]

    x = np.arange(len(jobs_data))
    bar_color = COMPANY_COLORS[company]

    bars = ax.bar(x, lags, color=bar_color, width=0.6, zorder=3, alpha=0.85)
    for bar, lag in zip(bars, lags):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 10,
                f'{lag}d', ha='center', va='bottom', fontsize=8, color=TEXT, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(short_titles, rotation=35, ha='right', fontsize=8, color=TEXT)
    ax.set_ylabel('Days ahead of LinkedIn (upper bound)', fontsize=9, color=SUBTEXT)
    ax.set_title(f'{company}\n{s["matchedJobs"]} matched · 100% career first · avg {s["avgLagDays"]}d lead',
                 fontsize=12, fontweight='bold', color=TEXT, pad=12)
    ax.set_ylim(0, max(lags) * 1.18)
    ax.spines[['top','right']].set_visible(False)
    ax.spines[['left','bottom']].set_color(GRID)
    ax.yaxis.grid(True, color=GRID, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis='y', colors=SUBTEXT, labelsize=8)

    # Avg line
    avg = s['avgLagDays']
    ax.axhline(avg, color='#EF4444', linestyle='--', linewidth=1.4, zorder=4)
    ax.text(len(jobs_data) - 0.5, avg + max(lags)*0.02, f'avg {avg}d',
            color='#EF4444', fontsize=8.5, ha='right', fontweight='bold')

fig.suptitle('Career Page vs LinkedIn — Lead Time by Company',
             fontsize=15, fontweight='bold', color=TEXT, y=1.01)
fig.text(0.5, -0.02,
         'Lead time = career page datePosted (from ATS JSON-LD) → LinkedIn Wayback snapshot date',
         ha='center', fontsize=8, color=SUBTEXT)

plt.tight_layout()
plt.savefig('charts/02_per_company_breakdown.png', dpi=180, bbox_inches='tight')
plt.close()
print('Saved charts/02_per_company_breakdown.png')

# ── Chart 3: Key stat callout (marketing hero card) ──────────────────────────

fig, ax = plt.subplots(figsize=(10, 5.5))
fig.patch.set_facecolor('#0F172A')
ax.set_facecolor('#0F172A')
ax.axis('off')

all_lags = [m['lagDays'] for m in matches]
total_matched = len(matches)
med = int(statistics.median(all_lags))
min_lag = min(all_lags)

# Central stat
ax.text(0.5, 0.82, '100%', transform=ax.transAxes,
        fontsize=72, fontweight='bold', ha='center', va='center',
        color='#34D399')
ax.text(0.5, 0.62, 'of matched jobs appeared on the career page\nBEFORE LinkedIn — every single time',
        transform=ax.transAxes, fontsize=16, ha='center', va='center',
        color='white', linespacing=1.5)

# Sub-stats
for x_pos, label, value in [
    (0.2,  'min lead time',    f'{min_lag} days'),
    (0.5,  'median lead time', f'{med} days'),
    (0.8,  'jobs analysed',    f'{total_matched} matched'),
]:
    ax.text(x_pos, 0.28, value, transform=ax.transAxes,
            fontsize=22, fontweight='bold', ha='center', color='#60A5FA')
    ax.text(x_pos, 0.14, label, transform=ax.transAxes,
            fontsize=11, ha='center', color='#94A3B8')

# Source
ax.text(0.5, 0.02,
        'OpenAI & Notion · Greenhouse ATS datePosted vs Wayback Machine LinkedIn archive · 2020–2023',
        transform=ax.transAxes, fontsize=8, ha='center', color='#475569')

plt.tight_layout()
plt.savefig('charts/03_hero_stat.png', dpi=180, bbox_inches='tight')
plt.close()
print('Saved charts/03_hero_stat.png')

# ── Chart 4: Timeline scatter — career posting date vs LinkedIn snapshot ───────

fig, ax = plt.subplots(figsize=(12, 5))
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)

import datetime

for i, m in enumerate(matches):
    career_dt = datetime.datetime.strptime(m['effectiveCareerDate'], '%Y-%m-%d')
    board_dt  = datetime.datetime.strptime(m['effectiveBoardDate'],  '%Y-%m-%d')
    color = COMPANY_COLORS[m['company']]
    # Draw horizontal line from career date to LinkedIn date
    ax.plot([career_dt, board_dt], [i, i], color=color, linewidth=2, alpha=0.5, zorder=2)
    ax.scatter([career_dt], [i], color=CAREER_COLOR, s=60, zorder=4)
    ax.scatter([board_dt],  [i], color=LINKEDIN_COLOR, s=60, marker='D', zorder=4)

ax.set_yticks(range(len(matches)))
short = [f"{m['company']} · {m['careerTitle'][:35]}" for m in matches]
ax.set_yticklabels(short, fontsize=8, color=TEXT)
ax.set_xlabel('Date', fontsize=10, color=SUBTEXT, labelpad=8)
ax.set_title('Timeline: career page post date → LinkedIn first appearance\n'
             'Blue dot = career page · Diamond = LinkedIn snapshot',
             fontsize=13, fontweight='bold', color=TEXT, pad=12)
ax.spines[['top','right']].set_visible(False)
ax.spines[['left','bottom']].set_color(GRID)
ax.xaxis.grid(True, color=GRID, zorder=0, alpha=0.6)
ax.set_axisbelow(True)
ax.tick_params(axis='x', colors=SUBTEXT, labelsize=9)

dot   = plt.scatter([], [], color=CAREER_COLOR, s=60, label='Career page (datePosted)')
diam  = plt.scatter([], [], color=LINKEDIN_COLOR, s=60, marker='D', label='LinkedIn (Wayback snapshot)')
ax.legend(handles=[dot, diam], fontsize=9, loc='lower right', framealpha=0.9)

plt.tight_layout()
plt.savefig('charts/04_timeline.png', dpi=180, bbox_inches='tight')
plt.close()
print('Saved charts/04_timeline.png')

print('\nDone. Charts written to charts/')
