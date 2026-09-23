// SenseTrack dashboard. Plain JS against the same-origin API — no build step.

const REFRESH_MS = 15000;

const el = (id) => document.getElementById(id);
const state = { label: '', changedOnly: false };

const SUGGESTIONS = [
  'When was my laptop last detected?',
  'What objects were present yesterday?',
  'When was my phone last seen?',
];

// --- formatting ---------------------------------------------------------

function timeLabel(iso) {
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (sameDay) return time;

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`;

  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`;
}

function relative(iso) {
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// --- rendering ----------------------------------------------------------

function renderStatus(ok, count) {
  el('status-dot').className =
    `h-2 w-2 rounded-full ${ok ? 'bg-emerald-500' : 'bg-rose-500'}`;
  el('status-text').textContent = ok ? `${count} events` : 'backend unreachable';
  el('status-text').className = ok ? 'text-slate-300' : 'text-rose-400';
}

function renderObjects(objects) {
  const box = el('objects');
  if (!objects.length) {
    box.innerHTML = '<span class="text-slate-500">nothing recorded yet</span>';
    return;
  }

  box.innerHTML = objects
    .map(
      (o) => `
      <button data-label="${o.label}"
        class="obj-chip rounded-full bg-slate-900 px-3 py-1.5 ring-1 ring-slate-800 hover:ring-sky-600">
        <span class="font-medium text-slate-200">${o.label}</span>
        <span class="ml-1.5 text-xs text-slate-500">${relative(o.last_seen)}</span>
      </button>`
    )
    .join('');

  // Clicking a chip filters the timeline to that object.
  box.querySelectorAll('.obj-chip').forEach((btn) =>
    btn.addEventListener('click', () => {
      state.label = btn.dataset.label === state.label ? '' : btn.dataset.label;
      el('filter-label').value = state.label;
      loadTimeline();
    })
  );

  // Keep the dropdown in sync with whatever labels actually exist.
  const select = el('filter-label');
  const current = select.value;
  select.innerHTML =
    '<option value="">All objects</option>' +
    objects.map((o) => `<option value="${o.label}">${o.label}</option>`).join('');
  select.value = current;
}

function renderTimeline(events) {
  const box = el('timeline');
  el('empty').classList.toggle('hidden', events.length > 0);

  box.innerHTML = events
    .map((e) => {
      const chips = e.object_labels.length
        ? e.object_labels
            .map(
              (l) =>
                `<span class="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300">${l}</span>`
            )
            .join(' ')
        : '<span class="text-xs italic text-slate-600">nothing detected</span>';

      const thumb = e.image_url
        ? `<img src="${e.image_url}" alt="" loading="lazy"
              class="h-14 w-20 shrink-0 rounded object-cover ring-1 ring-slate-800">`
        : `<div class="flex h-14 w-20 shrink-0 items-center justify-center rounded bg-slate-900 text-[10px] text-slate-600 ring-1 ring-slate-800">no image</div>`;

      // `changed` marks a transition vs the previous cycle — the dedupe signal,
      // kept as metadata rather than used to suppress the event.
      const marker = e.changed
        ? '<span class="rounded bg-sky-950 px-1.5 py-0.5 text-[10px] font-medium text-sky-300 ring-1 ring-sky-900">changed</span>'
        : '';

      const seeded = e.source === 'seed'
        ? '<span class="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">seeded</span>'
        : '';

      return `
        <div class="slide-in flex items-center gap-3 rounded-lg bg-slate-900 p-2.5 ring-1 ring-slate-800">
          ${thumb}
          <div class="min-w-0 flex-1">
            <div class="mb-1 flex flex-wrap items-center gap-1.5">
              <span class="text-sm font-medium text-slate-200">${timeLabel(e.ts)}</span>
              ${marker} ${seeded}
              ${e.motion_since_last ? '<span class="text-[10px] text-amber-500">motion</span>' : ''}
            </div>
            <div class="flex flex-wrap gap-1">${chips}</div>
          </div>
        </div>`;
    })
    .join('');
}

// --- data ---------------------------------------------------------------

async function loadHealth() {
  try {
    const r = await fetch('/health');
    const d = await r.json();
    renderStatus(true, d.events);
  } catch {
    renderStatus(false, 0);
  }
}

async function loadObjects() {
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const r = await fetch(`/api/objects?start=${encodeURIComponent(since)}`);
  renderObjects(await r.json());
}

async function loadTimeline() {
  const params = new URLSearchParams({ limit: '60' });
  if (state.label) params.set('label', state.label);
  if (state.changedOnly) params.set('changed_only', 'true');

  const r = await fetch(`/api/events?${params}`);
  renderTimeline(await r.json());
}

async function ask(question) {
  const box = el('answer');
  box.classList.remove('hidden');
  box.innerHTML = '<span class="text-slate-500">thinking…</span>';

  try {
    const r = await fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });

    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      box.innerHTML = `<span class="text-rose-400">${err.detail || `error ${r.status}`}</span>`;
      return;
    }

    const d = await r.json();
    // Showing the intent makes the retrieval step inspectable during a demo.
    box.innerHTML = `
      <p class="text-slate-100">${d.answer}</p>
      <p class="mt-2 text-xs text-slate-500">
        interpreted as <code class="text-slate-400">${d.intent.query_type}</code>${
          d.intent.object_label ? ` · <code class="text-slate-400">${d.intent.object_label}</code>` : ''
        } · ${d.events_used} record${d.events_used === 1 ? '' : 's'} retrieved
      </p>`;
  } catch (e) {
    box.innerHTML = `<span class="text-rose-400">request failed: ${e.message}</span>`;
  }
}

// --- wiring -------------------------------------------------------------

el('suggestions').innerHTML = SUGGESTIONS.map(
  (s) =>
    `<button class="suggestion rounded-full bg-slate-950 px-2.5 py-1 text-xs text-slate-400 ring-1 ring-slate-800 hover:text-slate-200 hover:ring-slate-700">${s}</button>`
).join('');

el('suggestions').querySelectorAll('.suggestion').forEach((btn) =>
  btn.addEventListener('click', () => {
    el('question').value = btn.textContent;
    ask(btn.textContent);
  })
);

el('ask-btn').addEventListener('click', () => {
  const q = el('question').value.trim();
  if (q) ask(q);
});

el('question').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') el('ask-btn').click();
});

el('filter-label').addEventListener('change', (e) => {
  state.label = e.target.value;
  loadTimeline();
});

el('filter-changed').addEventListener('change', (e) => {
  state.changedOnly = e.target.checked;
  loadTimeline();
});

function refresh() {
  loadHealth();
  loadObjects().catch(() => {});
  loadTimeline().catch(() => {});
}

refresh();
setInterval(refresh, REFRESH_MS);
