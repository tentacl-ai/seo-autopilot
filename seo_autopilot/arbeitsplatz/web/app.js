/* Shared API, ephemeral access token, text-only rendering of all stored content. */
"use strict";
let token = "",
  project = "",
  role = "viewer",
  current = "overview",
  generation = 0,
  busy = false,
  sessionAbort = new AbortController();
const $ = (id) => document.getElementById(id),
  content = $("content");
const sections = {
  overview: "Überblick",
  assistant: "SEO-Agent",
  context: "Projektakte",
  keywords: "Keywords & Seiten",
  research: "Recherche",
  rankings: "Google",
  ai: "KI-Sichtbarkeit",
  maps: "Local SEO",
  backlinks: "Backlinks",
  recommendations: "Maßnahmen",
  changes: "Änderungen",
  effects: "Wirkung",
  trackers: "Messaufträge",
  reports: "Berichte",
};
const labels = {
  position_current: "Position aktuell",
  position_previous: "Position Vorwoche",
  clicks_current: "Klicks aktuell",
  clicks_previous: "Klicks Vorwoche",
  change: "Veränderung",
  zeitpunkt: "Zeitpunkt",
  begruendung: "Begründung",
  urheber: "Urheber",
  git_commit: "Commit",
  begriff: "Suchbegriff",
  query: "Suchbegriff",
  woche: "Woche",
  geraet: "Gerät",
  position: "Position",
  klicks: "Klicks",
  impressionen: "Einblendungen",
  datum: "Datum",
  ki: "KI",
  frage: "Frage",
  genannt: "Nennung",
  quellen: "Quellen",
  wettbewerber: "Wettbewerber",
  modell: "Modell",
  ort: "Ort",
  platz: "Position",
  domain: "Domain",
  rang: "Graph-Rang",
  graph: "Graph",
  titel: "Empfehlung",
  seite: "Seite",
  status: "Status",
  prioritaet: "Priorität",
  aktion: "Änderung",
  fenster_tage: "Tage",
  vorher_klicks: "Klicks vorher",
  nachher_klicks: "Klicks nachher",
  urteil: "Ergebnis",
  intent: "Suchabsicht",
  url: "Zielseite",
  tags: "Themen",
  source: "Quelle",
  measurement: "Messart",
  collected_at: "Datenstand",
  text: "Beschreibung",
  target: "Zielseite",
  keywords: "Suchbegriffe",
  action: "Nächster Schritt",
  basis: "Grundlage",
  revision: "Fassung",
  title: "Titel",
  created_at: "Gespeichert",
  author: "Von",
  type: "Art",
  terms: "Begriffe / Fragen",
  schedule: "Rhythmus",
};
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
}
function button(text, fn, quiet = true) {
  const b = el("button", text, quiet ? "quiet" : "");
  b.type = "button";
  b.onclick = () => Promise.resolve(fn()).catch(showError);
  return b;
}
function showError(error) {
  $("notice").textContent = error.message || "Aktion nicht möglich.";
}
async function api(path, options = {}) {
  const r = await fetch(new URL("./api/" + path, location.href), {
    ...options,
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
      ...options.headers,
    },
    cache: "no-store",
    signal: sessionAbort.signal,
  });
  const d = await r.json();
  if (!r.ok) throw Error(d.detail || "Anfrage fehlgeschlagen.");
  return d;
}
function scope(path) {
  return encodeURIComponent(project) + "/" + path;
}
function panel(title) {
  const n = el("section", undefined, "panel");
  n.append(el("h2", title));
  content.append(n);
  return n;
}
function empty(parent, text = "Noch keine gespeicherten Daten.") {
  parent.append(el("p", text, "empty"));
}
const terms = {
  informational: "Informieren",
  commercial: "Vergleichen",
  transactional: "Anfragen / kaufen",
  navigational: "Bestimmte Seite finden",
  unknown: "Noch offen",
  improve: "Bestehende Seite verbessern",
  review_gap: "Inhaltslücke prüfen",
  completed: "Abgeschlossen",
  partial: "Teilweise gemessen",
  failed: "Fehlgeschlagen",
  queued: "In Bearbeitung",
  manual: "Manuell",
  daily: "Täglich geplant",
  weekly: "Wöchentlich geplant",
  monthly: "Monatlich geplant",
  MOBILE: "Handy",
  DESKTOP: "Computer",
  offen: "Offen",
  open: "Offen",
};
function format(value) {
  if (typeof value === "string" && terms[value]) return terms[value];
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function table(parent, rows, columns, actions) {
  if (!rows.length) {
    empty(parent);
    return;
  }
  const filter = el("input");
  filter.placeholder = "Filtern …";
  filter.setAttribute("aria-label", "Tabelle filtern");
  filter.className = "filter";
  parent.append(filter);
  const wrap = el("div", undefined, "table-wrap"),
    t = el("table"),
    head = el("tr");
  for (const col of columns) head.append(el("th", labels[col] || col));
  if (actions) head.append(el("th", "Aktion"));
  const thead = el("thead");
  thead.append(head);
  t.append(thead);
  const tbody = el("tbody");
  function draw() {
    tbody.replaceChildren();
    for (const row of rows.filter((x) =>
      JSON.stringify(x).toLowerCase().includes(filter.value.toLowerCase()),
    )) {
      const tr = el("tr");
      for (const col of columns) tr.append(el("td", format(row[col])));
      if (actions) {
        const td = el("td");
        actions(td, row);
        tr.append(td);
      }
      tbody.append(tr);
    }
  }
  filter.oninput = draw;
  draw();
  t.append(tbody);
  wrap.append(t);
  parent.append(wrap);
}
function field(form, label, type = "text", value = "") {
  const l = el("label", label),
    input = el(type === "textarea" ? "textarea" : "input");
  if (type !== "textarea") input.type = type;
  if (type === "number") input.step = "any";
  input.value = value;
  l.append(input);
  form.append(l);
  return input;
}
function select(form, label, choices, value) {
  const l = el("label", label),
    s = el("select");
  s.setAttribute("aria-label", label);
  for (const [v, text] of choices) s.append(new Option(text, v));
  if (value) s.value = value;
  l.append(s);
  form.append(l);
  return s;
}
function saveButton(form, fn) {
  const b = el("button", "Speichern");
  b.disabled = role === "viewer";
  form.append(b);
  form.onsubmit = async (e) => {
    e.preventDefault();
    b.disabled = true;
    try {
      await fn();
      $("notice").textContent = "Gespeichert.";
    } catch (err) {
      showError(err);
    } finally {
      b.disabled = role === "viewer";
    }
  };
}
async function docs(kind) {
  return api(scope("documents/" + kind));
}
async function put(kind, key, payload, revision = 0) {
  return api(scope("documents/" + kind + "/" + encodeURIComponent(key)), {
    method: "PUT",
    body: JSON.stringify({ payload, expected_revision: revision }),
  });
}
function download(name, data, type = "application/json") {
  const a = el("a"),
    url = URL.createObjectURL(new Blob([data], { type }));
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function sourceView(section) {
  if (section === "rankings") {
    const compare = await api(scope("ranking-comparison")),
      box = panel("Vergleich zur Vorwoche");
    box.append(
      el(
        "p",
        compare.week
          ? compare.week +
              " und " +
              compare.previous_week +
              " · Durchschnittsposition aus Search Console"
          : "Noch kein Wochenvergleich verfügbar.",
        "source-note",
      ),
    );
    table(box, compare.rows, [
      "begriff",
      "geraet",
      "position_current",
      "position_previous",
      "clicks_current",
      "clicks_previous",
      "change",
    ]);
  }
  const d = await api(scope("source/" + section));
  const p = panel(sections[section]);
  p.append(
    el(
      "p",
      d.status === "ok"
        ? "Gespeicherte Messungen · keine neuen Abrufe"
        : "Für diesen Bereich liegen keine lesbaren Messungen vor.",
      "source-note",
    ),
  );
  if (d.rows.length) {
    const cols = Object.keys(d.rows[0]).filter(
      (k) => !["id", "audit_id", "abgerufen_am", "erstellt_am"].includes(k),
    );
    table(p, d.rows, cols);
    p.append(
      button("JSON exportieren", () =>
        download(section + ".json", JSON.stringify(d, null, 2)),
      ),
    );
  } else empty(p);
}
async function overview() {
  const d = await api(scope("overview")),
    a = d.sections.audits.rows,
    latest = a[0] || {},
    prev = a[1] || {};
  const cards = el("div", undefined, "cards");
  for (const [name, value, note] of [
    ["Website-Stand", latest.score ?? "—", "Letzte gespeicherte Prüfung"],
    [
      "Google-Klicks",
      latest.gsc_clicks ?? "—",
      latest.gsc_clicks != null && prev.gsc_clicks != null
        ? latest.gsc_clicks === prev.gsc_clicks
          ? "gleich wie letzte Prüfung"
          : "Letzte Prüfung: " + prev.gsc_clicks
        : "Kein Vergleich verfügbar",
    ],
    [
      "Geprüfte Seiten",
      latest.total_pages ?? "—",
      "Umfang der letzten Prüfung",
    ],
    [
      "Empfehlungen",
      d.sections.recommendations.status === "ok"
        ? d.sections.recommendations.rows.filter((x) =>
            ["offen", "open"].includes(x.status),
          ).length
        : "—",
      "Offene Einträge im Auszug",
    ],
  ]) {
    const c = el("div", undefined, "card");
    c.append(
      el("span", name, "muted"),
      el("div", value, "metric"),
      el("span", note, "muted"),
    );
    cards.append(c);
  }
  content.append(cards);
  const p = panel("Der nächste sinnvolle Schritt"),
    rec = d.sections.recommendations.rows.find((row) =>
      ["offen", "open"].includes(row.status),
    );
  if (rec) {
    p.append(el("h3", rec.titel), el("p", rec.text || rec.vorschlag || ""));
    p.append(button("Maßnahmen ansehen", () => render("recommendations")));
  } else
    empty(
      p,
      "Noch keine Empfehlung gespeichert. Zuerst vorhandene Website-Prüfung ansehen.",
    );
  const q = panel("Was wissen wir über dieses Projekt?");
  const goal = d.context.find((x) => x.key === "goal");
  q.append(
    el("p", goal ? goal.payload.text : "Das Projektziel ist noch offen."),
    button("Projektakte öffnen", () => render("context")),
  );
  const s = panel("Datenlage");
  table(
    s,
    Object.entries(d.sections).map(([name, v]) => ({
      Bereich:
        sections[name] ||
        {
          audits: "Website-Prüfungen",
          issues: "Technische Befunde",
          history: "Suchhistorie",
          profiles: "Unternehmensprofile",
          changes: "Durchgeführte Änderungen",
        }[name] ||
        name,
      Status:
        {
          ok: "Verfügbar",
          missing: "Noch keine Messung",
          unavailable: "Derzeit nicht lesbar",
        }[v.status] || v.status,
      Zeilen: v.status === "ok" ? v.rows.length : null,
    })),
    ["Bereich", "Status", "Zeilen"],
  );
}
async function context() {
  const records = await docs("context"),
    names = {
      business: "Geschäft & Zielgruppe",
      goal: "Aktuelles Ziel",
      positioning: "Angebot & Positionierung",
      voice: "Sprache & Stil",
      region: "Region & Markt",
      competitors: "Wettbewerber",
      pages: "Wichtige Seiten",
    };
  for (const [key, title] of Object.entries(names)) {
    const row = records.find((x) => x.key === key),
      p = panel(title),
      f = el("form"),
      text = field(f, "Inhalt", "textarea", row?.payload.text || ""),
      source = field(
        f,
        "Grundlage / Quelle",
        "text",
        row?.payload.source || "manual",
      ),
      status = select(
        f,
        "Status",
        [
          ["inferred", "Noch zu bestätigen"],
          ["confirmed", "Bestätigt"],
        ],
        row?.payload.status || "inferred",
      );
    status.disabled = role !== "owner";
    saveButton(f, async () => {
      await put(
        "context",
        key,
        { text: text.value, source: source.value, status: status.value },
        row?.revision || 0,
      );
      await render("context");
    });
    p.append(f);
  }
}
async function keywords() {
  const records = await docs("keyword"),
    p = panel("Suchbegriffe und Zielseiten"),
    f = el("form");
  p.append(
    button("Vorhandene GSC-Begriffe übernehmen", async () => {
      await api(scope("keywords/import"), { method: "POST" });
      await render("keywords");
    }),
  );
  let key = "",
    revision = 0;
  const query = field(f, "Suchbegriff"),
    intent = select(f, "Suchabsicht", [
      ["unknown", "Noch offen"],
      ["informational", "Informieren"],
      ["commercial", "Angebote vergleichen"],
      ["transactional", "Anfragen / kaufen"],
      ["navigational", "Bestimmte Seite finden"],
    ]),
    url = field(f, "Vorhandene Zielseite (optional)", "url"),
    tags = field(f, "Themen, mit Komma getrennt");
  saveButton(f, async () => {
    await put(
      "keyword",
      key || crypto.randomUUID(),
      {
        query: query.value,
        intent: intent.value,
        url: url.value,
        tags: tags.value
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      },
      revision,
    );
    await render("keywords");
  });
  p.append(f);
  table(
    p,
    records.map((r) => ({ ...r.payload, key: r.key, revision: r.revision })),
    ["query", "intent", "url", "tags"],
    (td, r) =>
      td.append(
        button("Bearbeiten", () => {
          key = r.key;
          revision = r.revision;
          query.value = r.query;
          intent.value = r.intent;
          url.value = r.url;
          tags.value = r.tags.join(", ");
          query.focus();
        }),
      ),
  );
  const plan = await api(scope("content-plan")),
    q = panel("Seitenplan");
  q.append(
    el(
      "p",
      "Gruppiert nach deiner Zuordnung und Suchabsicht. Neue Seiten werden zunächst vorgeschlagen.",
      "source-note",
    ),
  );
  table(q, plan, ["target", "intent", "keywords", "action"]);
}
async function research() {
  const p = panel("Gezielt recherchieren");
  p.append(
    el(
      "p",
      "Zusätzliche Wettbewerbs- und Suchdaten werden nur bei hinterlegter Datenfreigabe abgerufen. Gespeicherte Ergebnisse werden zuerst geprüft.",
      "muted",
    ),
  );
  const f = el("form"),
    feature = select(f, "Recherche", [
      ["keyword_ideas", "Keyword-Ideen"],
      ["domain_keywords", "Suchbegriffe einer Domain"],
      ["domain_overview", "Domain-Überblick"],
      ["backlinks", "Einzelne Backlinks"],
      ["referring_domains", "Verweisende Domains"],
      ["top_pages", "Stärkste verlinkte Seiten"],
      ["business_profile", "Unternehmensprofil"],
      ["reviews", "Bewertungen (bis 10)"],
    ]),
    target = field(f, "Domain oder Unternehmensname"),
    keyword = field(f, "Startbegriff"),
    loc = field(f, "Markt-Code", "number", "2276"),
    lang = field(f, "Sprache", "text", "de"),
    b = el("button", "Recherche vorbereiten");
  b.disabled = role !== "owner";
  f.append(b);
  const output = el("pre");
  f.onsubmit = async (e) => {
    e.preventDefault();
    try {
      const result = await api(scope("research/" + feature.value), {
        method: "POST",
        body: JSON.stringify({
          parameters: {
            target: target.value,
            keyword: keyword.value,
            location_code: Number(loc.value),
            language_code: lang.value,
          },
          request_id: crypto.randomUUID(),
          confirmed: await approveResearch(feature.value),
        }),
      });
      output.textContent = JSON.stringify(result, null, 2);
    } catch (err) {
      showError(err);
    }
  };
  p.append(f, output);
  const log = await api(scope("research"));
  table(
    p,
    log.map((r) => ({
      request_id: r.request_id,
      status: r.status,
      created_at: r.created_at,
      feature: r.payload?.feature || "Messung",
      payload: r.payload,
    })),
    ["feature", "status", "created_at"],
    (td, r) =>
      td.append(
        button("Ergebnis", () => {
          output.textContent = JSON.stringify(r.payload, null, 2);
        }),
        ...(r.feature === "reviews" && r.status === "queued"
          ? [
              button("Status abrufen", async () => {
                output.textContent = JSON.stringify(
                  await api(scope("reviews/" + r.request_id + "/result"), {
                    method: "POST",
                  }),
                  null,
                  2,
                );
              }),
            ]
          : []),
      ),
  );
}
async function trackers() {
  const records = await docs("tracker"),
    p = panel("Neue Messung vorbereiten"),
    f = el("form"),
    type = select(f, "Messart", [
      ["serp", "Google-Ergebnisposition"],
      ["local", "Lokales Rankingraster"],
      ["ai", "KI-Antworten"],
    ]),
    terms = field(f, "Suchbegriffe oder Fragen, eine pro Zeile", "textarea"),
    schedule = select(f, "Geplanter Rhythmus", [
      ["manual", "Manuell"],
      ["daily", "Täglich"],
      ["weekly", "Wöchentlich"],
      ["monthly", "Monatlich"],
    ]),
    lat = field(f, "Breitengrad (nur Local)", "number", "47.75"),
    lng = field(f, "Längengrad (nur Local)", "number", "9.17"),
    place = field(f, "Google-Place-ID (nur Local)"),
    size = select(f, "Raster", [
      ["3", "3 × 3"],
      ["5", "5 × 5"],
      ["7", "7 × 7"],
    ]),
    engines = field(
      f,
      "KI-Auswahl (chatgpt, gemini, claude)",
      "text",
      "chatgpt",
    );
  saveButton(f, async () => {
    await put("tracker", crypto.randomUUID(), {
      type: type.value,
      schedule: schedule.value,
      terms: terms.value
        .split("\n")
        .map((t) => t.trim())
        .filter(Boolean),
      lat: Number(lat.value),
      lng: Number(lng.value),
      place_id: place.value,
      size: Number(size.value),
      engines: engines.value.split(",").map((v) => v.trim()),
    });
    await render("trackers");
  });
  p.append(f);
  const q = panel("Vorbereitete Messaufträge");
  q.append(
    el(
      "p",
      "Ein Rhythmus wird zunächst nur vorbereitet. Regelmäßige Abrufe benötigen eine gesonderte Betriebsfreigabe.",
      "source-note",
    ),
  );
  table(
    q,
    records.map((r) => ({ ...r.payload, key: r.key })),
    ["type", "terms", "schedule"],
    (td, r) => {
      td.append(
        button("Kosten & Start", async () => {
          const e = await api(scope("trackers/" + r.key + "/estimate"));
          if (!e.enabled) {
            showError(
              Error(
                "Datenabrufe sind ausgeschaltet. Der Auftrag bleibt vorbereitet.",
              ),
            );
            return;
          }
          if (
            confirm(
              e.calls +
                " Abrufe, reserviert bis " +
                (e.reserved_microusd / 1e6).toFixed(4) +
                " USD als Planreserve. Providerpreise können abweichen. Jetzt einmal ausführen?",
            )
          ) {
            await api(scope("trackers/" + r.key + "/run"), {
              method: "POST",
              body: JSON.stringify({
                request_id: crypto.randomUUID(),
                confirmed: true,
              }),
            });
            await render("trackers");
          }
        }),
      );
    },
  );
  const observations = await docs("observation");
  for (const o of observations) {
    const box = panel("Messung vom " + o.created_at),
      d = o.payload;
    box.append(
      el(
        "p",
        d.successful + " von " + d.attempted + " erfolgreich · " + d.status,
        "status",
      ),
    );
    if (d.measurement === "local") {
      const points = d.observations,
        extent = Math.sqrt(points.length);
      if (Number.isInteger(extent)) {
        const raster = el("div", undefined, "grid");
        raster.classList.add("grid" + extent);
        for (const row of points) {
          const rank = row.value?.position ?? null;
          const cell = el(
            "div",
            row.status !== "ok" ? "offen" : (rank ?? "> 10"),
            "point " + (rank && rank <= 3 ? "good" : "bad"),
          );
          cell.title = row.term + " · " + JSON.stringify(row.variant);
          raster.append(cell);
        }
        box.append(raster);
      }
    }
    const detail = el("details"),
      summary = el("summary", "Messdaten und Quellen");
    detail.append(summary, el("pre", JSON.stringify(d, null, 2)));
    box.append(detail);
  }
}
async function reports() {
  const p = panel("Privater Berichtsbestand");
  p.append(
    el(
      "p",
      "Berichte bleiben im Projekt. Jede Änderung erzeugt eine neue Fassung.",
      "muted",
    ),
    button("Bericht aus Messdaten erstellen", async () => {
      await api(scope("reports"), { method: "POST" });
      await render("reports");
    }),
  );
  const records = await docs("report");
  table(
    p,
    records.map((r) => ({
      ...r.payload,
      key: r.key,
      revision: r.revision,
      created_at: r.created_at,
    })),
    ["title", "revision", "created_at"],
    (td, r) => td.append(button("Öffnen", () => openReport(r))),
  );
  const q = panel("Berichtsvorlage"),
    f = el("form"),
    title = field(f, "Name"),
    text = field(f, "Aufbau und Hinweise", "textarea");
  saveButton(f, async () => {
    await put("template", crypto.randomUUID(), {
      title: title.value,
      text: text.value,
    });
    await render("reports");
  });
  q.append(f);
  const templates = await docs("template");
  table(
    q,
    templates.map((r) => ({ ...r.payload, key: r.key, revision: r.revision })),
    ["title", "text", "revision"],
    (td, r) =>
      td.append(
        button("Bericht erstellen", async () => {
          await api(
            scope("reports?template_key=" + encodeURIComponent(r.key)),
            { method: "POST" },
          );
          await render("reports");
        }),
      ),
  );
}
async function render(view) {
  if (busy) return;
  busy = true;
  $("project").disabled = true;
  for (const b of $("nav").children) b.disabled = true;
  const myGeneration = ++generation;
  current = view;
  $("title").textContent = sections[view];
  $("notice").textContent = "";
  content.replaceChildren(el("p", "Lade gespeicherte Daten …", "muted"));
  for (const b of $("nav").children)
    b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false");
  content.replaceChildren();
  try {
    await (
      { overview, context, keywords, research, trackers, reports, assistant }[
        view
      ] || (() => sourceView(view))
    )();
  } catch (err) {
    if (myGeneration === generation) showError(err);
  } finally {
    busy = false;
    $("project").disabled = false;
    for (const b of $("nav").children) b.disabled = false;
  }
}
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  sessionAbort = new AbortController();
  token = $("token").value;
  $("token").value = "";
  try {
    const d = await api("projects");
    role = d.role;
    $("role").textContent = {
      owner: "Betreiber",
      editor: "Bearbeiten",
      viewer: "Lesen",
    }[role];
    $("project").replaceChildren();
    for (const p of d.projects) $("project").append(new Option(p.name, p.id));
    if (!d.projects.length)
      throw Error("Für diesen Zugang ist noch kein Projekt freigegeben.");
    project = d.projects[0].id;
    $("project-domain").textContent = d.projects[0].domain;
    $("project").onchange = () => {
      project = $("project").value;
      $("project-domain").textContent = d.projects.find(
        (p) => p.id === project,
      ).domain;
      render(current);
    };
    $("login").hidden = true;
    $("workspace").hidden = false;
    await render("overview");
  } catch (err) {
    token = "";
    const msg = el("p", err.message, "muted");
    $("login-form").append(msg);
  }
};
for (const [view, title] of Object.entries(sections)) {
  const b = button(title, () => render(view));
  b.dataset.view = view;
  $("nav").append(b);
}
$("logout").onclick = () => {
  sessionAbort.abort();
  generation++;
  token = "";
  project = "";
  content.replaceChildren();
  $("project").replaceChildren();
  $("workspace").hidden = true;
  $("login").hidden = false;
};

fetch(new URL("./mode", location.href))
  .then((r) => r.json())
  .then((d) => {
    $("demo-note").hidden = !d.demo;
  })
  .catch(() => {});

async function approveResearch(feature) {
  const e = await api(scope("research/" + feature + "/estimate"));
  if (!e.enabled) return false;
  return confirm(
    "Neue Daten können bis " +
      (e.reserved_microusd / 1e6).toFixed(4) +
      " USD als Planreserve belegen. Providerpreise können abweichen. Jetzt einmal abrufen?",
  );
}

async function openReport(r) {
  content.querySelector(".report")?.remove();
  const box = el("article", undefined, "report"),
    title = el("h2", r.title),
    body = el("pre", r.text);
  const actions = el("div", undefined, "actions");
  const versions = el("select");
  versions.setAttribute("aria-label", "Berichtsfassung");
  for (let i = r.revision; i >= Math.max(1, r.revision - 99); i--)
    versions.append(new Option("Fassung " + i, i));
  versions.onchange = async () => {
    try {
      const d = await api(
        scope("documents/report/" + r.key + "?revision=" + versions.value),
      );
      title.textContent = d.payload.title;
      body.textContent = d.payload.text;
    } catch (e) {
      showError(e);
    }
  };
  actions.append(
    versions,
    button("Drucken / PDF", () => window.print()),
    button("Text herunterladen", () =>
      download("seo-bericht.txt", body.textContent, "text/plain"),
    ),
  );
  box.append(title, actions, body);
  const details = el("details"),
    summary = el("summary", "Bericht bearbeiten"),
    form = el("form");
  const name = field(form, "Berichtstitel", "text", r.title),
    text = field(form, "Berichtstext", "textarea", r.text);
  saveButton(form, async () => {
    await put(
      "report",
      r.key,
      { title: name.value, text: text.value },
      r.revision,
    );
    await render("reports");
  });
  details.append(summary, form);
  box.append(details);
  content.append(box);
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function assistant() {
  const p = panel("Was möchtest du über dieses Projekt wissen?");
  p.append(
    el(
      "p",
      "Der Agent kann gespeicherte Daten erklären. Er verändert keine Website und startet keine Recherche.",
      "muted",
    ),
  );
  const f = el("form"),
    question = field(f, "Deine Frage", "textarea"),
    b = el("button", "Fragen"),
    answer = el("pre");
  b.disabled = role === "viewer";
  f.append(b);
  f.onsubmit = async (e) => {
    e.preventDefault();
    b.disabled = true;
    try {
      const d = await api(scope("ask"), {
        method: "POST",
        body: JSON.stringify({ question: question.value }),
      });
      answer.textContent = d.answer + "\n\n" + d.notice;
    } catch (error) {
      showError(error);
    } finally {
      b.disabled = role === "viewer";
    }
  };
  p.append(f, answer);
}
