import "server-only";
import { readFileSync } from "node:fs";
import path from "node:path";

// Read the canonical repository documents at module load, not per request.
// next.config.ts traces from the repository root for standalone deployments.
const documents = {
  terms: readFileSync(path.join(process.cwd(), "../../TERMS-OF-SERVICE"), "utf8"),
  privacy: readFileSync(path.join(process.cwd(), "../../PRIVACY-POLICY"), "utf8"),
};

function linkedText(text: string) {
  return text.split(/(https:\/\/\S+|business@colophon-group\.org)/g).map((part, index) => {
    if (part.startsWith("https://") || part === "business@colophon-group.org") {
      const label = part.replace(/[.,;:!?)]+$/, "");
      return <span key={index}><a href={label.startsWith("https://") ? label : `mailto:${label}`} className="break-words text-primary underline">{label}</a>{part.slice(label.length)}</span>;
    }
    return part;
  });
}

export function LegalDocument({ document }: { document: keyof typeof documents }) {
  return (
    <div lang="en" className="space-y-5 text-sm leading-relaxed">
      {documents[document].trim().split(/\n\s*\n/).map((block, index) => (
        /^\d+\. [A-Z]/.test(block)
          ? <h3 key={index} className="pt-4 text-base font-bold">{block}</h3>
          : <p key={index} className="whitespace-pre-line">{linkedText(block)}</p>
      ))}
    </div>
  );
}
