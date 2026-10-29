---
step: configure
symptom: Oracle career sites return clean detail records but native detail extraction omits available WorkplaceTypeCode and corporate benefits text; listing buttons on official marketing pages resolve to dialog anchors.
tags: ['oracle_hcm', 'api_sniffer', 'board-discovery', 'field-mapping']
---
# Oracle career sites return clean detail records but native detail extraction omits available WorkplaceTypeCode and corporate benefits text; listing buttons on official marketing pages resolve to dialog anchors.

## Problem
Oracle career sites return clean detail records but native detail extraction omits available WorkplaceTypeCode and corporate benefits text; listing buttons on official marketing pages resolve to dialog anchors.

## Solution
Verified on Summit Companies issue 9643: ws probe api saves official careers HTML for manual inspection of dialog target URLs. The group uses five distinct Oracle sites CX_1 through CX_5; National Accounts and Consulting share CX_3. Native oracle_hcm monitoring plus HTTP api_sniffer detail extraction at recruitingCEJobRequisitionDetails, json_path items[0], preserves job and corporate description sections and maps ORA_ON_SITE/ORA_REMOTE/ORA_HYBRID. Preserve null workplace values; check source descriptions for API schedule/workplace contradictions. CSV validation rejects valid_through in enrich, so omit that enrich entry even if the scraper fields reference ExternalPostedEndDate.
