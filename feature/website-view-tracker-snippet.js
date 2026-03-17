/*
  Embed this snippet on each tracked website to send page-view events
  into the shared portfolio tracker used by feature/index.html.

  Configure SITE_SLUG as one of:
  - lace_luxx_com
  - modular_equity_com
  - michael_kaminski_io
*/
(function () {
  const API = "https://abacus.jasoncameron.dev";
  const NS = "makaminski_site_views_v1";
  const SITE_SLUG = "lace_luxx_com";

  const aiPattern = /bot|crawler|spider|gptbot|chatgpt-user|anthropic-ai|claudebot|perplexitybot|ccbot|bytespider/i;
  const isAI = aiPattern.test(navigator.userAgent);
  const viewerStorageKey = `mk_site_viewer_id_${SITE_SLUG}`;
  const isNewHuman = !isAI && !localStorage.getItem(viewerStorageKey);

  if (isNewHuman) {
    localStorage.setItem(viewerStorageKey, `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  }

  const hit = (key) => fetch(`${API}/hit/${NS}/${key}`).catch(() => null);

  // Per-site counters.
  hit(`site_${SITE_SLUG}_views_total`);
  hit(isAI ? `site_${SITE_SLUG}_views_ai` : `site_${SITE_SLUG}_views_human`);
  if (isNewHuman) {
    hit(`site_${SITE_SLUG}_real_human_views`);
  }

  // Portfolio-level aggregate counters.
  hit("portfolio_views_total");
  hit(isAI ? "portfolio_views_ai" : "portfolio_views_human");
  if (isNewHuman) {
    hit("portfolio_real_human_views");
  }
})();
