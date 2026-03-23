---
type: case-study
company: slaughter-and-may
monitor: dom
scraper: dom
summary: "ASP.NET WebForms with Telerik RadGrid ATS. No anchor links to job detail pages - all navigation via postback buttons. Used Telerik client API (set_pageSize/fireCommand) to show all jobs on one page, then injected anchor tags from print button onclick attributes containing VacancyIds. PrintVacancy.aspx pages have minimal content (title + 1-2 sentence teaser), full JDs in linked PDFs. Pagination via GET/POST/JS click all failed due to ViewState requirements; only Telerik client API worked."
tags: ['aspnet', 'telerik', 'radgrid', 'postback', 'pagination', 'custom-ats']
---
# Slaughter-And-May — ASP.NET WebForms with Telerik RadGrid ATS. No anchor links to job detail pages - all navigation via postback buttons. Used Telerik client API (set_pageSize/fireCommand) to show all jobs on one page, then injected anchor tags from print button onclick attributes containing VacancyIds. PrintVacancy.aspx pages have minimal content (title + 1-2 sentence teaser), full JDs in linked PDFs. Pagination via GET/POST/JS click all failed due to ViewState requirements; only Telerik client API worked.

## Setup
- Monitor: dom
- Scraper: dom

## Key decisions
<!-- What was non-obvious? What was tried? What worked? -->

## Config
<!-- Final scraper_config / monitor_config JSON -->
