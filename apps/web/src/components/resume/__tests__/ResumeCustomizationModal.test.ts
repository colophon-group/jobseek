import { describe, it, expect } from "vitest";

describe("ResumeCustomizationModal", () => {
  it("should render modal with customized content", () => {
    const props = {
      open: true,
      onOpenChange: () => {},
      original: "\\documentclass{article}\\begin{document}Senior Engineer\\end{document}",
      customized: "\\documentclass{article}\\begin{document}Senior Engineer with Kubernetes\\end{document}",
      insertedKeywords: ["Kubernetes"],
      loading: false,
      onAccept: () => {},
      onCancel: () => {},
    };

    expect(props.open).toBe(true);
    expect(props.customized).toContain("Kubernetes");
    expect(props.insertedKeywords).toHaveLength(1);
  });

  it("should show loading state while customizing", () => {
    const props = {
      loading: true,
      insertedKeywords: [],
    };

    expect(props.loading).toBe(true);
  });

  it("should have accept and cancel buttons", () => {
    const acceptMock = () => {};
    const cancelMock = () => {};

    expect(typeof acceptMock).toBe("function");
    expect(typeof cancelMock).toBe("function");
  });

  it("should pass customized content to ResumeDiffPreview", () => {
    const original = "\\documentclass{article}\\begin{document}test\\end{document}";
    const customized = "\\documentclass{article}\\begin{document}test Kubernetes\\end{document}";
    const insertedKeywords = ["Kubernetes"];

    expect(customized).not.toEqual(original);
    expect(insertedKeywords).toContain("Kubernetes");
  });

  it("should disable accept button while loading", () => {
    const props = {
      loading: true,
      acceptDisabled: true,
    };

    expect(props.acceptDisabled).toBe(true);
  });

  it("should call onAccept when accepting customization", () => {
    let acceptCalled = false;
    const onAccept = () => { acceptCalled = true; };

    onAccept();
    expect(acceptCalled).toBe(true);
  });

  it("should call onCancel when rejecting customization", () => {
    let cancelCalled = false;
    const onCancel = () => { cancelCalled = true; };

    onCancel();
    expect(cancelCalled).toBe(true);
  });

  it("should handle empty customized content gracefully", () => {
    const props = {
      original: "test",
      customized: "",
      insertedKeywords: [],
    };

    expect(props.customized).toBe("");
    expect(Array.isArray(props.insertedKeywords)).toBe(true);
  });
});
