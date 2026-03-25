// ../../shared/signalActor.ts
import { Actor as Actor2 } from "apify";

// ../../shared/constants.ts
var DATASETS = {
  SIGNALS: "hiring-signals",
  OUTREACH: "outreach-ready"
};

// ../../shared/storage.ts
import { Actor } from "apify";
async function pushDataWithFallback(data, preferredName) {
  const defaultDataset = await Actor.openDataset();
  for (const item of data) {
    await defaultDataset.pushData(item);
  }
  if (!preferredName) return;
  try {
    const namedDataset = await Actor.openDataset(preferredName);
    if (namedDataset.id !== defaultDataset.id) {
      for (const item of data) {
        await namedDataset.pushData(item);
      }
    }
  } catch (err) {
    console.warn(`Skipped writing to named dataset '${preferredName}':`, err);
  }
}

// ../../shared/signalActor.ts
async function runSignalActor(discover) {
  await Actor2.init();
  const input = await Actor2.getInput() ?? {};
  const signals = await discover(input);
  const seen = /* @__PURE__ */ new Set();
  const unique = signals.filter((s) => {
    if (seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });
  console.log(`Emitting ${unique.length} signals (${signals.length - unique.length} duplicates removed)`);
  await pushDataWithFallback(unique, DATASETS.SIGNALS);
  await Actor2.exit();
}

// ../../shared/id.ts
import { createHash } from "crypto";
function signalId(...parts) {
  return createHash("sha256").update(parts.join(":")).digest("hex").slice(0, 16);
}

// ../../shared/utils.ts
function guessDomain(name) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 30);
  return `${slug}.com`;
}
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// src/parser.ts
var EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index";
var HIRING_PHRASES = [
  "we expect to hire",
  "expanding our team",
  "we plan to hire",
  "workforce expansion",
  "we intend to grow",
  "increase our headcount",
  "we are growing our team",
  "additional employees",
  "talent acquisition"
];
function parseDisplayName(raw) {
  return raw.replace(/\s*\(CIK\s+\d+\)\s*$/i, "").replace(/\s*\([A-Z]{1,6}\)\s*$/i, "").replace(/\s+/g, " ").trim();
}
async function parseEdgarFilings(companies, lookbackDays) {
  const signals = [];
  const cutoff = /* @__PURE__ */ new Date();
  cutoff.setDate(cutoff.getDate() - lookbackDays);
  for (const phrase of HIRING_PHRASES) {
    const encodedPhrase = encodeURIComponent(`"${phrase}"`);
    const url = `${EDGAR_BASE}?q=${encodedPhrase}&forms=10-K,10-Q`;
    try {
      const response = await fetch(url, {
        headers: {
          "User-Agent": "hiring-signal-engine/1.0 (research@example.com)",
          Accept: "application/json"
        }
      });
      if (!response.ok) {
        console.warn(`EDGAR search failed for "${phrase}": ${response.status}`);
        continue;
      }
      const data = await response.json();
      const hits = data.hits?.hits ?? [];
      console.log(`"${phrase}": ${hits.length} hits (total: ${data.hits?.total?.value ?? "?"})`);
      for (const hit of hits) {
        const src = hit._source;
        const rawName = src.display_names?.[0];
        if (!rawName) continue;
        const entityName = parseDisplayName(rawName);
        const filingDate = src.file_date ?? src.period_ending ?? "";
        if (!filingDate) continue;
        const filingDateObj = new Date(filingDate);
        if (filingDateObj < cutoff) continue;
        if (companies.length > 0) {
          const matches = companies.some(
            (c) => entityName.toLowerCase().includes(c.toLowerCase()) || c.toLowerCase().includes(entityName.toLowerCase())
          );
          if (!matches) continue;
        }
        const formType = src.form ?? src.root_forms?.[0] ?? "10-K";
        const domain = guessDomain(entityName);
        const adsh = src.adsh ?? "";
        const sourceUrl = adsh ? `https://www.sec.gov/Archives/edgar/data/${adsh.replace(/-/g, "/")}` : `https://efts.sec.gov/LATEST/search-index?q=${encodeURIComponent(hit._id)}`;
        signals.push({
          id: signalId(entityName, "sec_filing", filingDate),
          company: entityName,
          company_domain: domain,
          signal_type: "sec_filing",
          signal_text: `${entityName} mentioned "${phrase}" in their ${formType} filing dated ${filingDate}`,
          source_url: sourceUrl,
          date: filingDateObj.toISOString(),
          raw: {
            form_type: formType,
            file_date: filingDate,
            period_ending: src.period_ending,
            matched_phrase: phrase,
            file_num: src.file_num,
            biz_location: src.biz_locations?.[0],
            edgar_id: hit._id
          }
        });
      }
    } catch (err) {
      console.error(`Error querying EDGAR for "${phrase}":`, err);
    }
    await sleep(300);
  }
  if (companies.length > 0) {
    for (const company of companies) {
      const companySignals = await searchByCompany(company, cutoff);
      signals.push(...companySignals);
    }
  }
  const seen = /* @__PURE__ */ new Set();
  return signals.filter((s) => {
    if (seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });
}
async function searchByCompany(company, cutoff) {
  const signals = [];
  const url = `${EDGAR_BASE}?q=${encodeURIComponent(`"${company}"`)}&forms=10-K,10-Q`;
  try {
    const response = await fetch(url, {
      headers: {
        "User-Agent": "hiring-signal-engine/1.0 (research@example.com)",
        Accept: "application/json"
      }
    });
    if (!response.ok) return signals;
    const data = await response.json();
    const hits = data.hits?.hits ?? [];
    for (const hit of hits.slice(0, 10)) {
      const src = hit._source;
      const rawName = src.display_names?.[0];
      const entityName = rawName ? parseDisplayName(rawName) : company;
      const filingDate = src.file_date ?? src.period_ending ?? "";
      if (!filingDate || new Date(filingDate) < cutoff) continue;
      const formType = src.form ?? "10-K";
      signals.push({
        id: signalId(entityName, "sec_filing", filingDate, "company_search"),
        company: entityName,
        company_domain: guessDomain(entityName),
        signal_type: "sec_filing",
        signal_text: `${entityName} filed ${formType} on ${filingDate} \u2014 review for growth/hiring language`,
        source_url: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=${encodeURIComponent(company)}&type=${formType}&dateb=&owner=include&count=10`,
        date: new Date(filingDate).toISOString(),
        raw: {
          form_type: formType,
          file_date: filingDate,
          search_term: company
        }
      });
    }
  } catch (err) {
    console.error(`Error searching EDGAR for company "${company}":`, err);
  }
  return signals;
}

// src/main.ts
runSignalActor(async (input) => {
  const { companies = [], lookbackDays = 30 } = input;
  console.log(`sec-edgar-actor: companies=${companies.length > 0 ? companies.join(", ") : "all"}, lookbackDays=${lookbackDays}`);
  return parseEdgarFilings(companies, lookbackDays);
});
