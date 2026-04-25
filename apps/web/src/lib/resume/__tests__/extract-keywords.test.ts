import { describe, it, expect } from "vitest";
import { filterStopWords } from "@/lib/resume/extract-keywords";

describe("filterStopWords", () => {
  it("removes common prepositions", () => {
    const tokens = ["with", "Go", "and", "PostgreSQL"];
    expect(filterStopWords(tokens)).toEqual(["Go", "PostgreSQL"]);
  });

  it("removes generic filler verbs", () => {
    const tokens = ["managed", "Redis", "worked", "TypeScript"];
    expect(filterStopWords(tokens)).toEqual(["Redis", "TypeScript"]);
  });

  it("removes articles and pronouns", () => {
    const tokens = ["the", "a", "React", "I", "we", "Kubernetes"];
    expect(filterStopWords(tokens)).toEqual(["React", "Kubernetes"]);
  });

  it("keeps tech tool names and domain nouns", () => {
    const tokens = ["Go", "PostgreSQL", "microservices", "system", "design"];
    expect(filterStopWords(tokens)).toEqual(["Go", "PostgreSQL", "microservices", "system", "design"]);
  });

  it("case-insensitive removal", () => {
    const tokens = ["With", "Python", "AND", "Django"];
    expect(filterStopWords(tokens)).toEqual(["Python", "Django"]);
  });

  it("empty input returns empty array", () => {
    expect(filterStopWords([])).toEqual([]);
  });
});
