---
title: About EnergyCast AI
description: About the EnergyCast AI research project and this engineering journal.
permalink: /about/
---

<section class="page-hero">
  <div class="shell narrow">
    <p class="eyebrow">About the project</p>
    <h1>A local forecasting laboratory for the Texas grid.</h1>
    <p>EnergyCast AI studies monthly ERCOT energy consumption and peak demand across eight weather zones and the system as a whole. The project combines public electricity and weather data, rolling-origin evaluation, statistical and machine-learning models, and a guarded deterministic production design.</p>
  </div>
</section>

<section class="shell narrow prose standalone">
  <h2>Why this journal exists</h2>
  <p>The repository contains the scripts and detailed phase reports. This journal explains the decisions connecting them: why particular datasets were used, why complex models sometimes lost to statistical baselines, how leakage was prevented, and where human review remained essential.</p>

  <h2>Current release state</h2>
  <p>The fitted deterministic V1 bundle has been validated for reproducible inference. Live forecast publication and unattended monthly scheduling were not activated at the documented milestone. Probabilistic intervals remain research-only pending independent confirmation.</p>

  <h2>Source and evidence</h2>
  <p>Read the <a href="https://github.com/{{ site.repository }}">EnergyCast AI repository</a> for acquisition manifests, test suites, model comparison reports, and production validation evidence.</p>
</section>
