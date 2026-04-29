(function () {
  "use strict";

  const MODEL_STYLES = {
    "claude-opus-4-6": { line: "#58a6ff", bandBg: "rgba(88, 166, 255, 0.05)" },
    "claude-opus-4-7": { line: "#a371f7", bandBg: "rgba(163, 113, 247, 0.10)" },
  };
  const DEFAULT_MODEL_STYLE = { line: "#58a6ff", bandBg: "rgba(88, 166, 255, 0.05)" };

  // External events worth correlating with the score timeline. The marker
  // is anchored to the first run on or after `date`, so it lands at the
  // moment the change becomes visible in the data going forward. `tone`
  // selects color: "danger" for "thing got worse here", "warning" for
  // "thing got fixed here". Order in this array also drives vertical
  // staggering so adjacent labels don't overlap.
  const EVENT_MARKERS = [
    {
      date: "2026-04-16",
      tone: "danger",
      label: "Verbosity bug",
      url: "https://www.anthropic.com/engineering/april-23-postmortem",
      description:
        "Anthropic's April 23 postmortem: a system-prompt verbosity instruction shipped on April 16 that degraded Claude responses. Reverted April 20 (v2.1.116).",
    },
    {
      date: "2026-04-20",
      tone: "warning",
      label: "Anthropic fix",
      url: "https://www.anthropic.com/engineering/april-23-postmortem",
      description:
        "Anthropic's April 23 postmortem: three Claude quality issues (reasoning-effort default, caching bug, verbosity instruction) — all resolved by April 20 (v2.1.116).",
    },
  ];

  const TONE_STROKE = {
    danger: "rgba(248, 81, 73, 0.65)",
    warning: "rgba(210, 153, 34, 0.65)",
  };

  function modelStyle(model) {
    return MODEL_STYLES[model] || DEFAULT_MODEL_STYLE;
  }

  function resolveEventMarkers(data, markers) {
    return markers
      .map((m) => {
        const idx = data.findIndex((e) => e.date >= m.date);
        return idx >= 0 ? Object.assign({}, m, { index: idx }) : null;
      })
      .filter(Boolean);
  }

  // Two-phase plugin: afterDatasetsDraw paints the dashed line on the
  // canvas (so it sits cleanly behind the data points and obeys
  // chartArea bounds); afterRender syncs an absolutely-positioned HTML
  // <a> element so the label is a real, focusable, clickable link
  // rather than a non-interactive canvas drawing.
  const eventMarkerPlugin = {
    id: "eventMarker",
    afterDatasetsDraw(chart, _args, opts) {
      const markers = (opts && opts.markers) || [];
      if (markers.length === 0) return;
      const { ctx, chartArea, scales } = chart;
      const xScale = scales.x;
      ctx.save();
      for (const m of markers) {
        const x = xScale.getPixelForValue(m.index);
        ctx.beginPath();
        ctx.setLineDash([4, 3]);
        ctx.strokeStyle = TONE_STROKE[m.tone] || TONE_STROKE.warning;
        ctx.lineWidth = 1.5;
        ctx.moveTo(x, chartArea.top);
        ctx.lineTo(x, chartArea.bottom);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.restore();
    },
    afterRender(chart, _args, opts) {
      const markers = (opts && opts.markers) || [];
      const container = chart.canvas.parentElement;
      if (!container) return;
      let overlay = container.querySelector(".event-markers-overlay");
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.className = "event-markers-overlay";
        container.appendChild(overlay);
      }
      if (markers.length === 0) {
        overlay.innerHTML = "";
        return;
      }
      // Canvas position within container: padding pushes the canvas
      // inward, so chart-pixel coords need that offset added to land in
      // the overlay's coordinate space.
      const canvasRect = chart.canvas.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      const xOffset = canvasRect.left - containerRect.left;
      const yOffset = canvasRect.top - containerRect.top;
      // Stagger labels by array order so adjacent dates don't overlap.
      const ROW_STEP = 26;
      overlay.innerHTML = markers
        .map((m, i) => {
          const x = chart.scales.x.getPixelForValue(m.index);
          const left = (x + xOffset).toFixed(1);
          const top = (chart.chartArea.top + yOffset + i * ROW_STEP).toFixed(1);
          const desc = m.description.replace(/"/g, "&quot;");
          const tone = m.tone || "warning";
          return (
            '<a class="event-marker-link tone-' + tone +
            '" href="' + m.url +
            '" target="_blank" rel="noopener noreferrer"' +
            ' style="left:' + left + 'px;top:' + top + 'px"' +
            ' title="' + desc + '">' +
            m.label +
            ' <span aria-hidden="true">↗</span>' +
            "</a>"
          );
        })
        .join("");
    },
  };

  function formatModelLabel(model) {
    if (!model) return "Unknown";
    const m = /^claude-([a-z]+)-(\d)-(\d+)/.exec(model);
    if (!m) return model;
    return m[1].charAt(0).toUpperCase() + m[1].slice(1) + " " + m[2] + "." + m[3];
  }

  async function loadJSON(url) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      return null;
    }
  }

  async function loadData() {
    const [latest, history] = await Promise.all([
      loadJSON("data/latest.json"),
      loadJSON("data/history.json"),
    ]);
    return { latest, history: history ? history.entries || [] : [] };
  }

  function computeVerdict(latest, history) {
    if (!latest || latest.score == null || history.length === 0) {
      return { verdict: "UNKNOWN", reason: "No historical data yet" };
    }

    // Compare against rolling avg of SAME-MODEL runs only, so adding a
    // reference-baseline model to history can't poison the verdict window.
    const sameModel = history.filter(
      (e) => e.primary_model === latest.primary_model
    );
    const prior = sameModel.slice(0, -1);
    const recent = prior.slice(-14); // ≈ 7 days at 2 runs/day per model

    if (recent.length === 0) {
      return { verdict: "UNKNOWN", reason: "Not enough history for comparison" };
    }

    const todayScore = latest.score;
    const avg = recent.reduce((s, e) => s + e.score, 0) / recent.length;
    const delta = todayScore - avg;

    if (todayScore <= avg - 5) {
      return {
        verdict: "YES",
        reason: `Score ${todayScore}% is ${Math.abs(delta).toFixed(1)}pp below rolling avg (${avg.toFixed(1)}%)`,
      };
    }
    if (todayScore <= avg - 2) {
      return {
        verdict: "MAYBE",
        reason: `Score ${todayScore}% is ${Math.abs(delta).toFixed(1)}pp below rolling avg (${avg.toFixed(1)}%)`,
      };
    }
    return {
      verdict: "NO",
      reason: `Score ${todayScore}% is on par with rolling avg (${avg.toFixed(1)}%)`,
    };
  }

  function renderVerdict(verdictInfo) {
    const pill = document.getElementById("verdict-pill");
    const subtitle = document.getElementById("verdict-subtitle");
    const mascot = document.getElementById("verdict-mascot");

    pill.textContent = verdictInfo.verdict;
    pill.className = "verdict-pill " + verdictInfo.verdict.toLowerCase();
    subtitle.textContent = verdictInfo.reason;

    const mascotMap = {
      YES: "claude_dumb.png",
      MAYBE: "claude_dumb.png",
      NO: "claude_not_dumb.png",
    };
    const src = mascotMap[verdictInfo.verdict];
    if (src) {
      mascot.src = src;
      mascot.alt = verdictInfo.verdict === "NO" ? "Claude is not dumb today" : "Claude is dumb today";
      mascot.style.display = "";
    } else {
      mascot.style.display = "none";
    }
  }

  function relativeTime(date) {
    const diffMs = Date.now() - date.getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + " minute" + (mins === 1 ? "" : "s") + " ago";
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + " hour" + (hrs === 1 ? "" : "s") + " ago";
    if (hrs < 48) return "yesterday";
    const days = Math.floor(hrs / 24);
    return days + " day" + (days === 1 ? "" : "s") + " ago";
  }

  function nextRunRelative() {
    const now = new Date();
    const utcH = now.getUTCHours();
    const utcM = now.getUTCMinutes();
    const next = new Date(now);
    next.setUTCSeconds(0, 0);
    if (utcH < 7 || (utcH === 7 && utcM === 0)) {
      next.setUTCHours(7, 0);
    } else if (utcH < 15 || (utcH === 15 && utcM === 0)) {
      next.setUTCHours(15, 0);
    } else {
      next.setUTCDate(next.getUTCDate() + 1);
      next.setUTCHours(7, 0);
    }
    const diffMs = next.getTime() - now.getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 60) return "in ~" + mins + " minute" + (mins === 1 ? "" : "s");
    const hrs = Math.round(mins / 60);
    return "in ~" + hrs + " hour" + (hrs === 1 ? "" : "s");
  }

  function formatTimestamp(isoStr) {
    const d = new Date(isoStr);
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const mon = months[d.getUTCMonth()];
    const day = d.getUTCDate();
    const hh = String(d.getUTCHours()).padStart(2, "0");
    const mm = String(d.getUTCMinutes()).padStart(2, "0");
    return mon + " " + day + ", " + hh + ":" + mm + " UTC";
  }

  function renderRunTiming(latest) {
    const el = document.getElementById("run-timing");
    if (!el) return;
    const ts = latest.finished_at || latest.run_id;
    if (!ts) { el.textContent = ""; return; }
    const lastChecked = relativeTime(new Date(ts));
    const nextCheck = nextRunRelative();
    el.textContent = "Last checked " + lastChecked + " \u00B7 Next check " + nextCheck;
  }

  function renderSummary(latest, history) {
    if (!latest) return;

    // Deltas compare the latest run to the previous run of the SAME model,
    // so a reference-baseline run doesn't sit between two primary runs and
    // distort the "vs previous" arrow.
    const sameModel = history.filter(
      (e) => e.primary_model === latest.primary_model
    );
    const previous = sameModel.length >= 2 ? sameModel[sameModel.length - 2] : null;

    // Score
    document.getElementById("score-value").textContent = latest.score + "%";
    const deltaEl = document.getElementById("score-delta");
    if (previous) {
      const d = latest.score - previous.score;
      const arrow = d > 0 ? "↑" : d < 0 ? "↓" : "→";
      deltaEl.textContent = `${arrow} ${Math.abs(d).toFixed(1)}pp vs previous run`;
      deltaEl.className = "delta " + (d > 0 ? "positive" : d < 0 ? "negative" : "neutral");
    }

    // Model
    document.getElementById("model-value").textContent = latest.primary_model || "unknown";
    document.getElementById("version-value").textContent = latest.claude_version || "";

    // Cost
    document.getElementById("cost-value").textContent = "$" + latest.total_cost_usd.toFixed(2);
    const costDeltaEl = document.getElementById("cost-delta");
    if (previous) {
      const cd = latest.total_cost_usd - previous.total_cost_usd;
      costDeltaEl.textContent = (cd >= 0 ? "+$" : "-$") + Math.abs(cd).toFixed(2) + " vs previous run";
      costDeltaEl.className = "delta " + (cd > 0 ? "negative" : cd < 0 ? "positive" : "neutral");
    }

    // Runtime
    const mins = (latest.total_duration_ms / 60000).toFixed(1);
    document.getElementById("runtime-value").textContent = mins + " min";
    const ts = latest.finished_at || latest.run_id;
    document.getElementById("date-value").textContent = ts ? formatTimestamp(ts) : latest.date;
  }

  function renderChartLegend(data) {
    const el = document.getElementById("chart-legend");
    if (!el) return;
    const seen = new Set();
    const models = [];
    for (const e of data) {
      const m = e.primary_model;
      if (m && !seen.has(m)) {
        seen.add(m);
        models.push(m);
      }
    }
    if (models.length <= 1) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = models
      .map((m) => {
        const s = modelStyle(m);
        return (
          '<span class="chart-legend-item">' +
          '<span class="chart-legend-swatch" style="background:' +
          s.line +
          '"></span>' +
          formatModelLabel(m) +
          "</span>"
        );
      })
      .join("");
  }

  function renderChart(history) {
    const ctx = document.getElementById("score-chart");
    if (!ctx || history.length === 0) return;

    // Last 90 entries across all models. With two models running per
    // schedule, that's ≈ 22 days of paired runs — enough to see regression
    // signals without crowding the axis.
    const data = history.slice(-90);

    // Show MM-DD only on the first run of each day; blank for subsequent
    // same-day runs. Keeps the axis calm; tooltips carry run-level detail.
    let prevDate = null;
    const labels = data.map((e) => {
      const dateStr = e.date.slice(5); // MM-DD
      if (dateStr !== prevDate) {
        prevDate = dateStr;
        return dateStr;
      }
      return "";
    });

    // Dynamic y-axis: floor the min score to the next lower multiple of 5
    // (with ~3pp headroom below) so the interesting range fills the plot.
    const minScore = Math.min.apply(null, data.map((e) => e.score));
    const yMin = Math.max(0, Math.floor((minScore - 3) / 5) * 5);

    // Group entries by model, preserving first-appearance order so the
    // primary (typically newest) model draws last = on top of the old one.
    const modelOrder = [];
    const seen = new Set();
    for (const e of data) {
      const m = e.primary_model;
      if (m && !seen.has(m)) {
        seen.add(m);
        modelOrder.push(m);
      }
    }

    // One sparse dataset per model: data[i] = score if entry i belongs to
    // this model, else null. spanGaps: true lets each model's line stay
    // continuous across indices occupied by the *other* model.
    const datasets = modelOrder.map((model) => {
      const style = modelStyle(model);
      const values = data.map((e) => (e.primary_model === model ? e.score : null));
      return {
        label: formatModelLabel(model),
        model,
        data: values,
        borderColor: style.line,
        backgroundColor: style.line,
        borderWidth: 2,
        pointRadius: 3,
        pointHoverRadius: 5,
        pointBackgroundColor: style.line,
        pointBorderColor: "#0d1117",
        pointBorderWidth: 1,
        tension: 0.25,
        spanGaps: true,
      };
    });

    renderChartLegend(data);
    const eventMarkers = resolveEventMarkers(data, EVENT_MARKERS);

    new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      plugins: [eventMarkerPlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: false, axis: "x" },
        scales: {
          y: {
            min: yMin,
            max: 100,
            grid: { color: "rgba(48, 54, 61, 0.5)" },
            ticks: { color: "#8b949e", stepSize: 5, callback: (v) => v + "%" },
          },
          x: {
            grid: { display: false },
            ticks: {
              color: "#8b949e",
              maxRotation: 0,
              autoSkip: true,
              autoSkipPadding: 15,
              maxTicksLimit: 10,
            },
          },
        },
        plugins: {
          legend: { display: false },
          eventMarker: { markers: eventMarkers },
          tooltip: {
            // Only show the dataset the user is actually hovering. With
            // sparse per-model series, the non-hovered datasets contribute
            // null at that index and would otherwise render as "null%".
            filter: (item) => item.raw != null,
            callbacks: {
              title: (items) => {
                const idx = items[0].dataIndex;
                const entry = data[idx];
                if (entry.run_id) {
                  return entry.date + " " + entry.run_id.slice(11, 16) + " UTC";
                }
                return entry.date;
              },
              label: (item) => {
                const entry = data[item.dataIndex];
                return `${formatModelLabel(entry.primary_model)}: ${item.raw}% (${entry.passed}/${entry.total})`;
              },
            },
          },
        },
      },
    });
  }

  function miniBadge(flag) {
    // Three-state: true=PASS (pill green), false=FAIL (pill red),
    // undefined=— (legacy runs that didn't record the split).
    if (flag === true) return '<span class="badge pass">P</span>';
    if (flag === false) return '<span class="badge fail">F</span>';
    return '<span class="rate">—</span>';
  }

  function renderTaskTable(tasks) {
    const tbody = document.getElementById("tasks-body");
    if (!tasks || tasks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="loading">No task data available</td></tr>';
      return;
    }

    tbody.innerHTML = tasks
      .map(
        (t) => `
      <tr class="${t.passed ? "pass" : "fail"}">
        <td>${t.task_id}</td>
        <td><code>${t.function_name}</code></td>
        <td><span class="badge ${t.passed ? "pass" : "fail"}">${t.passed ? "PASS" : "FAIL"}</span></td>
        <td>${miniBadge(t.passed_base)}</td>
        <td>${miniBadge(t.passed_evalplus)}</td>
        <td>${t.attempts_used}</td>
        <td>${t.num_turns_total}</td>
        <td>$${t.total_cost_usd_total.toFixed(3)}</td>
        <td>${t.error_type || "—"}</td>
      </tr>`
      )
      .join("");

    document.querySelectorAll("#tasks-table th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        const sorted = [...tasks].sort((a, b) => {
          const av = a[key],
            bv = b[key];
          if (typeof av === "boolean") return av === bv ? 0 : av ? -1 : 1;
          if (typeof av === "number") return bv - av;
          return String(av).localeCompare(String(bv));
        });
        renderTaskTable(sorted);
      });
    });
  }

  // --- Divergence view -------------------------------------------------

  function bitmapToBool(bitmap, idx) {
    if (!bitmap || idx >= bitmap.length) return null;
    const c = bitmap[idx];
    if (c === "1") return true;
    if (c === "0") return false;
    return null;
  }

  function computeTaskStats(history) {
    // Use the last N runs that carry bitmaps. Entries produced before the
    // split-scoring reform have no bitmap and are simply skipped.
    const WINDOW = 14; // ~7 days at 2 paired runs/day

    const withBitmaps = history.filter(
      (e) => e.pass_bitmap_base && e.pass_bitmap_evalplus && e.task_ids && e.task_ids.length
    );
    if (withBitmaps.length === 0) return null;

    // Trim to the last WINDOW runs per model so an asymmetric backlog
    // doesn't bias the pass-rate.
    const byModel = {};
    for (const e of withBitmaps) {
      (byModel[e.primary_model] = byModel[e.primary_model] || []).push(e);
    }
    for (const m of Object.keys(byModel)) {
      byModel[m] = byModel[m].slice(-WINDOW);
    }
    const models = Object.keys(byModel);
    if (models.length < 2) return null;

    // Canonical task_ids list: use the most recent run's order. Runs are
    // expected to share the same set post-quarantine, but we tolerate drift
    // by using the superset.
    const latestTaskIds = withBitmaps[withBitmaps.length - 1].task_ids;
    const taskIdIndex = new Map(latestTaskIds.map((t, i) => [t, i]));

    // For each task, collect per-run pass/fail per model.
    const stats = latestTaskIds.map((task_id) => {
      const perModel = {};
      for (const m of models) {
        const runs = byModel[m];
        const trail = runs.map((e) => {
          const idx = e.task_ids.indexOf(task_id);
          if (idx < 0) return null;
          const base = bitmapToBool(e.pass_bitmap_base, idx);
          const plus = bitmapToBool(e.pass_bitmap_evalplus, idx);
          if (base == null || plus == null) return null;
          return base && plus; // combined = pass both
        }).filter((v) => v != null);
        const passed = trail.filter((v) => v).length;
        perModel[m] = { passed, total: trail.length, trail };
      }
      return { task_id, perModel };
    });
    return { stats, models };
  }

  function rateCell(passed, total) {
    if (total === 0) return '<span class="rate">—</span>';
    const pct = Math.round((passed / total) * 100);
    const cls = pct >= 80 ? "high" : pct <= 20 ? "low" : "";
    return `<span class="rate ${cls}">${passed}/${total}<span class="pct">${pct}%</span></span>`;
  }

  function sparkdots(trail) {
    // One 8px dot per run. Left = oldest, right = newest, matching the
    // line chart's direction so viewers can correlate at a glance.
    return (
      '<span class="sparkdots" aria-hidden="true">' +
      trail.map((v) => `<span class="${v ? "p" : "f"}"></span>`).join("") +
      "</span>"
    );
  }

  function renderDivergenceTable(history) {
    const tbody = document.getElementById("divergence-body");
    const note = document.getElementById("divergence-note");
    if (!tbody) return;

    const computed = computeTaskStats(history);
    if (!computed) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="loading">No bitmap history yet — this view populates once runs have recorded the new pass bitmaps.</td></tr>';
      return;
    }

    const { stats, models } = computed;
    // Prefer 4.7 as "primary" column, 4.6 as "reference"; fall back to
    // whatever two models we have (alphabetical order) so the view still
    // works if the lineup changes.
    const preferred = ["claude-opus-4-7", "claude-opus-4-6"];
    let mA, mB;
    if (preferred.every((m) => models.includes(m))) {
      [mA, mB] = preferred;
    } else {
      const sorted = [...models].sort();
      [mA, mB] = [sorted[sorted.length - 1], sorted[0]];
    }

    // Row is "interesting" iff the two models have different pass rates
    // over the window. Ties (0-0, N-N) are dropped — they're not signal.
    const rows = stats
      .map((s) => {
        const a = s.perModel[mA] || { passed: 0, total: 0, trail: [] };
        const b = s.perModel[mB] || { passed: 0, total: 0, trail: [] };
        const rateA = a.total ? a.passed / a.total : 0;
        const rateB = b.total ? b.passed / b.total : 0;
        const delta = rateA - rateB;
        return { ...s, a, b, delta };
      })
      .filter((r) => Math.abs(r.delta) > 1e-6)
      .sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta));

    if (rows.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="loading">No per-task divergences in the recent window.</td></tr>';
      return;
    }

    const header = document.querySelector("#divergence-table thead tr");
    if (header) {
      header.innerHTML = `
        <th>Task</th>
        <th>${formatModelLabel(mA)}</th>
        <th>${formatModelLabel(mB)}</th>
        <th>Delta</th>`;
    }

    const fmtDelta = (d) => {
      const pp = Math.round(d * 100);
      const cls = pp === 0 ? "zero" : pp > 0 ? "positive" : "negative";
      const sign = pp > 0 ? "+" : "";
      return `<span class="delta-num ${cls}">${sign}${pp}pp</span>`;
    };

    tbody.innerHTML = rows
      .map(
        (r) => `
      <tr>
        <td>${r.task_id}</td>
        <td>${rateCell(r.a.passed, r.a.total)} ${sparkdots(r.a.trail)}</td>
        <td>${rateCell(r.b.passed, r.b.total)} ${sparkdots(r.b.trail)}</td>
        <td>${fmtDelta(r.delta)}</td>
      </tr>`
      )
      .join("");

    if (note) {
      note.insertAdjacentHTML(
        "beforeend",
        ` <span class="pct">(window: last ${Math.min(14, Math.max(...models.map((m) => (computed.stats[0].perModel[m] || {}).total || 0)))} runs per model)</span>`
      );
    }
  }

  async function init() {
    const { latest, history } = await loadData();

    if (!latest) {
      renderVerdict({ verdict: "UNKNOWN", reason: "No benchmark data found yet" });
      return;
    }

    const verdictInfo = computeVerdict(latest, history);
    renderVerdict(verdictInfo);
    renderRunTiming(latest);
    renderSummary(latest, history);
    renderChart(history);
    renderDivergenceTable(history);
    renderTaskTable(latest.tasks);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
