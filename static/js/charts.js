'use strict';

// ---------------------------------------------------------------------------
// Chart instances
// ---------------------------------------------------------------------------
let heightChart, periodChart, directionChart;

// ---------------------------------------------------------------------------
// Shared chart configuration helpers
// ---------------------------------------------------------------------------

function sharedScaleX(xMin, xMax) {
  return {
    type: 'time',
    min: xMin,
    max: xMax,
    time: {
      tooltipFormat: 'MMM d, HH:mm',
      displayFormats: { hour: 'MMM d HH:mm', day: 'MMM d' },
    },
    grid: { color: '#2a2a2a' },
    ticks: { color: '#888', maxTicksLimit: 8 },
  };
}

function sharedScaleY(title, extra = {}) {
  return {
    grid: { color: '#2a2a2a' },
    ticks: { color: '#888' },
    title: { display: true, text: title, color: '#888', font: { size: 11 } },
    ...extra,
  };
}

function sharedPlugins(titleText) {
  return {
    legend: { display: false },
    tooltip: {
      mode: 'index',
      intersect: false,
      backgroundColor: '#1e1e1e',
      borderColor: '#333',
      borderWidth: 1,
      titleColor: '#ccc',
      bodyColor: '#aaa',
      callbacks: {
        title(items) {
          if (!items.length) return '';
          const d = new Date(items[0].parsed.x);
          return d.toLocaleString('en-US', {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
            timeZoneName: 'short',
          });
        },
      },
    },
  };
}

function makeChartConfig(canvasId, yLabel, yExtra, tooltipSuffix) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: { datasets: [] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', axis: 'x', intersect: false },
      elements: {
        point: { radius: 0, hoverRadius: 4 },
        line: { borderWidth: 2.5, tension: 0.35, fill: false },
      },
      plugins: {
        ...sharedPlugins(yLabel),
        tooltip: {
          ...sharedPlugins(yLabel).tooltip,
          callbacks: {
            ...sharedPlugins(yLabel).tooltip.callbacks,
            label(item) {
              const val = item.parsed.y;
              if (val == null) return null;
              return `  ${item.dataset.label}: ${val}${tooltipSuffix}`;
            },
          },
        },
      },
      scales: {
        x: sharedScaleX(undefined, undefined),
        y: sharedScaleY(yLabel, yExtra),
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Direction chart – compass ticks
// ---------------------------------------------------------------------------
const COMPASS_LABELS = {
  0: 'N', 45: 'NE', 90: 'E', 135: 'SE',
  180: 'S', 225: 'SW', 270: 'W', 315: 'NW', 360: 'N',
};

function directionChartConfig() {
  const ctx = document.getElementById('direction-chart').getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: { datasets: [] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', axis: 'x', intersect: false },
      elements: {
        point: { radius: 0, hoverRadius: 4 },
        line: { borderWidth: 2.5, tension: 0.35, fill: false },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: 'index',
          intersect: false,
          backgroundColor: '#1e1e1e',
          borderColor: '#333',
          borderWidth: 1,
          titleColor: '#ccc',
          bodyColor: '#aaa',
          callbacks: {
            title(items) {
              if (!items.length) return '';
              const d = new Date(items[0].parsed.x);
              return d.toLocaleString('en-US', {
                month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit',
                timeZoneName: 'short',
              });
            },
            label(item) {
              const val = item.parsed.y;
              if (val == null) return null;
              const compass = degToCompass(val);
              return `  ${item.dataset.label}: ${val}° (${compass})`;
            },
          },
        },
      },
      scales: {
        x: sharedScaleX(undefined, undefined),
        y: {
          ...sharedScaleY('Direction (° from)'),
          min: 0,
          max: 360,
          ticks: {
            color: '#888',
            stepSize: 45,
            callback: (v) => COMPASS_LABELS[v] !== undefined
              ? `${COMPASS_LABELS[v]} ${v}°`
              : `${v}°`,
          },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
function initCharts() {
  heightChart    = makeChartConfig('height-chart',    'Height (ft)', { beginAtZero: true }, ' ft');
  periodChart    = makeChartConfig('period-chart',    'Period (s)',  { beginAtZero: false }, 's');
  directionChart = directionChartConfig();
}

// ---------------------------------------------------------------------------
// Sync x-axis across all three charts
// ---------------------------------------------------------------------------
function syncXAxis(xMin, xMax) {
  for (const chart of [heightChart, periodChart, directionChart]) {
    chart.options.scales.x.min = xMin;
    chart.options.scales.x.max = xMax;
  }
}

// ---------------------------------------------------------------------------
// Build datasets from swell data
// ---------------------------------------------------------------------------
function buildDatasets(swells) {
  const heightDs = [], periodDs = [], dirDs = [];

  // Sort: swells first (tallest avg), windsea last
  const sorted = Object.entries(swells).sort(([, a], [, b]) => {
    if (a.is_windsea !== b.is_windsea) return a.is_windsea ? 1 : -1;
    const avgH = (arr) => arr.data.reduce((s, d) => s + (d.height_ft || 0), 0) / arr.data.length;
    return avgH(b) - avgH(a);
  });

  for (const [sid, swell] of sorted) {
    const color = swell.color;
    const label = swell.label || sid;

    const dash = swell.is_windsea ? [5, 3] : [];

    const base = {
      label,
      borderColor: color,
      backgroundColor: color + '18',
      borderDash: dash,
      spanGaps: true,
    };

    heightDs.push({
      ...base,
      data: swell.data
        .filter(d => d.height_ft != null)
        .map(d => ({ x: new Date(d.timestamp), y: d.height_ft })),
    });

    periodDs.push({
      ...base,
      data: swell.data
        .filter(d => d.period_s != null)
        .map(d => ({ x: new Date(d.timestamp), y: d.period_s })),
    });

    dirDs.push({
      ...base,
      data: swell.data
        .filter(d => d.direction_deg != null)
        .map(d => ({ x: new Date(d.timestamp), y: d.direction_deg })),
    });
  }

  return { heightDs, periodDs, dirDs };
}

// ---------------------------------------------------------------------------
// Render charts
// ---------------------------------------------------------------------------
function renderCharts(swells) {
  if (!swells || !Object.keys(swells).length) {
    showStatus('No swell data available for this station / time window.', 'warn');
    return;
  }

  const { heightDs, periodDs, dirDs } = buildDatasets(swells);

  // Determine overall time bounds
  let xMin = Infinity, xMax = -Infinity;
  for (const ds of heightDs) {
    for (const pt of ds.data) {
      const t = pt.x.getTime();
      if (t < xMin) xMin = t;
      if (t > xMax) xMax = t;
    }
  }
  if (!isFinite(xMin)) { xMin = undefined; xMax = undefined; }

  syncXAxis(xMin, xMax);

  heightChart.data.datasets    = heightDs;
  periodChart.data.datasets    = periodDs;
  directionChart.data.datasets = dirDs;

  heightChart.update('none');
  periodChart.update('none');
  directionChart.update('none');
}

// ---------------------------------------------------------------------------
// Conditions panel
// ---------------------------------------------------------------------------
function renderConditions(latest, stationName) {
  document.getElementById('station-heading').textContent =
    stationName ? `Current Conditions — ${stationName}` : 'Current Conditions';

  if (!latest) return;

  const fmt = (v, unit) => v != null ? `${v}${unit}` : '—';
  const windDir = latest.wind_dir_deg != null ? degToCompass(latest.wind_dir_deg) : '';

  const cards = [
    { label: 'Wave Height',     value: fmt(latest.wvht_ft, ' ft') },
    { label: 'Dominant Period', value: fmt(latest.dominant_period_s, ' s') },
    { label: 'Wave Direction',  value: latest.dominant_dir_deg != null
        ? `${degToCompass(latest.dominant_dir_deg)} (${latest.dominant_dir_deg}°)` : '—' },
    { label: 'Wind',            value: latest.wind_speed_mph != null
        ? `${latest.wind_speed_mph} mph ${windDir}` : '—' },
    { label: 'Wind Gust',       value: fmt(latest.wind_gust_mph, ' mph') },
    { label: 'Water Temp',      value: fmt(latest.water_temp_f, '°F') },
    { label: 'Air Temp',        value: fmt(latest.air_temp_f, '°F') },
    { label: 'Pressure',        value: fmt(latest.pressure_inhg, ' inHg') },
  ];

  const panel = document.getElementById('conditions-panel');
  panel.innerHTML = cards.map(c => `
    <div class="condition-card">
      <span class="cond-label">${c.label}</span>
      <span class="cond-value">${c.value}</span>
    </div>
  `).join('');
}

// ---------------------------------------------------------------------------
// Swell legend
// ---------------------------------------------------------------------------
function renderLegend(swells) {
  const section = document.getElementById('legend-section');
  const legend  = document.getElementById('swell-legend');

  if (!swells || !Object.keys(swells).length) {
    section.classList.add('hidden');
    return;
  }

  section.classList.remove('hidden');

  const sorted = Object.entries(swells).sort(([, a], [, b]) => {
    if (a.is_windsea !== b.is_windsea) return a.is_windsea ? 1 : -1;
    const avgH = (s) => s.data.reduce((t, d) => t + (d.height_ft || 0), 0) / s.data.length;
    return avgH(b) - avgH(a);
  });

  legend.innerHTML = `
    <span class="legend-heading">Tracked Swells</span>
    ${sorted.map(([sid, s]) => {
      const lastPt = s.data[s.data.length - 1];
      const ht   = lastPt?.height_ft != null ? `${lastPt.height_ft} ft` : '';
      const per  = lastPt?.period_s  != null ? `${lastPt.period_s} s`  : '';
      const dir  = lastPt?.direction_deg != null
        ? `${degToCompass(lastPt.direction_deg)} ${lastPt.direction_deg}°` : '';
      const tag  = s.is_windsea
        ? '<span class="badge windsea">Wind Sea</span>'
        : '<span class="badge swell">Swell</span>';
      return `
        <div class="legend-item">
          <span class="legend-swatch" style="background:${s.color}"></span>
          <span class="legend-label">${s.label || sid}</span>
          ${tag}
          <span class="legend-stats">${[ht, per, dir].filter(Boolean).join(' · ')}</span>
        </div>`;
    }).join('')}
  `;
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------
function showStatus(msg, type = 'info') {
  const bar = document.getElementById('status-bar');
  bar.textContent = msg;
  bar.className = `status-bar status-${type}`;
}

function clearStatus() {
  const bar = document.getElementById('status-bar');
  bar.className = 'status-bar hidden';
}

// ---------------------------------------------------------------------------
// Main fetch + render
// ---------------------------------------------------------------------------
async function fetchAndRender() {
  const station = document.getElementById('station-select').value;
  const hours   = document.getElementById('hours-select').value;

  clearStatus();
  showStatus('Loading buoy data…', 'info');

  // Skeleton loading state
  document.getElementById('charts-section').classList.add('loading');

  try {
    const res  = await fetch(`/api/swells?station=${encodeURIComponent(station)}&hours=${hours}`);
    const data = await res.json();

    if (!res.ok || data.error) {
      showStatus(`Error: ${data.error || res.statusText}`, 'error');
      return;
    }

    renderConditions(data.latest, data.station_name);
    renderCharts(data.swells);
    renderLegend(data.swells);
    clearStatus();

  } catch (err) {
    showStatus(`Network error: ${err.message}`, 'error');
    console.error(err);
  } finally {
    document.getElementById('charts-section').classList.remove('loading');
  }
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
function degToCompass(deg) {
  const dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE',
                 'S','SSW','SW','WSW','W','WNW','NW','NNW'];
  return dirs[Math.round(deg / 22.5) % 16];
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  fetchAndRender();

  document.getElementById('refresh-btn').addEventListener('click', fetchAndRender);
  document.getElementById('station-select').addEventListener('change', fetchAndRender);
  document.getElementById('hours-select').addEventListener('change', fetchAndRender);
});
