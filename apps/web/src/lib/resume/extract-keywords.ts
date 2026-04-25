import { STOP_WORDS } from "./stop-words";

export function filterStopWords(tokens: string[]): string[] {
  return tokens.filter(
    (token) => !STOP_WORDS.has(token.toLowerCase())
  );
}

export async function extractKeywords(text: string): Promise<string[]> {
  if (!text.trim()) return [];

  const apiKey = process.env.MINIMAX_API_KEY;
  if (!apiKey) return [];

  try {
    // Tokenize by splitting on non-alphanumeric characters, but preserve hyphens and underscores
    const tokens = text
      .split(/[\s\-_.,;:()[\]{}!?"']+/)
      .filter((t) => t.length > 0);

    // Filter stop words
    const filtered = filterStopWords(tokens);

    if (filtered.length === 0) return [];

    // Call MiniMax to normalize abbreviations and deduplicate
    const resp = await fetch("https://api.minimax.chat/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "abab6.5s-chat",
        messages: [
          {
            role: "system",
            content: `You are a keyword extraction specialist. Given a list of technical keywords and skills, normalize abbreviations and remove duplicates.

Rules:
1. Expand abbreviations to full forms (e.g., "JS" → "JavaScript")
2. Normalize variations (e.g., "python" → "Python")
3. Remove exact duplicates
4. Keep only valid technical terms (tools, languages, frameworks, methodologies)
5. Return as a JSON array of strings, sorted alphabetically

Example input: ["js", "JavaScript", "react", "React", "ts"]
Example output: ["JavaScript", "React", "TypeScript"]`,
          },
          {
            role: "user",
            content: `Normalize these keywords: ${JSON.stringify(filtered)}`,
          },
        ],
        max_tokens: 500,
        temperature: 0.1,
      }),
    });

    if (!resp.ok) return filtered;

    const data = (await resp.json()) as {
      choices?: { message?: { content?: string } }[];
    };

    const content = data.choices?.[0]?.message?.content;
    if (!content) return filtered;

    // Parse JSON array from response
    try {
      const jsonMatch = content.match(/\[[\s\S]*\]/);
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
          return parsed;
        }
      }
    } catch {
      // Fall back to filtered tokens if JSON parsing fails
    }

    return filtered;
  } catch {
    // Return filtered tokens if API call fails
    return filterStopWords(
      text
        .split(/[\s\-_.,;:()[\]{}!?"']+/)
        .filter((t) => t.length > 0)
    );
  }
}
