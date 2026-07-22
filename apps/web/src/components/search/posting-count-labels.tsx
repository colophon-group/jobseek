import { Plural } from "@lingui/react/macro";

export function ActivePostingCount({ count }: { count: number }) {
  return (
    <Plural
      id="common.stats.activeCount"
      comment="Locale-formatted active posting count"
      value={count}
      one="# active job"
      other="# active jobs"
    />
  );
}

export function YearPostingCount({ count }: { count: number }) {
  return (
    <Plural
      id="common.stats.yearCountWithValue"
      comment="Locale-formatted postings seen in the last year count"
      value={count}
      one="# in the last year"
      other="# in the last year"
    />
  );
}
