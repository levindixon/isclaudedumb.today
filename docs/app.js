(function () {
  "use strict";

  const MODEL_STYLES = {
    "claude-opus-4-6": { line: "#58a6ff", bandBg: "rgba(88, 166, 255, 0.05)" },
    "claude-opus-4-7": { line: "#a371f7", bandBg: "rgba(163, 113, 247, 0.10)" },
  };
  const DEFAULT_MODEL_STYLE = { line: "#58a6ff", bandBg: "rgba(88, 166, 255, 0.05)" };

  function modelStyle(model) {
    return MODEL_STYLES[model] || DEFAULT_MODEL_STYLE;
  }

  function formatModelLabel(model) {
    if (!model) return "Unknown";
    const m = /^claude-([a-z]+)-(\d)-(\d+)/.exec(model);
    if (!m) return model;
    return m[1].charAt(0).toUpperCase() + m[1].slice(1) + " " + m[2] + "." + m[3];
  }

  function computeModelBoundaries(data) {
    const out = [];
    for (let i = 1; i < data.length; i++) {
      const a = data[i - 1].primary_model;
      const b = data[i].primary_model;
      if (a && b && a !== b) out.push({ index: i, from: a, to: b });
    }
    return out;
  }

  function roundedRectPath(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  const modelBoundaryPlugin = {
    id: "modelBoundary",
    beforeDatasetsDraw(chart, _args, opts) {
      const boundaries = opts && opts.boundaries;
      if (!boundaries || boundaries.length === 0) return;
      const data = opts && opts.data;
      if (!data || data.length === 0) return;
      const { ctx, chartArea, scales } = chart;
      const xScale = scales.x;

      // Paint a translucent band for each era after the first boundary,
      // from the transition midpoint to the next boundary (or right edge).
      ctx.save();
      for (let i = 0; i < boundaries.length; i++) {
        const b = boundaries[i];
        const xLeft = xScale.getPixelForValue(b.index - 1);
        const xRight = xScale.getPixelForValue(b.index);
        const startX = (xLeft + xRight) / 2;
        let endX = chartArea.right;
        if (i + 1 < boundaries.length) {
          const nb = boundaries[i + 1];
          const nxL = xScale.getPixelForValue(nb.index - 1);
          const nxR = xScale.getPixelForValue(nb.index);
          endX = (nxL + nxR) / 2;
        }
        const style = modelStyle(b.to);
        ctx.fillStyle = style.bandBg;
        ctx.fillRect(
          startX,
          chartArea.top,
          endX - startX,
          chartArea.bottom - chartArea.top
        );
      }
      ctx.restore();
    },
    afterDatasetsDraw(chart, _args, opts) {
      const boundaries = opts && opts.boundaries;
      if (!boundaries || boundaries.length === 0) return;
      const { ctx, chartArea, scales } = chart;
      const xScale = scales.x;
      ctx.save();
      for (const b of boundaries) {
        const xLeft = xScale.getPixelForValue(b.index - 1);
        const xRight = xScale.getPixelForValue(b.index);
        const x = (xLeft + xRight) / 2;
        const style = modelStyle(b.to);

        // Vertical dashed divider spanning the plot area.
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = style.line;
        ctx.lineWidth = 1.25;
        ctx.globalAlpha = 0.7;
        ctx.moveTo(x, chartArea.top);
        ctx.lineTo(x, chartArea.bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;

        // Solid-background pill label with colored border & text.
        ctx.font = "600 11px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
        const label = formatModelLabel(b.to);
        const padX = 8;
        const labelW = ctx.measureText(label).width;
        const boxW = labelW + padX * 2;
        const boxH = 20;
        const gap = 6;
        // Prefer placing the label to the left of the divider so it doesn't
        // collide with the chart's right edge or recent points.
        const boxX =
          x - gap - boxW >= chartArea.left + 2
            ? x - gap - boxW
            : Math.min(x + gap, chartArea.right - boxW - 2);
        const boxY = chartArea.top + 6;
        const r = 5;

        ctx.fillStyle = "rgba(22, 27, 34, 0.95)";
        roundedRectPath(ctx, boxX, boxY, boxW, boxH, r);
        ctx.fill();
        ctx.strokeStyle = style.line;
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = style.line;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(label, boxX + padX, boxY + boxH / 2);
      }
      ctx.restore();
    },
  };

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

  function computeVerdict(todayScore, history) {
    if (todayScore == null || history.length === 0) {
      return { verdict: "UNKNOWN", reason: "No historical data yet" };
    }

    // Use last 14 entries (excluding the most recent) as comparison window.
    // 14 entries ≈ 7 days at 2 runs/day.
    const prior = history.slice(0, -1);
    const recent = prior.slice(-14);

    if (recent.length === 0) {
      return { verdict: "UNKNOWN", reason: "Not enough history for comparison" };
    }

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

    // Score
    const scoreEl = document.getElementById("score-value");
    scoreEl.textContent = latest.score + "%";

    // Score delta vs previous run
    const deltaEl = document.getElementById("score-delta");
    if (history.length >= 2) {
      const previous = history[history.length - 2];
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
    if (history.length >= 2) {
      const yCost = history[history.length - 2].total_cost_usd;
      const cd = latest.total_cost_usd - yCost;
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

    // Last 90 entries (≈ 30 days at 3 runs/day)
    const data = history.slice(-90);
    const scores = data.map((e) => e.score);

    // Show MM-DD only on the first run of each day; blank for subsequent
    // same-day runs. Keeps the axis calm and lets tooltips carry the detail.
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
    const minScore = Math.min.apply(null, scores);
    const yMin = Math.max(0, Math.floor((minScore - 3) / 5) * 5);

    const boundaries = computeModelBoundaries(data);
    renderChartLegend(data);

    new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Score (%)",
            data: scores,
            borderColor: "#58a6ff",
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBorderColor: "#0d1117",
            pointBorderWidth: 1,
            pointBackgroundColor: scores.map((s) =>
              s >= 92.5 ? "#3fb950" : s >= 85 ? "#d29922" : "#f85149"
            ),
            tension: 0.25,
            segment: {
              borderColor: (c) => {
                const entry = data[c.p1DataIndex];
                return modelStyle(entry && entry.primary_model).line;
              },
            },
          },
        ],
      },
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
          modelBoundary: { boundaries, data },
          tooltip: {
            callbacks: {
              title: (items) => {
                const idx = items[0].dataIndex;
                const entry = data[idx];
                if (entry.run_id) {
                  return entry.date + " " + entry.run_id.slice(11, 16) + " UTC";
                }
                return entry.date;
              },
              label: (item) =>
                `Score: ${item.raw}% (${data[item.dataIndex].passed}/${data[item.dataIndex].total})`,
              afterLabel: (item) => {
                const m = data[item.dataIndex].primary_model;
                return m ? "Model: " + formatModelLabel(m) : "";
              },
            },
          },
        },
      },
      plugins: [modelBoundaryPlugin],
    });
  }

  function renderTaskTable(tasks) {
    const tbody = document.getElementById("tasks-body");
    if (!tasks || tasks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="loading">No task data available</td></tr>';
      return;
    }

    tbody.innerHTML = tasks
      .map(
        (t) => `
      <tr class="${t.passed ? "pass" : "fail"}">
        <td>${t.task_id}</td>
        <td><code>${t.function_name}</code></td>
        <td><span class="badge ${t.passed ? "pass" : "fail"}">${t.passed ? "PASS" : "FAIL"}</span></td>
        <td>${t.attempts_used}</td>
        <td>${t.num_turns_total}</td>
        <td>$${t.total_cost_usd_total.toFixed(3)}</td>
        <td>${t.error_type || "—"}</td>
      </tr>`
      )
      .join("");

    // Sortable headers
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

  async function init() {
    const { latest, history } = await loadData();

    if (!latest) {
      renderVerdict({ verdict: "UNKNOWN", reason: "No benchmark data found yet" });
      return;
    }

    const verdictInfo = computeVerdict(latest.score, history);
    renderVerdict(verdictInfo);
    renderRunTiming(latest);
    renderSummary(latest, history);
    renderChart(history);
    renderTaskTable(latest.tasks);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
