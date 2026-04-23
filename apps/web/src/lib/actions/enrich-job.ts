"use server";

import { cached } from "@/lib/cache";

function stripHtml(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 3000);
}

export async function getJobAiSummary(params: {
  postingId: string;
  title: string | null;
  descriptionHtml: string | null;
  companyName: string;
}): Promise<string | null> {
  if (!params.title || !params.descriptionHtml) return null;
  const apiKey = process.env.MINIMAX_API_KEY;
  if (!apiKey) return null;

  const key = `ai-summary:${params.postingId}`;
  return cached(
    key,
    async () => {
      const text = stripHtml(params.descriptionHtml!);
      try {
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
                content:
                  "You are a job search assistant. Write a concise 2-3 sentence summary of the job posting. Cover: what the role does, key required skills, and one thing that makes it stand out. Be factual and direct. No preamble.",
              },
              {
                role: "user",
                content: `Title: ${params.title} at ${params.companyName}\n\n${text}`,
              },
            ],
            max_tokens: 150,
            temperature: 0.3,
          }),
        });
        if (!resp.ok) return null;
        const data = (await resp.json()) as {
          choices?: { message?: { content?: string } }[];
        };
        return data.choices?.[0]?.message?.content?.trim() ?? null;
      } catch {
        return null;
      }
    },
    { ttl: 3600 },
  );
}
