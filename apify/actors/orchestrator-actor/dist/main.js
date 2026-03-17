// src/main.ts
import { Actor as Actor2 } from "apify";
import Anthropic from "@anthropic-ai/sdk";

// ../../shared/constants.ts
var SIGNAL_ROLE_MAP = {
  funding: ["CTO", "VP Engineering", "Head of Talent", "CEO"],
  sec_filing: ["VP Engineering", "Chief People Officer"],
  twitter: ["CTO", "VP Engineering", "Head of Product"],
  headcount: ["Head of Talent", "VP People", "CHRO"],
  github: ["VP Engineering", "Head of Platform", "Staff Engineer"],
  job_gap: ["VP Engineering", "Head of Data", "Director of Engineering"]
};
var SIGNAL_DECAY_RATE = 0.3;
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

// src/scorer.ts
async function scoreSignal(client, signal, userProfile2) {
  if (!client) {
    return {
      score: getDefaultScore(signal.signal_type),
      reasoning: "No Anthropic API key provided; using fallback score by signal type"
    };
  }
  const prompt = buildScoringPrompt(signal, userProfile2);
  try {
    const message = await client.messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 512,
      messages: [{ role: "user", content: prompt }]
    });
    const rawText = message.content.filter((block) => block.type === "text").map((block) => block.text).join("");
    const parsed = parseJsonResponse(rawText);
    return {
      score: clamp(parsed.score, 1, 10),
      reasoning: parsed.reasoning ?? "No reasoning provided"
    };
  } catch (err) {
    console.error("Error calling Claude for signal scoring:", err);
    return {
      score: getDefaultScore(signal.signal_type),
      reasoning: "Claude scoring failed; using fallback score"
    };
  }
}
function buildScoringPrompt(signal, userProfile2) {
  const skillsList = userProfile2.skills.join(", ");
  const pastWinsList = userProfile2.pastWins.map((w, i) => `${i + 1}. ${w}`).join("\n");
  return `You are evaluating whether a hiring signal is relevant for a job seeker to act on.

## Signal Details
- Company: ${signal.company}
- Signal Type: ${signal.signal_type}
- Signal Text: ${signal.signal_text}
- Date: ${signal.date}
- Source: ${signal.source_url}

## Job Seeker Profile
- Skills: ${skillsList}
- Background: ${userProfile2.background}
- Past Wins:
${pastWinsList}

## Your Task
Score this signal's relevance for this job seeker on a scale of 1-10, where:
- 1-3: Low relevance (wrong industry, skills don't match, signal is weak)
- 4-6: Moderate relevance (some skill overlap, signal is real but indirect)
- 7-9: High relevance (strong skill match, clear hiring need implied)
- 10: Perfect relevance (ideal match, explicit hiring signal, skills are directly needed)

Consider:
1. Does this signal type typically create roles matching this person's skills?
2. Is the company's growth trajectory likely to create demand for their background?
3. How specific and credible is the signal?

Respond with ONLY valid JSON in this exact format:
{"score": <number 1-10>, "reasoning": "<one to two sentence explanation>"}`;
}
function parseJsonResponse(text) {
  const jsonMatch = text.match(/\{[\s\S]*"score"[\s\S]*"reasoning"[\s\S]*\}/);
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[0]);
      if (typeof parsed.score === "number" && typeof parsed.reasoning === "string") {
        return { score: parsed.score, reasoning: parsed.reasoning };
      }
    } catch {
    }
  }
  const scoreMatch = text.match(/"score"\s*:\s*(\d+(?:\.\d+)?)/);
  const reasoningMatch = text.match(/"reasoning"\s*:\s*"([^"]+)"/);
  return {
    score: scoreMatch ? parseFloat(scoreMatch[1]) : 5,
    reasoning: reasoningMatch ? reasoningMatch[1] : text.slice(0, 200)
  };
}
function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
function getDefaultScore(signalType) {
  const defaults = {
    funding: 7,
    // High: funding almost always precedes hiring
    sec_filing: 6,
    // Moderate: disclosures are real but often vague
    twitter: 5,
    // Moderate: noisy; keyword scorer already filtered
    headcount: 7,
    // High: direct evidence of growth
    github: 6,
    // Moderate: engineering activity but no direct hiring signal
    job_gap: 8
    // High: gap vs peers is a strong predictor of imminent hire
  };
  return defaults[signalType] ?? 5;
}

// src/decay.ts
function applyDecay(score, signalDate) {
  const now = /* @__PURE__ */ new Date();
  const signal = new Date(signalDate);
  if (isNaN(signal.getTime())) {
    console.warn(`Invalid signal date: "${signalDate}", returning original score`);
    return score;
  }
  const msElapsed = now.getTime() - signal.getTime();
  const weeksElapsed = msElapsed / (7 * 24 * 60 * 60 * 1e3);
  if (weeksElapsed <= 0) return score;
  const decayFactor = Math.pow(1 - SIGNAL_DECAY_RATE, weeksElapsed);
  const decayedScore = score * decayFactor;
  return Math.max(1, parseFloat(decayedScore.toFixed(2)));
}

// src/main.ts
await Actor2.init();
var input = await Actor2.getInput() ?? {};
var {
  anthropicApiKey,
  userProfile,
  scoreThreshold = 4,
  hunterApiKey = "",
  apolloApiKey = "",
  lookbackDays = 14,
  runIngestionActors = true,
  secCompanies = [],
  githubOrgs = [],
  xHandles = [],
  keywords = [],
  targetCompanies = [],
  peerCompanies = [],
  linkedinCompanyUrls = [],
  linkedinCookies = "",
  crunchbaseApiKey = "",
  githubToken = "",
  actorNamespace = "golanger",
  fundingCategories,
  minFundingAmountUsd = 1e6,
  fundingRoundTypes = ["seed", "pre_seed", "series_a", "series_b", "series_c", "series_d", "series_e"]
} = input;
if (!userProfile?.skills || !userProfile?.background) {
  console.error("userProfile with skills and background is required");
  await Actor2.exit({ exit: false });
  process.exit(1);
}
console.log(`Starting orchestrator: scoreThreshold=${scoreThreshold}, lookbackDays=${lookbackDays}`);
var anthropic = anthropicApiKey ? new Anthropic({ apiKey: anthropicApiKey }) : null;
var allSignals = runIngestionActors ? await collectSignalsFromSourceActors({
  accountUsername: actorNamespace,
  lookbackDays,
  secCompanies,
  githubOrgs,
  xHandles,
  keywords,
  targetCompanies,
  peerCompanies,
  linkedinCompanyUrls,
  linkedinCookies,
  crunchbaseApiKey,
  githubToken,
  fundingCategories,
  minFundingAmountUsd,
  fundingRoundTypes
}) : [];
console.log(`Loaded ${allSignals.length} raw signals from source actors`);
var cutoff = /* @__PURE__ */ new Date();
cutoff.setDate(cutoff.getDate() - lookbackDays);
var recentSignals = allSignals.filter((s) => {
  try {
    return new Date(s.date) >= cutoff;
  } catch {
    return false;
  }
});
console.log(`${recentSignals.length} signals within the last ${lookbackDays} days`);
var signalMap = /* @__PURE__ */ new Map();
for (const signal of recentSignals) {
  if (!signalMap.has(signal.id)) {
    signalMap.set(signal.id, signal);
  }
}
var deduped = Array.from(signalMap.values());
console.log(`${deduped.length} unique signals after deduplication`);
var scored = [];
for (const signal of deduped) {
  console.log(`Scoring signal: ${signal.id} (${signal.signal_type} \u2014 ${signal.company})`);
  const { score, reasoning } = await scoreSignal(anthropic, signal, userProfile);
  const decayedScore = applyDecay(score, signal.date);
  console.log(`  Raw score: ${score}, Decayed: ${decayedScore}, Reasoning: ${reasoning.slice(0, 80)}...`);
  scored.push({ signal, finalScore: decayedScore, reasoning });
  await sleep(200);
}
var qualifying = scored.filter((s) => s.finalScore >= scoreThreshold);
console.log(`${qualifying.length} signals meet score threshold of ${scoreThreshold}`);
for (const { signal, finalScore, reasoning } of qualifying) {
  console.log(`
Processing qualifying signal: ${signal.company} (score: ${finalScore})`);
  const targetRoles = SIGNAL_ROLE_MAP[signal.signal_type] ?? ["VP Engineering", "CTO"];
  let contact = null;
  if (hunterApiKey || apolloApiKey) {
    try {
      const contactRun = await Actor2.call(resolveActorName(actorNamespace, "contact-finder-actor"), {
        signal,
        hunterApiKey,
        apolloApiKey,
        targetRoles
      });
      if (contactRun?.defaultDatasetId) {
        const contactDataset = await Actor2.openDataset(contactRun.defaultDatasetId);
        const { items: contactItems } = await contactDataset.getData();
        const result = contactItems[0];
        contact = result?.contact ?? null;
      }
    } catch (err) {
      console.error(`Error finding contact for ${signal.company}:`, err);
    }
  } else {
    contact = buildFallbackContact(signal, targetRoles);
  }
  if (!contact) {
    console.warn(`No contact found for ${signal.company}, skipping email draft`);
    continue;
  }
  console.log(`  Found contact: ${contact.name} (${contact.title})`);
  let draft = null;
  if (anthropicApiKey) {
    try {
      const emailRun = await Actor2.call(resolveActorName(actorNamespace, "email-drafter-actor"), {
        signal,
        contact,
        userProfile,
        anthropicApiKey
      });
      if (emailRun?.defaultDatasetId) {
        const emailDataset = await Actor2.openDataset(emailRun.defaultDatasetId);
        const { items: emailItems } = await emailDataset.getData();
        const result = emailItems[0];
        draft = result?.draft ?? null;
      }
    } catch (err) {
      console.error(`Error drafting email for ${signal.company}:`, err);
    }
  } else {
    draft = buildFallbackDraft(signal, contact);
  }
  if (!draft) {
    console.warn(`No email draft generated for ${signal.company}`);
    continue;
  }
  const outreachRecord = {
    ...draft,
    signal_id: signal.id,
    signal_company: signal.company,
    signal_type: signal.signal_type,
    signal_text: signal.signal_text,
    signal_date: signal.date,
    source_url: signal.source_url,
    careers_url: signal.careers_url,
    final_score: finalScore,
    scoring_reasoning: reasoning,
    contact,
    status: "pending_review",
    created_at: (/* @__PURE__ */ new Date()).toISOString()
  };
  await pushDataWithFallback([outreachRecord], DATASETS.OUTREACH);
  console.log(`  Pushed outreach draft for ${signal.company} \u2014 subject: "${draft.subject}"`);
}
console.log(`
Orchestrator complete. Processed ${qualifying.length} qualifying signals.`);
await Actor2.exit();
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
async function collectSignalsFromSourceActors(input2) {
  const allSignals2 = [];
  await collectActorOutput("funding-news-actor", {
    crunchbaseApiKey: input2.crunchbaseApiKey,
    lookbackDays: input2.lookbackDays,
    minRoundAmountUsd: input2.minFundingAmountUsd,
    roundTypes: input2.fundingRoundTypes,
    fundingCategories: input2.fundingCategories
  }, allSignals2, input2.accountUsername);
  await collectActorOutput("sec-edgar-actor", {
    companies: input2.secCompanies,
    lookbackDays: input2.lookbackDays
  }, allSignals2, input2.accountUsername);
  if (input2.githubOrgs.length > 0) {
    await collectActorOutput("github-signal-actor", {
      githubOrgs: input2.githubOrgs,
      githubToken: input2.githubToken,
      lookbackDays: input2.lookbackDays
    }, allSignals2, input2.accountUsername);
  }
  if (input2.xHandles.length > 0 || input2.keywords.length > 0) {
    await collectActorOutput("twitter-x-actor", {
      xHandles: input2.xHandles,
      keywords: input2.keywords,
      lookbackDays: input2.lookbackDays
    }, allSignals2, input2.accountUsername);
  }
  if (input2.linkedinCompanyUrls.length > 0) {
    await collectActorOutput("linkedin-headcount-actor", {
      companyUrls: input2.linkedinCompanyUrls
    }, allSignals2, input2.accountUsername);
  }
  if (input2.targetCompanies.length > 0 && input2.peerCompanies.length > 0) {
    await collectActorOutput("jobboard-gap-actor", {
      targetCompanies: input2.targetCompanies,
      peerCompanies: input2.peerCompanies,
      linkedinCookies: input2.linkedinCookies
    }, allSignals2, input2.accountUsername);
  }
  return allSignals2;
}
async function collectActorOutput(actorName, actorInput, sink, accountUsername) {
  try {
    console.log(`Running source actor: ${actorName}`);
    const run = await Actor2.call(resolveActorName(accountUsername, actorName), actorInput);
    if (!run?.defaultDatasetId) return;
    const dataset = await Actor2.openDataset(run.defaultDatasetId);
    const { items } = await dataset.getData({ clean: true });
    sink.push(...items);
    console.log(`Collected ${items.length} signals from ${actorName}`);
  } catch (err) {
    console.error(`Source actor failed: ${actorName}`, err);
  }
}
function resolveActorName(accountUsername, actorName) {
  return accountUsername ? `${accountUsername}/${actorName}` : actorName;
}
function buildFallbackContact(signal, targetRoles) {
  const role = targetRoles[0] ?? "Hiring Manager";
  const localPart = role.toLowerCase().includes("talent") ? "careers" : "hello";
  const domain = signal.company_domain || `${signal.company.toLowerCase().replace(/[^a-z0-9]/g, "")}.com`;
  return {
    signal_id: signal.id,
    name: `${signal.company} Hiring Team`,
    title: role,
    email: `${localPart}@${domain}`,
    linkedin_url: "",
    confidence: 0.1
  };
}
function buildFallbackDraft(signal, contact) {
  const firstName = contact.name.split(" ")[0];
  return {
    signal_id: signal.id,
    contact,
    subject: signal.signal_type === "funding" ? "Congrats on the round \u2014 quick question" : `${signal.company} caught my eye`,
    body: [
      `Hi ${firstName},`,
      "",
      signal.signal_text,
      "",
      "I noticed this and wanted to reach out because my background aligns well with the kind of work this usually creates.",
      "",
      "Would you be open to a 20-minute call next week to explore whether there might be a fit?",
      "",
      "Best,"
    ].join("\n"),
    status: "pending_review"
  };
}
