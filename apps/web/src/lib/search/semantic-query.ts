export type TokenizedSemanticSearchQuery = {
  segmentWords: string[][];
  singles: string[];
  allCandidates: string[];
};

/**
 * Canonical tokenizer shared by semantic parsing and its server-action bound.
 * Keep delimiter and candidate construction changes centralized here so input
 * validation always measures the exact work the parser will perform.
 */
export function tokenizeSemanticSearchQuery(
  input: string,
): TokenizedSemanticSearchQuery {
  const segments = input
    .split(/[,\n\r\t/|]+|-+/)
    .map((part) => part.trim())
    .filter(Boolean);
  const segmentWords = segments.map((segment) =>
    segment.split(/\s+/).filter(Boolean),
  );
  const singleSet = new Set<string>();
  const allCandidateSet = new Set<string>();
  for (const words of segmentWords) {
    for (const word of words) {
      singleSet.add(word);
      allCandidateSet.add(word);
    }
    for (let index = 0; index < words.length - 1; index += 1) {
      allCandidateSet.add(`${words[index]} ${words[index + 1]}`);
    }
    if (words.length <= 10) {
      for (let index = 0; index < words.length - 2; index += 1) {
        allCandidateSet.add(
          `${words[index]} ${words[index + 1]} ${words[index + 2]}`,
        );
      }
    }
  }
  return {
    segmentWords,
    singles: [...singleSet],
    allCandidates: [...allCandidateSet],
  };
}

/** Exact fan-out dimensions used by the canonical semantic parser. */
export function getSemanticSearchQueryComplexity(input: string): {
  uniqueTerms: number;
  occupationCandidates: number;
  maxTermLength: number;
} {
  const tokenized = tokenizeSemanticSearchQuery(input);
  return {
    uniqueTerms: tokenized.singles.length,
    occupationCandidates: tokenized.allCandidates.length,
    maxTermLength: tokenized.singles.reduce(
      (maximum, term) => Math.max(maximum, term.length),
      0,
    ),
  };
}
