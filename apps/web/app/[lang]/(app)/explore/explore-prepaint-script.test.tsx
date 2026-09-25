import { runInNewContext } from "node:vm";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { EXPLORE_PREPAINT_SCRIPT, ExplorePrepaintScript } from "./explore-prepaint-script";

const attributes: Record<string, string> = {
  "data-filter-keys": JSON.stringify(["q", "lang"]),
  "data-logged-in-cookie": "logged_in",
  "data-job-languages-cookie": "JSEEK_JOB_LANGUAGES",
};

function runPrepaint(search: string, cookie: string, data = attributes) {
  const setAttribute = vi.fn();
  runInNewContext(EXPLORE_PREPAINT_SCRIPT, {
    document: {
      currentScript: { getAttribute: (name: string) => data[name] ?? null },
      cookie,
      documentElement: { setAttribute },
    },
    location: { search },
    URLSearchParams,
  });
  return setAttribute;
}

describe("Explore prepaint script", () => {
  it("keeps configuration out of executable JavaScript and HTML-escapes it", () => {
    const malicious = '</script><script>globalThis.injected = true</script>';
    const markup = renderToStaticMarkup(
      <ExplorePrepaintScript
        filterKeys={[malicious]}
        loggedInCookie={malicious}
        jobLanguagesCookie={malicious}
      />,
    );

    expect(markup.match(/<script\b/g)).toHaveLength(1);
    expect(markup.match(/<\/script>/g)).toHaveLength(1);
    expect(markup).toContain("&lt;/script&gt;");
    expect(markup.slice(markup.indexOf(">") + 1, markup.lastIndexOf("</script>")))
      .toBe(EXPLORE_PREPAINT_SCRIPT);
  });

  it.each([
    ["?q=engineer", ""],
    ["?lang=de", ""],
    ["", "logged_in=1"],
    ["", "JSEEK_JOB_LANGUAGES=%5B%22de%22%5D"],
  ])("marks personalized results pending for %s and %s", (search, cookie) => {
    expect(runPrepaint(search, cookie)).toHaveBeenCalledWith("data-explore-pending", "");
  });

  it("leaves the anonymous unfiltered page visible", () => {
    expect(runPrepaint("?show=grid", "not_logged_in=1")).not.toHaveBeenCalled();
  });

  it("hides cached results if the script configuration is missing", () => {
    expect(runPrepaint("", "", {})).toHaveBeenCalledWith("data-explore-pending", "");
  });
});
