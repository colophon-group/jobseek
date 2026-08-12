import { parse, type DefaultTreeAdapterTypes } from "parse5";

export interface VisibleExploreHtmlInspection {
  staticTextContent: string;
  staticResultsCount: number;
  companyResultCount: number;
  repositoryFallbackCount: number;
  postingResultCount: number;
}

type HtmlElement = DefaultTreeAdapterTypes.Element;
type HtmlNode = DefaultTreeAdapterTypes.Node;
type HtmlTextNode = DefaultTreeAdapterTypes.TextNode;

function isElement(node: HtmlNode): node is HtmlElement {
  return "tagName" in node;
}

function isTextNode(node: HtmlNode): node is HtmlTextNode {
  return node.nodeName === "#text" && "value" in node;
}

function findBody(node: HtmlNode): HtmlElement | undefined {
  if (isElement(node) && node.tagName === "body") {
    return node;
  }

  if ("childNodes" in node) {
    for (const child of node.childNodes) {
      const body = findBody(child);
      if (body) {
        return body;
      }
    }
  }

  return undefined;
}

function visitVisibleNode(
  node: HtmlNode,
  inspection: VisibleExploreHtmlInspection,
  insideStaticResults: boolean,
): void {
  if (isTextNode(node)) {
    if (insideStaticResults) {
      inspection.staticTextContent += node.value;
    }
    return;
  }

  if (!isElement(node)) {
    if ("childNodes" in node) {
      for (const child of node.childNodes) {
        visitVisibleNode(child, inspection, insideStaticResults);
      }
    }
    return;
  }

  // These subtrees hold executable, styling, or inert payload content rather
  // than rendered page nodes. Do not exclude the HTML `hidden` attribute:
  // React's streamed SSR protocol stages completed Suspense segments in a
  // hidden element, then reveals them with its bootstrap script.
  if (
    node.tagName === "script" ||
    node.tagName === "style" ||
    node.tagName === "template"
  ) {
    return;
  }

  const isStaticResultsRoot = node.attrs.some(
    (attribute) => attribute.name === "data-explore-static-results",
  );
  const isInsideStaticResults = insideStaticResults || isStaticResultsRoot;

  if (isStaticResultsRoot) {
    inspection.staticResultsCount += 1;
  }

  if (isInsideStaticResults) {
    for (const attribute of node.attrs) {
      switch (attribute.name) {
        case "data-search-result-company":
          inspection.companyResultCount += 1;
          break;
        case "data-explore-repository-fallback":
          inspection.repositoryFallbackCount += 1;
          break;
        case "data-posting-id":
          inspection.postingResultCount += 1;
          break;
      }
    }
  }

  for (const child of node.childNodes) {
    visitVisibleNode(child, inspection, isInsideStaticResults);
  }
}

/**
 * Parse an Explore response and inspect its server-rendered body tree. A
 * standards-based parser handles malformed markup; the explicit walk excludes
 * executable and inert payload subtrees without interpreting untrusted
 * response text as selectors or executable DOM input.
 */
export function inspectVisibleExploreHtml(html: string): VisibleExploreHtmlInspection {
  const inspection: VisibleExploreHtmlInspection = {
    staticTextContent: "",
    staticResultsCount: 0,
    companyResultCount: 0,
    repositoryFallbackCount: 0,
    postingResultCount: 0,
  };
  const document = parse(html, { scriptingEnabled: false });
  const body = findBody(document);

  if (body) {
    visitVisibleNode(body, inspection, false);
  }

  return inspection;
}
