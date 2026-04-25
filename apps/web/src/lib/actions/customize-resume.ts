"use server";

import { getResume } from "@/lib/actions/resume";
import { getSessionUserId } from "@/lib/sessionCache";

type CustomizationResult = {
  customized: boolean;
  original: string;
  customized_content?: string;
  preview?: string;
  error?: string;
};

async function callLlmForCustomization(
  resumeContent: string,
  missingKeywords: string[],
  jobTitle: string,
  model: "sonnet" | "gpt-4o",
): Promise<string | null> {
  const isAnthropic = model === "sonnet";
  const endpoint = isAnthropic ? "https://api.anthropic.com/v1/messages" : "https://api.openai.com/v1/chat/completions";
  const headers: Record<string, string> = isAnthropic
    ? {
        "x-api-key": process.env.ANTHROPIC_API_KEY || "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      }
    : {
        Authorization: `Bearer ${process.env.OPENAI_API_KEY || ""}`,
        "Content-Type": "application/json",
      };

  const systemPrompt = `You are a LaTeX resume expert. Your task is to intelligently integrate missing keywords into a resume while:
1. PRESERVING all LaTeX formatting and structure
2. MAINTAINING one-page limit (do not add content, only replace)
3. PRIORITIZING the first \section{Experience} or equivalent
4. Using SEMANTIC replacements (e.g., Java→Kotlin for JVM languages, Python→Go for systems)
5. VALIDATING tech stack compatibility (Python ✗ Spring Boot is INVALID)
6. KEEPING authentic and natural (no buzzwords, no obvious insertions)

Return ONLY the modified LaTeX content. Do not explain or comment.`;

  const userPrompt = `Resume:\n${resumeContent}\n\nJob Title: ${jobTitle}\nMissing Keywords: ${missingKeywords.join(", ")}\n\nIntegrate these keywords strategically into the resume.`;

  try {
    if (isAnthropic) {
      const resp = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({
          model: "claude-sonnet-4-20250514",
          max_tokens: 4000,
          system: systemPrompt,
          messages: [{ role: "user", content: userPrompt }],
        }),
      });

      if (!resp.ok) return null;
      const data = (await resp.json()) as {
        content?: { type: string; text?: string }[];
      };
      return data.content?.[0]?.text ?? null;
    } else {
      const resp = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({
          model: "gpt-4o",
          max_tokens: 4000,
          system: systemPrompt,
          messages: [{ role: "user", content: userPrompt }],
        }),
      });

      if (!resp.ok) return null;
      const data = (await resp.json()) as {
        choices?: { message?: { content?: string } }[];
      };
      return data.choices?.[0]?.message?.content ?? null;
    }
  } catch {
    return null;
  }
}

export async function customizeResume(params: {
  jobTitle: string;
  missingKeywords: string[];
  originalContent?: string;
}): Promise<CustomizationResult> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const resume = await getResume();
  if (!resume) throw new Error("Resume not found");

  // Try Sonnet first
  let customized = await callLlmForCustomization(
    params.originalContent || resume.filename,
    params.missingKeywords,
    params.jobTitle,
    "sonnet",
  );

  // Fallback to GPT-4o
  if (!customized) {
    customized = await callLlmForCustomization(
      params.originalContent || resume.filename,
      params.missingKeywords,
      params.jobTitle,
      "gpt-4o",
    );
  }

  if (!customized) {
    return {
      customized: false,
      original: params.originalContent || resume.filename,
      error: "Failed to customize resume with both models",
    };
  }

  return {
    customized: true,
    original: params.originalContent || resume.filename,
    customized_content: customized,
    preview: `Customized resume with keywords: ${params.missingKeywords.slice(0, 3).join(", ")}${params.missingKeywords.length > 3 ? ` +${params.missingKeywords.length - 3} more` : ""}`,
  };
}
