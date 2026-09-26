type Props = {
  filterKeys: readonly string[];
  loggedInCookie: string;
  jobLanguagesCookie: string;
};

// Runs while the cached document is parsed, before its results are painted.
// Configuration lives in HTML attributes, which React escapes, so no values
// from the server are interpolated into executable JavaScript.
export const EXPLORE_PREPAINT_SCRIPT = `(() => {
  try {
    const script = document.currentScript;
    const keys = JSON.parse(script.getAttribute("data-filter-keys"));
    const loggedInCookie = script.getAttribute("data-logged-in-cookie");
    const jobLanguagesCookie = script.getAttribute("data-job-languages-cookie");
    const params = new URLSearchParams(location.search);
    const cookies = document.cookie.split(";").map(value => value.trim());
    const hasCookie = name => name !== null && cookies.some(value => value.startsWith(name + "="));
    if (keys.some(key => params.has(key)) || hasCookie(loggedInCookie) || hasCookie(jobLanguagesCookie)) {
      document.documentElement.setAttribute("data-explore-pending", "");
    }
  } catch {
    document.documentElement.setAttribute("data-explore-pending", "");
  }
})();`;

export function ExplorePrepaintScript({ filterKeys, loggedInCookie, jobLanguagesCookie }: Props) {
  return (
    <script
      data-filter-keys={JSON.stringify(filterKeys)}
      data-logged-in-cookie={loggedInCookie}
      data-job-languages-cookie={jobLanguagesCookie}
      dangerouslySetInnerHTML={{ __html: EXPLORE_PREPAINT_SCRIPT }}
    />
  );
}
