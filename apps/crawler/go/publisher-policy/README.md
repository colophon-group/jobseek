# Available resource-level publisher signals

This package preserves the shared Python TDM header/meta check for held browser
results. It accepts only the bounded `ResourcePolicySignals` contract, parses
at most 65,536 Unicode characters, uses the last valid HTML reservation over a
header, and ignores script/style/comment literals. Duplicate meta attributes
retain Python's last-value behavior. It opens no connection and follows no
policy URL.

`testdata/collect_python.py` freezes 21 cases from `src/shared/tdm.py` without
publisher traffic. The Go test replays those cases and rejects invalid UTF-8,
NUL, oversized and unknown signal fields. This is available-resource coverage;
it does not implement origin-file policies or a rights register.

The executor owns fenced persistence and downstream schedule exclusion. A
reservation must not delist the job or replace retained descriptive content.
The supervisor uses this check before a challenge retry, preserving the held
result for the executor to record instead of causing another origin request.
