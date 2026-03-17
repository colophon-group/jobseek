// src/main.ts
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

// src/sources/crunchbase.ts
import { createHash } from "crypto";
async function parseCrunchbase(apiKey, minAmount, roundTypes2, lookbackDays2, categories) {
  const signals = [];
  const startDate = /* @__PURE__ */ new Date();
  startDate.setDate(startDate.getDate() - lookbackDays2);
  const startDateStr = startDate.toISOString().split("T")[0];
  const queryPredicates = [
    {
      type: "predicate",
      field_id: "announced_on",
      operator_id: "gte",
      values: [startDateStr]
    },
    {
      type: "predicate",
      field_id: "investment_type",
      operator_id: "includes",
      values: roundTypes2
    },
    {
      type: "predicate",
      field_id: "money_raised",
      operator_id: "gte",
      values: [minAmount]
    }
  ];
  if (categories && categories.length > 0) {
    queryPredicates.push({
      type: "predicate",
      field_id: "funded_organization_categories",
      operator_id: "includes",
      values: categories
    });
  }
  const requestBody = {
    field_ids: [
      "identifier",
      "announced_on",
      "investment_type",
      "money_raised",
      "funded_organization_identifier",
      "funded_organization_location",
      "short_description"
    ],
    query: queryPredicates,
    order: [{ field_id: "announced_on", sort: "desc" }],
    limit: 100
    // Crunchbase max per page
  };
  let after;
  let hasMore = true;
  while (hasMore) {
    const body = { ...requestBody };
    if (after) body["after_id"] = after;
    const response = await fetch(
      `https://api.crunchbase.com/api/v4/searches/funding_rounds?user_key=${apiKey}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }
    );
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Crunchbase API error ${response.status}: ${errorText}`);
    }
    const data = await response.json();
    if (!data.entities || data.entities.length === 0) {
      hasMore = false;
      break;
    }
    for (const entity of data.entities) {
      const props = entity.properties;
      const company = props.funded_organization_identifier?.value ?? "Unknown";
      const permalink = props.funded_organization_identifier?.permalink ?? "";
      const domain = derivedomainFromPermalink(permalink);
      const announcedOn = props.announced_on;
      const investmentType = props.investment_type ?? "unknown";
      const amountUsd = props.money_raised?.value_usd ?? 0;
      const amountFormatted = formatCurrency(amountUsd);
      const signalText = `${company} announced a ${formatRoundType(investmentType)} of ${amountFormatted}`;
      const sourceUrl = `https://www.crunchbase.com/funding_round/${entity.identifier?.value ?? permalink}`;
      const id = createHash("sha256").update(`${company}:funding:${announcedOn}`).digest("hex").slice(0, 16);
      const signal = {
        id,
        company,
        company_domain: domain,
        signal_type: "funding",
        signal_text: signalText,
        source_url: sourceUrl,
        date: new Date(announcedOn).toISOString(),
        raw: {
          investment_type: investmentType,
          money_raised_usd: amountUsd,
          permalink,
          short_description: props.short_description ?? ""
        }
      };
      signals.push(signal);
    }
    if (data.entities.length < 100) {
      hasMore = false;
    } else {
      const lastEntity = data.entities[data.entities.length - 1];
      after = lastEntity.identifier?.value;
      if (!after) hasMore = false;
    }
  }
  return signals;
}
function derivedomainFromPermalink(permalink) {
  if (!permalink) return "";
  const slug = permalink.replace(/[^a-z0-9-]/gi, "").toLowerCase();
  return `${slug}.com`;
}
function formatCurrency(amount) {
  if (amount >= 1e9) return `$${(amount / 1e9).toFixed(1)}B`;
  if (amount >= 1e6) return `$${(amount / 1e6).toFixed(0)}M`;
  return `$${amount.toLocaleString()}`;
}
function formatRoundType(type) {
  return type.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

// src/sources/rss.ts
import Parser from "rss-parser";
import { createHash as createHash2 } from "crypto";
var RSS_FEEDS = [
  // General startup/venture
  {
    name: "TechCrunch Venture",
    url: "https://techcrunch.com/category/startups/feed/"
  },
  {
    name: "Crunchbase News",
    url: "https://news.crunchbase.com/feed/"
  },
  // EU-focused tech/startup coverage
  {
    name: "Sifted EU",
    url: "https://sifted.eu/feed"
  },
  {
    name: "EU Startups",
    url: "https://eu-startups.com/feed/"
  },
  {
    name: "Tech EU",
    url: "https://tech.eu/feed/"
  },
  // Crypto / Blockchain / Web3
  {
    name: "The Block",
    url: "https://www.theblock.co/rss.xml"
  },
  {
    name: "CoinTelegraph",
    url: "https://cointelegraph.com/rss"
  },
  {
    name: "Decrypt",
    url: "https://decrypt.co/feed"
  }
];
var FUNDING_PATTERNS = {
  company: [
    /^([A-Z][a-zA-Z0-9\s.,'&-]+?)\s+(?:raises?|secures?|lands?|closes?|announces?)/i,
    /^([A-Z][a-zA-Z0-9\s.,'&-]+?),\s+/i
  ],
  amount: [
    /\$(\d+(?:\.\d+)?)\s*(billion|million|[BM])\b/i,
    /raises?\s+\$(\d+(?:\.\d+)?)\s*(billion|million|[BM])/i,
    /(?:EUR|€)\s*(\d+(?:\.\d+)?)\s*(billion|million|[BM])\b/i,
    /(\d+(?:\.\d+)?)\s*(?:million|billion)\s*(?:euro|EUR|dollars?|USD)/i
  ],
  roundType: [
    /(seed|pre-seed|series\s+[a-g]|series[a-g]|growth|late.stage|bridge|ipo|spac)/i
  ]
};
async function parseRssFeeds(lookbackDays2) {
  const parser = new Parser({
    customFields: {
      item: ["content", "summary"]
    },
    timeout: 15e3
  });
  const cutoff = /* @__PURE__ */ new Date();
  cutoff.setDate(cutoff.getDate() - lookbackDays2);
  const signals = [];
  for (const feed of RSS_FEEDS) {
    console.log(`Fetching RSS feed: ${feed.name} (${feed.url})`);
    try {
      const result = await parser.parseURL(feed.url);
      for (const item of result.items) {
        const pubDate = item.pubDate ? new Date(item.pubDate) : null;
        if (pubDate && pubDate < cutoff) continue;
        const title = item.title ?? "";
        const description = item.contentSnippet ?? item.summary ?? item.content ?? "";
        const fullText = `${title} ${description}`;
        const extracted = extractFundingInfo(fullText);
        if (!extracted) continue;
        const { company, amountFormatted, roundType } = extracted;
        const articleDate = pubDate?.toISOString() ?? (/* @__PURE__ */ new Date()).toISOString();
        const domain = guessDomainFromCompany(company);
        const signalText = [
          `${company} announced`,
          roundType ? `a ${roundType}` : "a funding round",
          amountFormatted ? `of ${amountFormatted}` : ""
        ].filter(Boolean).join(" ");
        const id = createHash2("sha256").update(`${company}:funding:${articleDate.split("T")[0]}`).digest("hex").slice(0, 16);
        const signal = {
          id,
          company,
          company_domain: domain,
          signal_type: "funding",
          signal_text: signalText,
          source_url: item.link ?? feed.url,
          date: articleDate,
          raw: {
            feed: feed.name,
            title,
            description: description.slice(0, 500),
            amount_formatted: amountFormatted,
            round_type: roundType
          }
        };
        signals.push(signal);
      }
    } catch (err) {
      console.error(`Error parsing RSS feed ${feed.name}:`, err);
    }
  }
  return signals;
}
function extractFundingInfo(text) {
  const fundingVerbs = /\b(raises?|secures?|lands?|closes?|funding|funded|investment|raised|announced|backs?|token sale|token round|seed round|series|pre-seed)\b/i;
  if (!fundingVerbs.test(text)) return null;
  let company = null;
  for (const pattern of FUNDING_PATTERNS.company) {
    const match = text.match(pattern);
    if (match?.[1]) {
      company = match[1].trim().replace(/\s+/g, " ");
      break;
    }
  }
  if (!company || company.length < 2 || company.length > 60) return null;
  let amountFormatted = null;
  for (const pattern of FUNDING_PATTERNS.amount) {
    const match = text.match(pattern);
    if (match) {
      const num = parseFloat(match[1]);
      const unit = match[2]?.toLowerCase();
      if (unit === "billion" || unit === "b") {
        amountFormatted = `$${num}B`;
      } else if (unit === "million" || unit === "m") {
        amountFormatted = `$${num}M`;
      }
      break;
    }
  }
  let roundType = null;
  const rtMatch = text.match(FUNDING_PATTERNS.roundType[0]);
  if (rtMatch?.[1]) {
    roundType = rtMatch[1].replace(/\s+/g, " ").split(" ").map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(" ");
  }
  return { company, amountFormatted, roundType };
}
function guessDomainFromCompany(company) {
  const slug = company.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 30);
  return `${slug}.com`;
}

// src/main.ts
await Actor2.init();
var input = await Actor2.getInput() ?? {};
var {
  crunchbaseApiKey,
  minRoundAmountUsd = 1e6,
  roundTypes = ["seed", "pre_seed", "series_a", "series_b", "series_c", "series_d", "series_e"],
  lookbackDays = 14,
  fundingCategories
} = input;
console.log(`Starting funding-news-actor with lookbackDays=${lookbackDays}, minRoundAmountUsd=${minRoundAmountUsd}, categories=${JSON.stringify(fundingCategories ?? [])}`);
var allSignals = [];
if (crunchbaseApiKey) {
  console.log("Fetching signals from Crunchbase...");
  try {
    const crunchbaseSignals = await parseCrunchbase(crunchbaseApiKey, minRoundAmountUsd, roundTypes, lookbackDays, fundingCategories);
    console.log(`Got ${crunchbaseSignals.length} signals from Crunchbase`);
    allSignals.push(...crunchbaseSignals);
  } catch (err) {
    console.error("Error fetching from Crunchbase:", err);
  }
} else {
  console.warn("No crunchbaseApiKey provided, skipping Crunchbase source");
}
console.log("Fetching signals from RSS feeds...");
try {
  const rssSignals = await parseRssFeeds(lookbackDays);
  console.log(`Got ${rssSignals.length} signals from RSS feeds`);
  allSignals.push(...rssSignals);
} catch (err) {
  console.error("Error fetching from RSS feeds:", err);
}
var signalMap = /* @__PURE__ */ new Map();
for (const signal of allSignals) {
  if (!signalMap.has(signal.id)) {
    signalMap.set(signal.id, signal);
  }
}
var deduped = Array.from(signalMap.values());
console.log(`Total unique signals after deduplication: ${deduped.length}`);
await pushDataWithFallback(deduped, DATASETS.SIGNALS);
console.log(`Pushed ${deduped.length} signals to dataset '${DATASETS.SIGNALS}'`);
await Actor2.exit();
