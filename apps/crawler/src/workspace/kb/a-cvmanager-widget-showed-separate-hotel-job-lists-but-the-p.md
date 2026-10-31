---
step: test_config
symptom: A CVManager widget showed separate hotel job lists, but the public GetPublicVacanciesByCompany response included both hotels and spontaneous-application forms.
tags: ['cvmanager', 'api-sniffer', 'shared-board', 'non-job-filter']
---
# A CVManager widget showed separate hotel job lists, but the public GetPublicVacanciesByCompany response included both hotels and spontaneous-application forms.

## Problem
A CVManager widget showed separate hotel job lists, but the public GetPublicVacanciesByCompany response included both hotels and spontaneous-application forms.

## Solution
Use the shared companyId API as one company-wide api_sniffer monitor. Map VacancyMultilanguage[0] fields and BranchCity, build the canonical cvmanager.ch job URL from JobadUrl, and exclude only JobadUrl paths containing /spontanbewerbung/. Verify the filtered count against both official hotel pages.
