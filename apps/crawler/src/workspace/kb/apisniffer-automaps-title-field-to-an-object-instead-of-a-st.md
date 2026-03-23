---
step: select_monitor
symptom: api_sniffer auto-maps title field to an object instead of a string (title contains {id, name, isValid} dict)
tags: ['api_sniffer', 'field_mapping', 'nested_fields']
---
# api_sniffer auto-maps title field to an object instead of a string (title contains {id, name, isValid} dict)

## Problem
api_sniffer auto-maps title field to an object instead of a string (title contains {id, name, isValid} dict)

## Solution
Fix field mapping to use nested path: title.name instead of title. Check extracted content for dict/object values and use dot-notation to reach the string field.
