/* Pure catalog state helpers. This file is embedded into generated pages and
 * can also be required by Node tests without a DOM. */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.OutcomeCatalogState = api;
})(typeof globalThis === "undefined" ? null : globalThis, function () {
  "use strict";
  const SORTS = new Set(["source", "updated", "title"]);
  const SIZES = new Set([20, 50]);
  function positiveInteger(value, fallback) { const parsed = Number.parseInt(value, 10); return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback; }
  function normalized(value) { return String(value || "").toLocaleLowerCase("zh-Hant"); }
  function parseState(search, defaultSize) {
    const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
    const size = Number(params.get("size"));
    return { q: params.get("q") || "", topic: params.get("topic") || "", research_topic: params.get("research_topic") || "", research_facet: params.get("research_facet") || "", status: params.get("status") || "", sort: SORTS.has(params.get("sort")) ? params.get("sort") : "source", page: positiveInteger(params.get("page"), 1), size: SIZES.has(size) ? size : positiveInteger(defaultSize, 20) };
  }
  function stateToSearch(state) {
    const params = new URLSearchParams();
    if (state.q) params.set("q", state.q); if (state.topic) params.set("topic", state.topic); if (state.research_topic) params.set("research_topic", state.research_topic); if (state.research_facet) params.set("research_facet", state.research_facet); if (state.status) params.set("status", state.status); if (state.sort && state.sort !== "source") params.set("sort", state.sort); if (state.size && state.size !== 20) params.set("size", String(state.size)); if (state.page && state.page > 1) params.set("page", String(state.page)); return params.toString();
  }
  function sortItems(items, sort) {
    const indexed = items.map((item, index) => ({ item, index }));
    if (sort === "updated") indexed.sort((left, right) => String(right.item.updated_at || "").localeCompare(String(left.item.updated_at || "")) || left.index - right.index);
    else if (sort === "title") indexed.sort((left, right) => String(left.item.title || "").localeCompare(String(right.item.title || ""), "zh-Hant") || left.index - right.index);
    return indexed.map(({ item }) => item);
  }
  function selectItems(items, kind, state) {
    const needle = normalized(state.q).trim();
    const filtered = items.filter((item) => {
      const searchable = [item.title, item.summary];
      const matchesText = !needle || searchable.some((value) => normalized(value).includes(needle));
      const itemTopics = [item.topic];
      const matchesTopic = !state.topic || itemTopics.includes(state.topic);
      const researchTopics = Array.isArray(item.research_topic_ids) ? item.research_topic_ids : [];
      const matchesResearchTopic = !state.research_topic || researchTopics.includes(state.research_topic);
      const researchFacets = item.research_facets && typeof item.research_facets === "object" ? item.research_facets : {};
      const facetValues = Object.entries(researchFacets).flatMap(([kind, values]) => (Array.isArray(values) ? values : []).map(value => `${kind}:${value}`));
      const matchesResearchFacet = !state.research_facet || facetValues.includes(state.research_facet);
      const matchesStatus = !state.status || (state.status === "needs_review" ? Boolean(item.needs_review) : item.status === state.status);
      return matchesText && matchesTopic && matchesResearchTopic && matchesResearchFacet && matchesStatus;
    });
    return sortItems(filtered, state.sort);
  }
  function paginate(items, page, size) { const pageCount = Math.max(1, Math.ceil(items.length / size)); const currentPage = Math.max(1, Math.min(positiveInteger(page, 1), pageCount)); return { items: items.slice((currentPage - 1) * size, currentPage * size), currentPage, pageCount, total: items.length }; }
  return { parseState, stateToSearch, selectItems, sortItems, paginate };
});
