(function () {
  "use strict";

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

    // Use last 21 entries (excluding the most recent) as comparison window.
    // 21 entries ≈ 7 days at 3 runs/day.
    const prior = history.slice(0, -1);
    const recent = prior.slice(-21);

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

    pill.textContent = verdictInfo.verdict;
    pill.className = "verdict-pill " + verdictInfo.verdict.toLowerCase();
    subtitle.textContent = verdictInfo.reason;
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
    document.getElementById("date-value").textContent = latest.date;
  }

  function renderChart(history) {
    const ctx = document.getElementById("score-chart");
    if (!ctx || history.length === 0) return;

    // Last 90 entries (≈ 30 days at 3 runs/day)
    const data = history.slice(-90);
    const scores = data.map((e) => e.score);

    // Smart x-axis labels: show MM-DD for first run of each day, HH:mm for subsequent same-day runs
    let prevDate = null;
    const labels = data.map((e) => {
      const dateStr = e.date.slice(5); // MM-DD
      if (dateStr !== prevDate) {
        prevDate = dateStr;
        return dateStr;
      }
      // Same day — show time from run_id if available
      if (e.run_id) {
        return e.run_id.slice(11, 16); // HH:mm
      }
      return "";
    });

    new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Score (%)",
            data: scores,
            borderColor: "#58a6ff",
            backgroundColor: "rgba(88, 166, 255, 0.1)",
            borderWidth: 2,
            pointRadius: 4,
            pointBackgroundColor: scores.map((s) =>
              s >= 92.5 ? "#3fb950" : s >= 85 ? "#d29922" : "#f85149"
            ),
            fill: true,
            tension: 0.2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            min: 0,
            max: 100,
            grid: { color: "rgba(48, 54, 61, 0.6)" },
            ticks: { color: "#8b949e" },
          },
          x: {
            grid: { color: "rgba(48, 54, 61, 0.3)" },
            ticks: { color: "#8b949e", maxRotation: 45 },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => {
                const idx = items[0].dataIndex;
                const entry = data[idx];
                // Show full date + time if run_id is available
                if (entry.run_id) {
                  return entry.date + " " + entry.run_id.slice(11, 16) + " UTC";
                }
                return entry.date;
              },
              label: (item) =>
                `Score: ${item.raw}% (${data[item.dataIndex].passed}/${data[item.dataIndex].total})`,
            },
          },
        },
      },
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
    renderSummary(latest, history);
    renderChart(history);
    renderTaskTable(latest.tasks);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
