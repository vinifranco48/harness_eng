/** Validate saved charts with the same pinned Vega browser bundles as the UI.
 * Usage: node scripts/validate_vega.mjs charts.json [vendas_demo_totals.json]
 * Input: an array of chart records, or {charts: [...]}. Optional per-chart
 * validation.expected entries contain {key: [x, optionalColor], value: number}.
 * No network requests, DOM, package install or database access are needed.
 */
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";
import vm from "node:vm";

process.env.TZ ||= "America/Belem";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function loadRuntime() {
  const vendor = resolve(root, "apps/web/app/vendor");
  const manifest = JSON.parse(await readFile(resolve(vendor, "manifest.json"), "utf8"));
  const context = vm.createContext({console, structuredClone, setTimeout, clearTimeout});
  for (const name of ["vega", "vega-lite"]) {
    const item = manifest.find(entry => entry.name === name);
    assert.ok(item, `Missing local ${name} bundle`);
    const source = await readFile(resolve(vendor, item.file));
    assert.equal(createHash("sha256").update(source).digest("hex"), item.sha256, `Changed ${name} bundle`);
    vm.runInContext(source.toString("utf8"), context, {filename: item.file, timeout: 10_000});
  }
  return {vega: context.vega, vegaLite: context.vegaLite};
}

function keyFor(values, xType) {
  return JSON.stringify(values.map((value, index) => index === 0 && xType === "temporal" ? new Date(value).toISOString() : value));
}

function closeTo(actual, expected, label) {
  assert.ok(Number.isFinite(actual), `${label}: non-finite aggregate`);
  const tolerance = Math.max(1e-7, Math.abs(expected) * 1e-12);
  assert.ok(Math.abs(actual - expected) <= tolerance, `${label}: expected ${expected}, received ${actual}`);
}

function markList(marks = []) {
  return marks.flatMap(mark => [mark, ...markList(mark.marks)]);
}

function fixtureExpectations(spec, totals) {
  const {x, y, color} = spec.encoding;
  const section = {regiao: "by_region", mes: "by_month", produto: "by_product"}[x.field];
  if (color || !section || !["sum", "mean", "count"].includes(y.aggregate)) return null;
  const groups = totals[section];
  assert.ok(groups, `Missing ${section} fixture`);
  return Object.entries(groups).map(([key, summary]) => {
    const value = y.aggregate === "count" ? summary.row_count : summary[y.field];
    assert.ok(Number.isFinite(value), `Missing fixture metric ${y.field}`);
    return {key: [key], value: y.aggregate === "mean" ? value / summary.row_count : value};
  });
}

export async function validateCharts(charts, totals = null) {
  assert.ok(Array.isArray(charts) && charts.length, "Provide at least one chart");
  const {vega, vegaLite} = await loadRuntime();
  const reports = [];
  for (const chart of charts) {
    const label = chart.title || chart.id || "chart";
    const spec = structuredClone(chart.spec);
    assert.ok(Array.isArray(spec?.data?.values), `${label}: expected inline data`);
    assert.ok(["bar", "line", "area", "point"].includes(spec.mark.type), `${label}: unsupported mark`);
    const {x, y, color} = spec.encoding;
    spec.width = 640; // "container" has no DOM container in a headless runtime.
    const compiled = vegaLite.compile(spec).spec;
    const aggregateData = compiled.data.find(data => data.transform?.some(transform => transform.type === "aggregate"));
    assert.ok(aggregateData, `${label}: no Vega aggregate transform`);
    const transform = aggregateData.transform.find(item => item.type === "aggregate");
    const metricIndex = transform.ops.findIndex((operation, index) => operation === y.aggregate && (operation === "count" || transform.fields[index] === y.field));
    assert.ok(metricIndex >= 0, `${label}: aggregate differs from saved chart`);
    const metric = transform.as[metricIndex];
    const xScale = compiled.scales.find(scale => scale.name === "x");
    if (x.type === "temporal") {
      assert.equal(xScale.type, "utc", `${label}: temporal scale must be UTC`);
      const tooltips = markList(compiled.marks).map(mark => mark.encode?.update?.tooltip?.signal || "");
      assert.ok(tooltips.some(signal => signal.includes("utcFormat(")), `${label}: tooltip must use utcFormat`);
      assert.ok(tooltips.every(signal => !signal.includes("timeFormat(")), `${label}: local-time tooltip shifts calendar dates`);
    }
    const view = new vega.View(vega.parse(compiled), {
      renderer: "none",
      loader: {load: async () => { throw new Error("External resources are not permitted during chart validation"); }},
    });
    try {
      await view.runAsync();
      const data = view.data(aggregateData.name);
      const values = data.map(row => ({key: [row[x.field], ...(color ? [row[color.field]] : [])], value: row[metric]}));
      const actual = new Map(values.map(row => [keyFor(row.key, x.type), row.value]));
      assert.equal(actual.size, values.length, `${label}: duplicate aggregate groups`);
      const expectedSets = [chart.validation?.expected, totals ? fixtureExpectations(spec, totals) : null].filter(Boolean);
      for (const expected of expectedSets) {
        assert.equal(actual.size, expected.length, `${label}: missing or extra groups`);
        for (const row of expected) {
          const key = keyFor(row.key, x.type);
          assert.ok(actual.has(key), `${label}: missing group ${key}`);
          closeTo(actual.get(key), row.value, `${label} ${key}`);
        }
      }
      for (const row of values) assert.ok(Number.isFinite(row.value), `${label}: non-finite result`);
      const svg = await view.toSVG();
      assert.ok(svg.includes("<svg"), `${label}: SVG was not rendered`);
      assert.ok(!/\b(?:x|y|width|height|d|transform)="[^"]*\b(?:NaN|Infinity)\b/.test(svg), `${label}: invalid SVG coordinates`);
      reports.push({title: label, mark: spec.mark.type, aggregate: y.aggregate, groups: actual.size,
        utc: x.type === "temporal", checkedValues: expectedSets.length > 0, renderedSVG: true});
    } finally {
      view.finalize();
    }
  }
  return {timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, charts: reports,
    count: reports.length, vega: vega.version, vegaLite: vegaLite.version};
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    assert.ok(process.argv[2], "Usage: node scripts/validate_vega.mjs charts.json [vendas_demo_totals.json]");
    const input = JSON.parse(await readFile(process.argv[2], "utf8"));
    const totals = process.argv[3] ? JSON.parse(await readFile(process.argv[3], "utf8")) : null;
    console.log(JSON.stringify(await validateCharts(Array.isArray(input) ? input : input.charts, totals)));
  } catch (error) {
    console.error(`Vega validation failed: ${error.message}`);
    process.exitCode = 1;
  }
}
