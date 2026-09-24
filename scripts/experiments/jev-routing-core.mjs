import { readFileSync } from "node:fs";

const occupationCatalog = JSON.parse(readFileSync(new URL("./jev-occupation-catalog.json", import.meta.url), "utf8"));

const CATEGORIES = {
  keyword: "Keep this text in keyword search; it is filler, unrelated, or only part of another selected span.",
  location: "A location to resolve against the job location taxonomy.",
  occupation: "A job occupation or title to resolve against the occupation taxonomy.",
  seniority: "A career level to resolve against the seniority taxonomy.",
  technology: "A named technology or tool to resolve against the technology taxonomy.",
  remote: "The remote or work-from-home work mode.",
  hybrid: "The hybrid work mode.",
  onsite: "The on-site or in-office work mode.",
  employmentType: "An employment type such as contract or part-time.",
};

const POLICIES = {
  natural: "Route search text into useful job filters. Use the whole query to understand intent. Prefer the longest meaningful span and classify its shorter overlapping spans as keyword. A clear typo or abbreviation may still indicate a filter. Unrelated requests should stay as keywords. Never invent a filter outside the listed categories.",
  literal: "Reproduce a literal search parser rather than correcting the user. Route a location, seniority, or technology only when its single-word text exactly names a taxonomy item or known alias. Occupations may be one to three words, but choose the longest exact name or alias; shorter overlapping spans should stay as keywords. Recognize remote, wfh, hybrid, onsite, work from home, on site, and in office literally. Do not repair typos, infer unstated filters, remove filler words, or use semantic synonyms. Even an unrelated query can contain a literal filter name.",
  minimal: "Identify the user's intended JOB SEARCH filters. Each filter uses the SHORTEST standalone span that expresses it. Route typos and common abbreviations by meaning. Use keyword for filler and non-filter spans. An occupation may span two or three words only when ALL words form ONE recognizable job title, such as 'data analyst', 'backend developer', or 'software engineer'. A location may span multiple words, such as 'New York' or 'San Francisco'. Never select a phrase containing separate filters, such as 'senior Python developer', 'react dev', 'sr swe', 'Zurich remote', or 'backend developer Zurich'. For those, choose the individual meaningful spans instead. When the whole query is unrelated to jobs, all spans are keywords, including words like remote, senior, Python, or Zurich. Choose employmentType for contract, part-time or equivalent terms. Do not choose a title category for generic filler such as job, role, positions, or 'nursing' when no matching occupation exists.",
  minimal2: "Identify the user's intended JOB SEARCH filters, including abbreviations and typos. Pick the SHORTEST exact text span for each separate filter. A technology name, even alone, is technology; a seniority word is seniority; a job title is occupation. NEVER combine a technology with a job title, a seniority with a job title, or a location with another filter. For example, 'Python developer' is TWO filters ('Python' technology and 'developer' occupation), not one occupation; 'senior accountant' is TWO filters. Use 2-3 word occupation spans ONLY when the entire phrase is one named occupation, such as 'data analyst' or 'platform engineer'. Use 2-3 word location spans for one place name such as 'New York'. Work modes include remote, hybrid, onsite and their synonyms or clear typos. Employment types are ONLY full-time, part-time, contract, temporary, or volunteer and direct translations; flexible hours is not an employment type. Generic numbers are not seniority. In a clearly unrelated request, route every span to keyword even when a word is also a technology or place. For every candidate phrase, choose keyword if it contains two different filter concepts, adds filler words, or is not itself a single filter.",
  broad3: "Interpret the query as a search for jobs across ALL career families: healthcare, finance, legal, operations, sales, marketing, design, research, construction, administration, and software. Select each filter's smallest complete phrase. An occupation may be a one-, two-, or three-word title; keep the WHOLE title together when all words name one occupation, including modifiers that specialize the job (for example, a manager of a particular function). A technology or tool, seniority, work mode, employment type, or place is a separate filter and must not be swallowed by a title phrase. Do not call a role modifier such as a job function a technology unless it is actually a named tool. For overlapping spans, select one complete occupation title and separate non-title filters, marking all other overlaps keyword. Recognize clear abbreviations, translations, and typos. Use employmentType only for full-time, part-time, contract, temporary, or volunteer. When an apparent title is not a recognized occupation in this platform, leave it as keyword rather than mapping it to a different job with one shared word. In informational or unrelated requests, every span remains keyword even if it contains a career term or a place. Never include filler words in a selected filter span.",
  catalog3: "Interpret the query as a search for jobs across ALL career families. The state lists the actual occupation taxonomy. Select each filter's smallest complete phrase. A one-, two-, or three-word occupation span must name one taxonomy occupation or a clear alias/translation/typo of it; do not accept a different role merely because one word overlaps a listed title. Keep a complete occupational title together, including its specializing words. Route a technology or tool, seniority, work mode, employment type, or place as its own span; never swallow it into an occupation. A role modifier such as a job function is not a technology unless it is a named tool. For overlaps, select one complete occupation and separate non-title filters, marking other spans keyword. Employment type is limited to full-time, part-time, contract, temporary, or volunteer. In an informational or unrelated request, every span is keyword even if a listed occupation or place appears. Never include filler words in a filter span.",
  catalog4: "Identify intended JOB SEARCH filters for every career family, using the occupation catalog in state. Choose the SHORTEST complete span for EACH independent filter. Keep all words of one listed occupation title together: a function or specialty followed by a role is one title when the catalog contains that role. A clear alias, abbreviation, translation, or typo of a catalog occupation may also be an occupation. Do not turn an unsupported title into another occupation that merely shares one word. A named technology/tool is a separate technology filter; a job specialty by itself is not a technology. Seniority, place, work mode, and employment type are separate filters and must NEVER be swallowed by an occupation phrase. For work modes select only the mode expression, not generic nearby words such as 'work' or 'jobs'. Employment types are only full-time, part-time, contract, temporary, and volunteer or direct translations. In informational or unrelated queries (questions about becoming a professional, advice, jokes, shopping, history), every span stays keyword, even a catalog title or place. For overlapping spans, mark the unselected overlaps keyword. Do not include filler words or neighboring filter concepts in any selected span.",
  catalog5: "Route a JOB SEARCH query into filters for all career families, using the occupation catalog in state. First decide if the WHOLE QUERY seeks jobs; informational, shopping, advice, history, and joke requests have NO filters. For a job search, choose the SHORTEST complete text span for each independent filter. Single words and common abbreviations/typos can be filters: seniority (senior, staff, junior, sr, jr), work mode (remote, wfh, hybrid, onsite and translations), named technologies/tools, or a known place. Employment type is limited to full-time, part-time, contract, temporary, and volunteer. An occupation is a complete one- to three-word job title in the catalog OR a clear alias, abbreviation, translation, or typo of one, across software, healthcare, finance, legal, sales, operations, administration, and other fields. Keep a complete role title together, including a specialty such as backend, product, or sales; these job specialties are not technologies by themselves. But a named technology/tool, seniority, work mode, employment type, or place NEXT TO a role is a SEPARATE filter and must not be swallowed by the occupation. If a title is unsupported, retain it as keyword; never map it to a different occupation with one shared word. Never include generic filler such as job, role, work, positions, in, or and inside a filter span. All unchosen overlapping spans are keyword.",
};

// The production search-query router consumes these through the checked-in
// generator so the evaluated catalog5 wording stays byte-for-byte identical.
export const JEV_ROUTING_CATEGORIES = CATEGORIES;
export const JEV_ROUTING_POLICY_CATALOG5 = POLICIES.catalog5;

export function tokenize(query) {
  return query.split(/[,\n\r\t/|]+|-+/).map((part) => part.trim()).filter(Boolean)
    .map((part) => part.split(/\s+/).filter(Boolean));
}

export function spansForQuery(query) {
  const segments = tokenize(query);
  const spans = [];
  for (let segment = 0; segment < segments.length; segment++) {
    const words = segments[segment];
    for (let start = 0; start < words.length; start++) {
      for (let length = 1; length <= 3 && start + length <= words.length; length++) {
        spans.push({
          id: `s_${spans.length}`,
          segment,
          start,
          end: start + length,
          text: words.slice(start, start + length).join(" "),
        });
      }
    }
  }
  return { segments, spans };
}

export function jevRoutingRequest(query, locale, variant) {
  if (!POLICIES[variant]) throw new Error(`Unknown routing variant ${variant}`);
  const { spans } = spansForQuery(query);
  return {
    model: "jev-1.13.0",
    state: {
      query,
      locale,
      policy: POLICIES[variant],
      ...(["catalog3", "catalog4", "catalog5"].includes(variant) ? {
        occupationCatalog: occupationCatalog.map((row) => row[locale] || row.en).join(" | "),
      } : {}),
      spans: spans.map(({ id, text, segment, start, end }) => ({ id, text, segment, start, end })),
    },
    questions: Object.fromEntries(spans.map((span) => [span.id, {
      type: "choice",
      instructions: `For span ${span.id} (${span.text}), choose its role in this exact query, following state.policy.`,
      criteria: CATEGORIES,
    }])),
  };
}

function normalizeLocationText(value) {
  return value.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]+/gu, "");
}

export function exactSuggestion(text, category, suggestions) {
  const lower = text.toLowerCase().trim();
  const normalized = normalizeLocationText(text);
  for (const item of suggestions ?? []) {
    if (category === "location") {
      const names = [item.name, item.slug, item.parentName ? `${item.name} ${item.parentName}` : null];
      if (names.some((name) => name && normalizeLocationText(name) === normalized)) return item;
    } else if (item.name?.toLowerCase() === lower || item.slug === lower || item.matchedName?.toLowerCase() === lower) {
      return item;
    }
  }
  return null;
}

function dedupeCaseInsensitive(values) {
  const seen = new Set();
  return values.filter((value) => {
    const key = value.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function categoryRequests(query, answers) {
  return spansForQuery(query).spans.flatMap((span) => {
    const category = answers[span.id]?.choice;
    if (!["location", "occupation", "seniority", "technology"].includes(category)) return [];
    return [{ key: span.id, category, text: span.text }];
  });
}

export function reconstruct(query, answers, candidates, options = {}) {
  const threshold = options.threshold ?? 0.5;
  const literalWorkMode = options.literalWorkMode ?? false;
  const longestFirst = options.longestFirst ?? true;
  const { segments, spans } = spansForQuery(query);
  const consumed = segments.map((words) => Array(words.length).fill(false));
  const selected = { locations: [], occupations: [], seniorities: [], technologies: [], workMode: [] };
  const selectedSpans = [];
  const seen = Object.fromEntries(Object.keys(selected).map((key) => [key, new Set()]));
  const workModeLiterals = {
    remote: new Set(["remote", "wfh", "work from home", "work-from-home"]),
    hybrid: new Set(["hybrid"]),
    onsite: new Set(["onsite", "on site", "on-site", "in office", "in-office"]),
  };
  const ordered = [...spans].sort((a, b) => {
    const lengthDifference = (b.end - b.start) - (a.end - a.start);
    if (longestFirst && lengthDifference) return lengthDifference;
    const confidenceDifference = (answers[b.id]?.probabilities?.[answers[b.id]?.choice] ?? 0)
      - (answers[a.id]?.probabilities?.[answers[a.id]?.choice] ?? 0);
    return confidenceDifference || a.segment - b.segment || a.start - b.start;
  });
  for (const span of ordered) {
    const answer = answers[span.id];
    const category = answer?.choice;
    const probability = answer?.probabilities?.[category] ?? 0;
    if (!category || category === "keyword" || probability < threshold) continue;
    if (consumed[span.segment].slice(span.start, span.end).some(Boolean)) continue;
    let field;
    let value;
    if (["remote", "hybrid", "onsite"].includes(category)) {
      if (literalWorkMode && !workModeLiterals[category].has(span.text.toLowerCase())) continue;
      field = "workMode";
      value = category;
    } else {
      const item = exactSuggestion(span.text, category, candidates[span.id] ?? []);
      if (!item) continue;
      field = { location: "locations", occupation: "occupations", seniority: "seniorities", technology: "technologies" }[category];
      value = item.slug;
    }
    if (!seen[field].has(value)) {
      seen[field].add(value);
      selected[field].push(value);
    }
    for (let index = span.start; index < span.end; index++) consumed[span.segment][index] = true;
    selectedSpans.push({ text: span.text, category, slug: value, probability });
  }
  const keywords = [];
  segments.forEach((words, segment) => words.forEach((word, index) => {
    if (!consumed[segment][index]) keywords.push(word);
  }));
  return {
    shape: { keywords: dedupeCaseInsensitive(keywords), ...selected, employmentTypes: [] },
    selectedSpans,
  };
}

export function selectedRouteSpans(query, answers, options = {}) {
  const threshold = options.threshold ?? 0.5;
  const longestFirst = options.longestFirst ?? true;
  const { segments, spans } = spansForQuery(query);
  const consumed = segments.map((words) => Array(words.length).fill(false));
  const ordered = [...spans].sort((a, b) => {
    const lengthDifference = (b.end - b.start) - (a.end - a.start);
    if (longestFirst && lengthDifference) return lengthDifference;
    const confidenceDifference = (answers[b.id]?.probabilities?.[answers[b.id]?.choice] ?? 0)
      - (answers[a.id]?.probabilities?.[answers[a.id]?.choice] ?? 0);
    return confidenceDifference || a.segment - b.segment || a.start - b.start;
  });
  const result = [];
  for (const span of ordered) {
    const answer = answers[span.id];
    const category = answer?.choice;
    if (!category || category === "keyword" ||
      (answer.probabilities?.[category] ?? 0) < threshold ||
      consumed[span.segment].slice(span.start, span.end).some(Boolean)) continue;
    for (let i = span.start; i < span.end; i++) consumed[span.segment][i] = true;
    result.push({ text: span.text, category, segment: span.segment, start: span.start, end: span.end });
  }
  return result.sort((a, b) => a.segment - b.segment || a.start - b.start);
}

export function sameShape(left, right) {
  const keys = ["keywords", "locations", "occupations", "seniorities", "technologies", "workMode", "employmentTypes"];
  return keys.every((key) => {
    const a = [...(left[key] ?? [])].sort();
    const b = [...(right[key] ?? [])].sort();
    return JSON.stringify(a) === JSON.stringify(b);
  });
}

export function fieldAgreement(prediction, gold) {
  return Object.fromEntries(
    ["keywords", "locations", "occupations", "seniorities", "technologies", "workMode", "employmentTypes"]
      .map((key) => [key, JSON.stringify([...(prediction[key] ?? [])].sort()) === JSON.stringify([...(gold[key] ?? [])].sort())]));
}
