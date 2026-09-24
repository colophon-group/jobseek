import { tokenize } from "./jev-routing-core.mjs";

// Editorial labels written before running catalog6 against this set.
// Filter and discard spans are independently marked; every other word stays
// a keyword. The holdout split must not be used to revise the prompt.
const cases = [
  ["t01", "tune", "verbose", "Find an on site compliance officer role in Munich", "en", [["on site", "onsite"], ["compliance officer", "occupation"], ["Munich", "location"]], ["Find an", "role in"]],
  ["t02", "tune", "verbose", "Looking for legal counsel positions in Vienna", "en", [["legal counsel", "occupation"], ["Vienna", "location"]], ["Looking for", "positions in"]],
  ["t03", "tune", "verbose", "please find me remote Python work", "en", [["remote", "remote"], ["Python", "technology"]], ["please find me", "work"]],
  ["t04", "tune", "terse", "junior accountant Zurich hybrid", "en", [["junior", "seniority"], ["accountant", "occupation"], ["Zurich", "location"], ["hybrid", "hybrid"]], []],
  ["t05", "tune", "unsupported", "nurse Berlin part time", "en", [["Berlin", "location"], ["part time", "employmentType"]], []],
  ["t06", "tune", "informational", "what is a compliance officer", "en", [], []],
  ["t07", "tune", "terse", "senior pharmacist Berlin remote", "en", [["senior", "seniority"], ["pharmacist", "occupation"], ["Berlin", "location"], ["remote", "remote"]], []],
  ["t08", "tune", "unsupported", "delivery driver jobs near Paris", "en", [["Paris", "location"]], ["jobs near"]],
  ["t09", "tune", "unsupported", "part time cashier jobs", "en", [["part time", "employmentType"]], ["jobs"]],
  ["t10", "tune", "verbose", "sales manager jobs near Paris", "en", [["sales manager", "occupation"], ["Paris", "location"]], ["jobs near"]],
  ["t11", "tune", "meaningful", "remote product manager no startup", "en", [["remote", "remote"], ["product manager", "occupation"]], []],
  ["t12", "tune", "ambiguous", "Find an accountant in New York", "en", [["accountant", "occupation"], ["New York", "location"]], ["Find an", "in"]],
  ["t13", "tune", "informational", "Which skills does a recruiter need", "en", [], []],
  ["t14", "tune", "terse", "hr manager Basel contract", "en", [["hr manager", "occupation"], ["Basel", "location"], ["contract", "employmentType"]], []],
  ["t15", "tune", "verbose", "software engineer jobs with Rust in Amsterdam", "en", [["software engineer", "occupation"], ["Rust", "technology"], ["Amsterdam", "location"]], ["jobs with", "in"]],
  ["t16", "tune", "verbose", "I want warehouse associate work in London", "en", [["warehouse associate", "occupation"], ["London", "location"]], ["I want", "work in"]],
  ["h01", "holdout", "verbose", "Show me entry level warehouse associate jobs in Basel", "en", [["entry level", "seniority"], ["warehouse associate", "occupation"], ["Basel", "location"]], ["Show me", "jobs in"]],
  ["h02", "holdout", "verbose", "I want a contract recruiter in London", "en", [["contract", "employmentType"], ["recruiter", "occupation"], ["London", "location"]], ["I want a", "in"]],
  ["h03", "holdout", "verbose", "Marketing manager hybrid roles around Amsterdam", "en", [["Marketing manager", "occupation"], ["hybrid", "hybrid"], ["Amsterdam", "location"]], ["roles around"]],
  ["h04", "holdout", "verbose", "Find software engineer jobs using React in Berlin", "en", [["software engineer", "occupation"], ["React", "technology"], ["Berlin", "location"]], ["Find", "jobs using", "in"]],
  ["h05", "holdout", "informational", "What does a pharmacist do", "en", [], []],
  ["h06", "holdout", "unsupported", "nurse Zurich remote", "en", [["Zurich", "location"], ["remote", "remote"]], []],
  ["h07", "holdout", "typo", "sr acctnt munich", "en", [["sr", "seniority"], ["acctnt", "occupation"], ["munich", "location"]], []],
  ["h08", "holdout", "verbose", "Customer success manager roles in Dublin please", "en", [["Customer success manager", "occupation"], ["Dublin", "location"]], ["roles in", "please"]],
  ["h09", "holdout", "ambiguous", "consultant positions in New York", "en", [["consultant", "occupation"], ["New York", "location"]], ["positions in"]],
  ["h10", "holdout", "verbose", "Get me temporary auditor work from home", "en", [["temporary", "employmentType"], ["auditor", "occupation"], ["work from home", "remote"]], ["Get me"]],
  ["h11", "holdout", "multilingual", "data analyst jobs Zürich", "en", [["data analyst", "occupation"], ["Zürich", "location"]], ["jobs"]],
  ["h12", "holdout", "meaningful", "jobs at Google for legal counsel", "en", [["legal counsel", "occupation"]], ["jobs at", "for"]],
  ["h13", "holdout", "typo", "Find me pharmacst in Bern", "en", [["pharmacst", "occupation"], ["Bern", "location"]], ["Find me", "in"]],
  ["h14", "holdout", "informational", "salary for accountant in Zurich", "en", [], []],
  ["h15", "holdout", "unsupported", "remote logistics coordinator jobs Berlin", "en", [["remote", "remote"], ["Berlin", "location"]], ["jobs"]],
  ["h16", "holdout", "multilingual", "cherche un poste de comptable à Genève", "fr", [["comptable", "occupation"], ["Genève", "location"]], ["cherche un poste de", "à"]],
];

function locate(query, text) {
  const words = text.split(/\s+/);
  const segments = tokenize(query);
  const found = [];
  segments.forEach((segmentWords, segment) => {
    for (let start = 0; start <= segmentWords.length - words.length; start++) {
      if (words.every((word, index) => word.toLowerCase() === segmentWords[start + index].toLowerCase())) {
        found.push({ text: segmentWords.slice(start, start + words.length).join(" "), segment, start, end: start + words.length });
      }
    }
  });
  if (found.length !== 1) throw new Error(`${query}: expected one occurrence of ${text}, got ${found.length}`);
  return found[0];
}

export const records = cases.map(([id, split, group, q, locale, filters, discarded]) => {
  const terms = filters.map(([text, category]) => ({ ...locate(q, text), category }));
  const discardSpans = discarded.map((text) => locate(q, text));
  const segments = tokenize(q);
  const used = segments.map((words) => words.map(() => false));
  for (const span of [...terms, ...discardSpans]) {
    for (let index = span.start; index < span.end; index++) {
      if (used[span.segment][index]) throw new Error(`${id}: overlapping gold spans`);
      used[span.segment][index] = true;
    }
  }
  const seen = new Set();
  const keywords = segments.flatMap((words, segment) => words.filter((word, index) => !used[segment][index]))
    .filter((word) => {
      if (seen.has(word.toLowerCase())) return false;
      seen.add(word.toLowerCase());
      return true;
    });
  return { id, split, group, q, locale, terms, discardSpans, keywords };
});
