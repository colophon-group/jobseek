---
step: add_boards
symptom: Beisen branded subdomains can look like separate employer or regional boards: CICC cicc.zhiye.com and ciccwm.zhiye.com.
tags: ['beisen', 'boards', 'overlap', 'mirror']
---
# Beisen branded subdomains can look like separate employer or regional boards: CICC cicc.zhiye.com and ciccwm.zhiye.com.

## Problem
Beisen branded subdomains can look like separate employer or regional boards: CICC cicc.zhiye.com and ciccwm.zhiye.com.

## Solution
Compare public bootstrap identity and live jobAdIds before adding boards. For CICC both roots returned byte-identical HTML, tenant 108394 and PortalId 8e5a323a-a6e8-4a37-9ff2-018191167d45; wealth pages and multilingual pages were subsets of the same central worldwide feed. Retain only the central board. Do not assume all Beisen subdomains are mirrors; verify each case.
