import { mkdtemp, readFile, readdir, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { restoreTestEnv, setTestEnv, snapshotTestEnv } from "@/test-utils/env";
import Ajv from "ajv";
import { afterEach, describe, expect, it } from "vitest";
import {
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
  normalizeClassifierInputV1,
} from "../classifier-input";
import {
  STAGE_A_EXAMPLE_V1_SCHEMA,
  STAGE_A_FREEZE_V1_SCHEMA,
  STAGE_A_MANIFEST_V1_SCHEMA,
  STAGE_A_READY_POLICY_V1_SCHEMA,
  STAGE_A_WIP_V1_SCHEMA,
} from "./schemas";
import {
  STAGE_A_EXAMPLE_SCHEMA_VERSION,
  STAGE_A_READY_POLICY_SCHEMA_VERSION,
  STAGE_A_WIP_SCHEMA_VERSION,
  StageAEvaluationError,
  buildStageAReadyManifest,
  canonicalStageAJson,
  digestStageAReadyPolicy,
  freezeStageA,
  loadStageABenchmark,
  readStageAReadyPolicyFile,
  reportStageAFreeze,
  validateStageAWip,
  writeStageAFreezeFile,
  type BinaryLabel,
  type StageAExampleV1,
  type StageAReadyPolicyV1,
  type StageAWipV1,
} from "./stage-a";

const temporaryRoots: string[] = [];
const originalEnv = snapshotTestEnv(["AI_FILTER_EVAL_DATA_ROOT"]);

afterEach(async () => {
  restoreTestEnv(originalEnv);
  await Promise.all(
    temporaryRoots.splice(0).map((temporaryRoot) =>
      rm(temporaryRoot, { recursive: true, force: true }),
    ),
  );
});

const locales = ["de", "en", "fr", "it"] as const;
const scenarios = [
  "clear_match",
  "clear_non_match",
  "ambiguous",
  "prompt_injection",
] as const;

function syntheticSource(index: number) {
  return {
    candidateId: `synthetic-candidate-${index}`,
    title: `Synthetic role ${index}`,
    companyName: "Synthetic Company",
    descriptionHtml: `<p>Synthetic description ${index}</p>`,
    selectedDescriptionLocale: locales[index % locales.length],
  };
}

function syntheticExample(index: number): StageAExampleV1 {
  const source = syntheticSource(index);
  const annotations = [
    {
      annotationId: `eval-annotation-${String(index).padStart(3, "0")}-a`,
      actorId: "eval-actor-a",
      label: (index % 2) as BinaryLabel,
    },
  ];
  if (index < 50) {
    annotations.push({
      annotationId: `eval-annotation-${String(index).padStart(3, "0")}-b`,
      actorId: "eval-actor-b",
      label: (index < 25 ? index % 2 : (index + 1) % 2) as BinaryLabel,
    });
  }
  return {
    schemaVersion: STAGE_A_EXAMPLE_SCHEMA_VERSION,
    exampleId: `eval-example-${String(index).padStart(3, "0")}`,
    softQuery: `synthetic query ${String(index).padStart(3, "0")}`,
    queryOrigin: "eval_authored",
    locale: locales[index % locales.length],
    scenario: scenarios[index % scenarios.length],
    classifierSource: source,
    contentIdentity: normalizeClassifierInputV1(source).contentIdentity,
    annotations,
    adjudication:
      index >= 25 && index < 50
        ? {
            adjudicationId: `eval-adjudication-${String(index).padStart(3, "0")}`,
            actorId: "eval-actor-c",
            label: (index % 2) as BinaryLabel,
          }
        : null,
  };
}

function syntheticWip(): StageAWipV1 {
  return {
    schemaVersion: STAGE_A_WIP_SCHEMA_VERSION,
    datasetId: "eval-synthetic-stage-a",
    classifierInputSchemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
    classifierInputNormalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
    examples: Array.from({ length: 200 }, (_, index) => syntheticExample(index)),
  };
}

function syntheticPolicy(overrides: Partial<StageAReadyPolicyV1> = {}): StageAReadyPolicyV1 {
  return {
    schemaVersion: STAGE_A_READY_POLICY_SCHEMA_VERSION,
    localeMinimums: { de: 40, en: 40, fr: 40, it: 40 },
    scenarioMinimums: {
      clear_match: 40,
      clear_non_match: 40,
      ambiguous: 40,
      prompt_injection: 40,
    },
    ...overrides,
  };
}

type Mutable<T> = T extends readonly (infer Item)[]
  ? Mutable<Item>[]
  : T extends object
    ? { -readonly [Key in keyof T]: Mutable<T[Key]> }
    : T;

function clone<T>(input: T): Mutable<T> {
  return JSON.parse(JSON.stringify(input)) as Mutable<T>;
}

async function useTemporaryRoot(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "jobseek-stage-a-"));
  temporaryRoots.push(root);
  setTestEnv({ AI_FILTER_EVAL_DATA_ROOT: root });
  return root;
}

async function writeReadyFixture(
  wip: StageAWipV1 = syntheticWip(),
  policy: StageAReadyPolicyV1 = syntheticPolicy(),
) {
  const root = await useTemporaryRoot();
  const policyDigest = digestStageAReadyPolicy(policy);
  const result = await writeStageAFreezeFile("ready.json", wip, policy, policyDigest);
  return { root, policyDigest, manifestDigest: result.manifestDigest };
}

describe("strict Stage A contracts", () => {
  it("publishes JSON schemas with closed object boundaries", () => {
    expect(STAGE_A_EXAMPLE_V1_SCHEMA.additionalProperties).toBe(false);
    expect(STAGE_A_EXAMPLE_V1_SCHEMA.properties.classifierSource.additionalProperties).toBe(
      false,
    );
    expect(STAGE_A_WIP_V1_SCHEMA.additionalProperties).toBe(false);
    expect(STAGE_A_READY_POLICY_V1_SCHEMA.additionalProperties).toBe(false);
    expect(STAGE_A_FREEZE_V1_SCHEMA.additionalProperties).toBe(false);
  });

  it("compiles every public schema and rejects known runtime-invalid fields", () => {
    const ajv = new Ajv({ allErrors: true, strict: true });
    for (const schema of [
      STAGE_A_EXAMPLE_V1_SCHEMA,
      STAGE_A_WIP_V1_SCHEMA,
      STAGE_A_READY_POLICY_V1_SCHEMA,
      STAGE_A_MANIFEST_V1_SCHEMA,
      STAGE_A_FREEZE_V1_SCHEMA,
    ]) {
      ajv.addSchema(schema);
    }
    const validateExample = ajv.getSchema(STAGE_A_EXAMPLE_V1_SCHEMA.$id);
    expect(validateExample).toBeTypeOf("function");
    expect(validateExample!(syntheticExample(0))).toBe(true);

    const invisible = clone(syntheticExample(0));
    invisible.softQuery = "synthetic\u200bquery";
    expect(validateExample!(invisible)).toBe(false);
    invisible.softQuery = "synthetic\u00adquery";
    expect(validateExample!(invisible)).toBe(false);
    const emptyTitle = clone(syntheticExample(0));
    emptyTitle.classifierSource.title = "";
    expect(validateExample!(emptyTitle)).toBe(false);
  });

  it("accepts synthetic WIP and recomputes every parent content identity", () => {
    const validated = validateStageAWip(syntheticWip());
    expect(validated.examples).toHaveLength(200);
    expect(validated.classifierInputNormalizerVersion).toBe("classifier-input-normalizer-v3");
    expect(Object.isFrozen(validated.examples)).toBe(true);
  });

  it("keeps the JSON schema and runtime ID boundary aligned at 68 characters", () => {
    const accepted = `eval-${"a".repeat(63)}`;
    const rejected = `eval-${"a".repeat(64)}`;
    const schemaPattern = new RegExp(STAGE_A_EXAMPLE_V1_SCHEMA.properties.exampleId.pattern);
    expect(schemaPattern.test(accepted)).toBe(true);
    expect(schemaPattern.test(rejected)).toBe(false);

    const input = clone(syntheticWip());
    input.examples[0].exampleId = accepted;
    expect(validateStageAWip(input).examples[0].exampleId).toBe(accepted);
    input.examples[0].exampleId = rejected;
    expect(() => validateStageAWip(input)).toThrow(/string_too_long/u);
  });

  it("rejects metadata canaries without exposing their key or value", () => {
    const input = clone(syntheticWip()) as StageAWipV1 & Record<string, unknown>;
    input["private-query-secret"] = "description-secret";
    let caught: unknown;
    try {
      validateStageAWip(input);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(StageAEvaluationError);
    const serialized = `${String(caught)} ${JSON.stringify(caught)}`;
    expect(serialized).not.toContain("private-query-secret");
    expect(serialized).not.toContain("description-secret");
    expect(caught).toEqual(new StageAEvaluationError("$", "additional_properties"));
    expect((caught as Error).stack).not.toContain("/private/");

    for (const field of ["notes", "filters", "provenance"]) {
      const nested = clone(syntheticWip()) as {
        examples: Array<Record<string, unknown>>;
      };
      nested.examples[0][field] = "never-map-this-private-value";
      let nestedError: unknown;
      try {
        validateStageAWip(nested);
      } catch (error) {
        nestedError = error;
      }
      expect(nestedError).toBeInstanceOf(StageAEvaluationError);
      expect(`${String(nestedError)} ${JSON.stringify(nestedError)}`).not.toContain(field);
      expect(`${String(nestedError)} ${JSON.stringify(nestedError)}`).not.toContain(
        "never-map-this-private-value",
      );
    }
  });

  it("fails closed on unreadable array wrappers without running values", () => {
    const input = clone(syntheticWip()) as Record<string, unknown>;
    const { proxy, revoke } = Proxy.revocable([], {});
    revoke();
    input.examples = proxy;
    expect(() => validateStageAWip(input)).toThrow(
      new StageAEvaluationError("$.examples", "array_unreadable"),
    );
  });

  it("fails closed on revoked object wrappers at root and nested paths", () => {
    const rootWrapper = Proxy.revocable({}, {});
    rootWrapper.revoke();
    expect(() => validateStageAWip(rootWrapper.proxy)).toThrow(
      new StageAEvaluationError("$", "object_unreadable"),
    );

    const nested = clone(syntheticWip()) as Record<string, unknown>;
    const exampleWrapper = Proxy.revocable({}, {});
    exampleWrapper.revoke();
    (nested.examples as unknown[])[0] = exampleWrapper.proxy;
    expect(() => validateStageAWip(nested)).toThrow(
      new StageAEvaluationError("$.examples[0]", "object_unreadable"),
    );
  });

  it("rejects a stale content-identity pin with a fixed error", () => {
    const input = clone(syntheticWip());
    input.examples[0].classifierSource.title = "Changed synthetic role";
    expect(() => validateStageAWip(input)).toThrow(
      new StageAEvaluationError(
        "$.examples[0].contentIdentity",
        "content_identity_mismatch",
      ),
    );
  });

  it("rejects duplicate annotators and non-independent adjudicators", () => {
    const duplicate = clone(syntheticWip());
    duplicate.examples[0].annotations[1].actorId = duplicate.examples[0].annotations[0].actorId;
    expect(() => validateStageAWip(duplicate)).toThrow(/independent_actors_required/u);

    const adjudicator = clone(syntheticWip());
    adjudicator.examples[25].adjudication!.actorId = "eval-actor-a";
    expect(() => validateStageAWip(adjudicator)).toThrow(
      /independent_adjudicator_required/u,
    );
  });

  it("keeps unresolved disagreement valid as WIP but blocks ready", () => {
    const input = clone(syntheticWip());
    input.examples[25].adjudication = null;
    expect(validateStageAWip(input).examples).toHaveLength(200);
    const policy = syntheticPolicy();
    expect(() =>
      buildStageAReadyManifest(input, policy, digestStageAReadyPolicy(policy)),
    ).toThrow(/unresolved_disagreement/u);
  });

  it("hard-codes the 200 and 50 readiness floors outside policy", () => {
    const policy = syntheticPolicy({
      localeMinimums: { de: 1, en: 1, fr: 1, it: 1 },
      scenarioMinimums: {
        clear_match: 1,
        clear_non_match: 1,
        ambiguous: 1,
        prompt_injection: 1,
      },
    });
    const policyDigest = digestStageAReadyPolicy(policy);
    const short = clone(syntheticWip());
    short.examples.pop();
    expect(() => buildStageAReadyManifest(short, policy, policyDigest)).toThrow(
      /exactly_200_examples_required/u,
    );

    const singleLabelled = clone(syntheticWip());
    for (let index = 0; index < 151; index += 1) {
      singleLabelled.examples[index].annotations.splice(1);
      singleLabelled.examples[index].adjudication = null;
    }
    expect(() => buildStageAReadyManifest(singleLabelled, policy, policyDigest)).toThrow(
      /at_least_50_double_labels_required/u,
    );
  });

  it("rejects duplicate semantic pairs even when IDs differ", () => {
    const input = clone(syntheticWip());
    input.examples[1].softQuery = input.examples[0].softQuery;
    input.examples[1].classifierSource = input.examples[0].classifierSource;
    input.examples[1].contentIdentity = input.examples[0].contentIdentity;
    const policy = syntheticPolicy();
    expect(() =>
      buildStageAReadyManifest(input, policy, digestStageAReadyPolicy(policy)),
    ).toThrow(/unique_semantic_pairs_required/u);
  });

  it("rejects whitespace variants before semantic-pair uniqueness", () => {
    for (const softQuery of [
      " synthetic query",
      "synthetic query ",
      "synthetic  query",
      "synthetic\u200bquery",
      "synthetic\u00adquery",
      "synthetic\u034fquery",
      "synthetic\u0007query",
    ]) {
      const input = clone(syntheticWip());
      input.examples[0].softQuery = softQuery;
      expect(() => validateStageAWip(input)).toThrow(/canonical_query_required/u);
    }
  });

  it("requires an external policy digest and enforces its approved coverage", () => {
    const policy = syntheticPolicy();
    expect(() => buildStageAReadyManifest(syntheticWip(), policy, "0".repeat(64))).toThrow(
      /policy_digest_mismatch/u,
    );

    const unmetCoveragePolicy = syntheticPolicy({
      localeMinimums: { de: 51, en: 40, fr: 40, it: 40 },
    });
    expect(() =>
      buildStageAReadyManifest(
        syntheticWip(),
        unmetCoveragePolicy,
        digestStageAReadyPolicy(unmetCoveragePolicy),
      ),
    ).toThrow(/coverage_not_met/u);

    const impossibleScenarioPolicy = syntheticPolicy({
      scenarioMinimums: {
        clear_match: 51,
        clear_non_match: 51,
        ambiguous: 51,
        prompt_injection: 51,
      },
    });
    expect(() => digestStageAReadyPolicy(impossibleScenarioPolicy)).toThrow(
      /coverage_minimums_impossible/u,
    );
  });

  it("derives gold rather than accepting a caller-provided field", () => {
    const input = clone(syntheticWip()) as StageAWipV1 & {
      examples: Array<StageAExampleV1 & Record<string, unknown>>;
    };
    input.examples[0].goldLabel = 1;
    expect(() => validateStageAWip(input)).toThrow(/additional_properties/u);

    const policy = syntheticPolicy();
    const ready = buildStageAReadyManifest(
      syntheticWip(),
      policy,
      digestStageAReadyPolicy(policy),
    );
    expect(ready.examples[0].goldLabel).toBe(0);
    expect(ready.examples[25].goldLabel).toBe(1);
  });
});

describe("deterministic freeze", () => {
  it("is invariant to set-like example and annotation order", () => {
    const policy = syntheticPolicy();
    const input = syntheticWip();
    const reordered = clone(input);
    reordered.examples.reverse();
    for (const example of reordered.examples) example.annotations.reverse();
    const expectedPolicyDigest = digestStageAReadyPolicy(policy);
    expect(freezeStageA(reordered, policy, expectedPolicyDigest)).toEqual(
      freezeStageA(input, policy, expectedPolicyDigest),
    );
  });

  it("uses raw code-unit key order rather than locale collation", () => {
    expect(canonicalStageAJson({ ubung: 1, Übung: 2 })).toBe(
      '{"ubung":1,"Übung":2}\n',
    );
  });

  it("changes the manifest digest when a scoring-relevant leaf changes", () => {
    const policy = syntheticPolicy();
    const expectedPolicyDigest = digestStageAReadyPolicy(policy);
    const first = freezeStageA(syntheticWip(), policy, expectedPolicyDigest);
    const changed = clone(syntheticWip());
    changed.examples[100].annotations[0].actorId = "eval-actor-d";
    const second = freezeStageA(changed, policy, expectedPolicyDigest);
    expect(second.manifestDigest).not.toBe(first.manifestDigest);
  });
});

describe("private filesystem and loader boundary", () => {
  it("publishes one complete 0600 file across concurrent no-replace freezes", async () => {
    const root = await useTemporaryRoot();
    const input = syntheticWip();
    const policy = syntheticPolicy();
    const policyDigest = digestStageAReadyPolicy(policy);
    const attempts = await Promise.allSettled(
      Array.from({ length: 20 }, () =>
        writeStageAFreezeFile("concurrent.json", input, policy, policyDigest),
      ),
    );
    expect(attempts.filter(({ status }) => status === "fulfilled")).toHaveLength(1);
    const bytes = await readFile(path.join(root, "concurrent.json"), "utf8");
    expect(bytes.endsWith("\n")).toBe(true);
    expect(JSON.parse(bytes)).toHaveProperty("manifestDigest");
    expect((await stat(path.join(root, "concurrent.json"))).mode & 0o777).toBe(0o600);
  });

  it("never overwrites an existing destination", async () => {
    const root = await useTemporaryRoot();
    const destination = path.join(root, "ready.json");
    await writeFile(destination, "do-not-overwrite", { mode: 0o600 });
    const before = await stat(destination);
    const policy = syntheticPolicy();
    await expect(
      writeStageAFreezeFile(
        "ready.json",
        syntheticWip(),
        policy,
        digestStageAReadyPolicy(policy),
      ),
    ).rejects.toEqual(new StageAEvaluationError("$file", "exclusive_publish_failed"));
    expect(await readFile(destination, "utf8")).toBe("do-not-overwrite");
    expect((await stat(destination)).mtimeMs).toBe(before.mtimeMs);
    expect((await readdir(root)).filter((entry) => entry.endsWith(".tmp"))).toEqual([]);

    const outside = path.join(root, "outside.json");
    await writeFile(outside, "outside-bytes", { mode: 0o600 });
    await symlink(outside, path.join(root, "linked-ready.json"));
    await expect(
      writeStageAFreezeFile(
        "linked-ready.json",
        syntheticWip(),
        policy,
        digestStageAReadyPolicy(policy),
      ),
    ).rejects.toThrow(/exclusive_publish_failed/u);
    expect(await readFile(outside, "utf8")).toBe("outside-bytes");
  });

  it("requires an absolute narrow root and never echoes a private filename", async () => {
    const symbolicName = Symbol("private-filename-canary") as unknown as string;
    let symbolicError: unknown;
    try {
      await readStageAReadyPolicyFile(symbolicName);
    } catch (error) {
      symbolicError = error;
    }
    expect(symbolicError).toEqual(
      new StageAEvaluationError("$file", "safe_file_name_required"),
    );
    expect(String(symbolicError)).not.toContain("private-filename-canary");

    setTestEnv({ AI_FILTER_EVAL_DATA_ROOT: undefined });
    await expect(readStageAReadyPolicyFile("private-name.json")).rejects.toEqual(
      new StageAEvaluationError("$root", "absolute_root_required"),
    );
    setTestEnv({ AI_FILTER_EVAL_DATA_ROOT: "relative-private-root" });
    await expect(readStageAReadyPolicyFile("private-name.json")).rejects.toEqual(
      new StageAEvaluationError("$root", "absolute_root_required"),
    );
    setTestEnv({ AI_FILTER_EVAL_DATA_ROOT: process.cwd() });
    let caught: unknown;
    try {
      await readStageAReadyPolicyFile("private-name.json");
    } catch (error) {
      caught = error;
    }
    expect(caught).toEqual(new StageAEvaluationError("$root", "private_root_required"));
    expect(String(caught)).not.toContain("private-name.json");

    const unsafeRepositoryRoot = await mkdtemp(path.join(process.cwd(), ".stage-a-unsafe-"));
    temporaryRoots.push(unsafeRepositoryRoot);
    setTestEnv({ AI_FILTER_EVAL_DATA_ROOT: unsafeRepositoryRoot });
    await expect(readStageAReadyPolicyFile("private-name.json")).rejects.toEqual(
      new StageAEvaluationError("$root", "repository_staging_path_required"),
    );
  });

  it("rejects traversal, absolute or nested children, and a symlink root", async () => {
    const root = await useTemporaryRoot();
    const policy = syntheticPolicy();
    const policyDigest = digestStageAReadyPolicy(policy);
    await expect(
      writeStageAFreezeFile("../escape.json", syntheticWip(), policy, policyDigest),
    ).rejects.toThrow(/safe_file_name_required/u);
    await expect(
      writeStageAFreezeFile(path.join(root, "escape.json"), syntheticWip(), policy, policyDigest),
    ).rejects.toThrow(/safe_file_name_required/u);

    const outside = await mkdtemp(path.join(tmpdir(), "jobseek-stage-a-outside-"));
    temporaryRoots.push(outside);
    await symlink(outside, path.join(root, "linked"));
    await expect(
      writeStageAFreezeFile("linked/escape.json", syntheticWip(), policy, policyDigest),
    ).rejects.toThrow(/safe_file_name_required/u);

    const rootLink = `${root}-link`;
    temporaryRoots.push(rootLink);
    await symlink(root, rootLink);
    setTestEnv({ AI_FILTER_EVAL_DATA_ROOT: rootLink });
    await expect(readStageAReadyPolicyFile("policy.json")).rejects.toThrow(
      /safe_directory_required/u,
    );
  });

  it("rejects duplicate JSON keys without exposing file content", async () => {
    const root = await useTemporaryRoot();
    const secret = "private-secret-value";
    await writeFile(
      path.join(root, "policy.json"),
      `{"schemaVersion":"${STAGE_A_READY_POLICY_SCHEMA_VERSION}","schemaVersion":"${secret}"}`,
      { mode: 0o600 },
    );
    let caught: unknown;
    try {
      await readStageAReadyPolicyFile("policy.json");
    } catch (error) {
      caught = error;
    }
    expect(caught).toEqual(new StageAEvaluationError("$file", "duplicate_json_key"));
    expect(`${String(caught)} ${JSON.stringify(caught)}`).not.toContain(secret);
  });

  it("rejects BOM and malformed UTF-8 input", async () => {
    const root = await useTemporaryRoot();
    await writeFile(path.join(root, "bom.json"), Buffer.from([0xef, 0xbb, 0xbf, 0x7b, 0x7d]));
    await writeFile(path.join(root, "invalid.json"), Buffer.from([0xc3, 0x28]));
    await expect(readStageAReadyPolicyFile("bom.json")).rejects.toThrow(
      /canonical_utf8_required/u,
    );
    await expect(readStageAReadyPolicyFile("invalid.json")).rejects.toThrow(
      /canonical_utf8_required/u,
    );
  });

  it("requires both external digests and returns an exact deeply frozen DTO", async () => {
    const input = clone(syntheticWip());
    input.examples[25].annotations[0].actorId = "eval-canary-actor-a";
    input.examples[25].annotations[0].annotationId = "eval-canary-annotation-a";
    input.examples[25].annotations[1].actorId = "eval-canary-actor-b";
    input.examples[25].annotations[1].annotationId = "eval-canary-annotation-b";
    input.examples[25].adjudication!.actorId = "eval-canary-adjudicator";
    input.examples[25].adjudication!.adjudicationId = "eval-canary-adjudication";
    input.examples[25].scenario = "prompt_injection";
    const { policyDigest, manifestDigest } = await writeReadyFixture(input);
    await expect(
      loadStageABenchmark("ready.json", "0".repeat(64), policyDigest),
    ).rejects.toThrow(/manifest_digest_mismatch/u);
    await expect(
      loadStageABenchmark("ready.json", manifestDigest, "0".repeat(64)),
    ).rejects.toThrow(/policy_digest_mismatch/u);

    const benchmark = await loadStageABenchmark(
      "ready.json",
      manifestDigest,
      policyDigest,
    );
    expect(Object.keys(benchmark[0]).sort()).toEqual([
      "classifierInput",
      "goldLabel",
      "softQuery",
    ]);
    expect(Object.keys(benchmark[0].classifierInput).sort()).toEqual([
      "candidateId",
      "companyName",
      "descriptionText",
      "schemaVersion",
      "title",
    ]);
    const serialized = JSON.stringify(benchmark);
    for (const forbidden of [
      "queryOrigin",
      "locale",
      "scenario",
      "actorId",
      "adjudication",
      "contentIdentity",
      "selectedDescriptionLocale",
      "descriptionHtml",
      "truncated",
      "eval-canary-actor-a",
      "eval-canary-actor-b",
      "eval-canary-adjudicator",
      "eval-canary-annotation-a",
      "eval-canary-annotation-b",
      "eval-canary-adjudication",
      "prompt_injection",
    ]) {
      expect(serialized).not.toContain(forbidden);
    }
    expect(Object.isFrozen(benchmark)).toBe(true);
    expect(Object.isFrozen(benchmark[0])).toBe(true);
    expect(Object.isFrozen(benchmark[0].classifierInput)).toBe(true);
  });

  it("never lets a WIP artifact reach the benchmark loader", async () => {
    const root = await useTemporaryRoot();
    await writeFile(path.join(root, "wip.json"), canonicalStageAJson(syntheticWip()), {
      mode: 0o600,
    });
    await expect(
      loadStageABenchmark("wip.json", "0".repeat(64), "0".repeat(64)),
    ).rejects.toThrow(/additional_properties/u);
  });

  it("rejects prototype-named properties inside the frozen classifier payload", async () => {
    const root = await useTemporaryRoot();
    const policy = syntheticPolicy();
    const policyDigest = digestStageAReadyPolicy(policy);
    const frozen = clone(freezeStageA(syntheticWip(), policy, policyDigest));
    Object.defineProperty(frozen.manifest.examples[0].classifierInput, "__proto__", {
      value: { privateNotes: "must-not-leak" },
      enumerable: true,
      configurable: true,
      writable: true,
    });
    await writeFile(path.join(root, "malformed.json"), canonicalStageAJson(frozen), {
      mode: 0o600,
    });
    let caught: unknown;
    try {
      await loadStageABenchmark(
        "malformed.json",
        frozen.manifestDigest,
        policyDigest,
      );
    } catch (error) {
      caught = error;
    }
    expect(caught).toEqual(
      new StageAEvaluationError(
        "$.manifest.examples[0].classifierInput",
        "additional_properties",
      ),
    );
    expect(`${String(caught)} ${JSON.stringify(caught)}`).not.toContain("must-not-leak");
  });
});

describe("privacy-safe reports", () => {
  it("reports only fixed, sufficiently large dimensions", async () => {
    const { manifestDigest, policyDigest } = await writeReadyFixture();
    const report = await reportStageAFreeze("ready.json", manifestDigest, policyDigest);
    expect(report.dimensions.locale.suppressed).toBe(false);
    expect(report.dimensions.scenario.suppressed).toBe(false);
    expect(report.agreement).toEqual({
      suppressed: false,
      doubleLabelled: 50,
      agreements: 25,
      disagreements: 25,
    });
    expect(JSON.stringify(report)).not.toContain("eval-");
  });

  it("suppresses a whole dimension and agreement breakdown to prevent complements", async () => {
    const input = clone(syntheticWip());
    for (let index = 1; index < input.examples.length; index += 1) {
      input.examples[index].locale = "de";
    }
    input.examples[1].locale = "en";
    input.examples[2].locale = "fr";
    input.examples[3].locale = "it";
    for (let index = 25; index < 49; index += 1) {
      input.examples[index].annotations[1].label = input.examples[index].annotations[0].label;
      input.examples[index].adjudication = null;
    }
    const policy = syntheticPolicy({ localeMinimums: { de: 1, en: 1, fr: 1, it: 1 } });
    const { manifestDigest, policyDigest } = await writeReadyFixture(input, policy);
    const report = await reportStageAFreeze("ready.json", manifestDigest, policyDigest);
    expect(report.dimensions.locale).toEqual({ suppressed: true });
    expect(report.agreement).toEqual({ suppressed: true });
    expect(JSON.stringify(report.agreement)).not.toContain("49");
    expect(JSON.stringify(report.agreement)).not.toContain("1");
  });

  it("suppresses the full label breakdown when either label is sparse", async () => {
    const input = clone(syntheticWip());
    for (const example of input.examples) {
      for (const annotation of example.annotations) annotation.label = 0;
      example.adjudication = null;
    }
    input.examples[199].annotations[0].label = 1;
    const { manifestDigest, policyDigest } = await writeReadyFixture(input);
    const report = await reportStageAFreeze("ready.json", manifestDigest, policyDigest);
    expect(report.labelCounts).toEqual({ suppressed: true });
  });
});
