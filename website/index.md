---
title: EnergyCast AI — Engineering Journal
description: Field notes from building a local, reproducible electricity-demand forecasting system with AI orchestration.
---

<section class="home-hero">
  <div class="shell hero-grid">
    <div>
      <p class="eyebrow">Open engineering journal</p>
      <h1>Forecasting the grid.<br><span>Documenting the build.</span></h1>
      <p class="hero-copy">EnergyCast AI is a local, reproducible forecasting project for ERCOT electricity demand. This journal records the data engineering, model failures, validated results, and production safeguards behind it.</p>
      <div class="hero-actions">
        <a class="button primary" href="{{ '/blog/building-energycast-ai/' | relative_url }}">Read the build story</a>
        <a class="button secondary" href="https://github.com/{{ site.repository }}">Explore the repository</a>
      </div>
    </div>
    <div class="signal-panel" aria-label="Selected validated results">
      <p class="signal-label">Validated operational backtests</p>
      <div class="metric"><span>Monthly energy</span><strong>4.409%</strong><small>WAPE · E14</small></div>
      <div class="metric"><span>Monthly peak</span><strong>6.201%</strong><small>WAPE · E24-B</small></div>
      <p class="signal-note">Historical rolling-origin results. Not a comparison with official ERCOT forecasts.</p>
    </div>
  </div>
</section>

<section class="shell latest-section">
  <div class="section-heading">
    <p class="eyebrow">Latest article</p>
    <h2>An AI-orchestrated engineering project</h2>
  </div>
  {% for post in site.posts limit:1 %}
  <article class="post-card">
    <div class="post-number">01</div>
    <div>
      <p class="post-meta">{{ post.date | date: "%B %-d, %Y" }} · {{ post.reading_time }}</p>
      <h3><a href="{{ post.url | relative_url }}">{{ post.title }}</a></h3>
      <p>{{ post.excerpt | strip_html | truncatewords: 42 }}</p>
      <a class="text-link" href="{{ post.url | relative_url }}">Read the full field note <span aria-hidden="true">→</span></a>
    </div>
  </article>
  {% endfor %}
</section>

<section class="principles">
  <div class="shell">
    <p class="eyebrow">Operating principles</p>
    <div class="principle-grid">
      <div><span>01</span><h3>Evidence before claims</h3><p>Every published metric traces to a stored rolling-origin evaluation.</p></div>
      <div><span>02</span><h3>Baselines before complexity</h3><p>Advanced models earn their place only by beating strong statistical controls.</p></div>
      <div><span>03</span><h3>Guardrails before automation</h3><p>Checksums, cutoff audits, and atomic publication precede unattended operation.</p></div>
    </div>
  </div>
</section>
