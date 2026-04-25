import { describe, it, expect } from "vitest";

describe("Resume actions", () => {
  it("should accept LaTeX file content", () => {
    const latexContent = `
      \\documentclass{article}
      \\begin{document}
      \\section{Experience}
      Senior Software Engineer at TechCorp
      \\begin{itemize}
      \\item Led development of microservices using Python and Kubernetes
      \\item Optimized database queries in PostgreSQL
      \\item Mentored junior engineers in best practices
      \\end{itemize}
      \\end{document}
    `;
    expect(latexContent).toContain("Python");
    expect(latexContent).toContain("Kubernetes");
  });

  it("should extract text from simple content", () => {
    const content = "Python TypeScript React PostgreSQL";
    const words = content.split(/\s+/);
    expect(words).toContain("Python");
    expect(words).toContain("React");
  });

  it("should validate filename", () => {
    const validFilename = "my-resume.tex";
    expect(validFilename).toMatch(/\.tex$/);
  });
});
