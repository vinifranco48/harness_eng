/** Build deterministic synthetic sales fixtures and the matching Excel workbook.
 * Run `node scripts/build_demo_workbook.mjs --data-only` for CSV/JSON only.
 * Excel authoring uses the external Codex artifact runtime, never app dependencies.
 */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dataDir = path.join(root, 'apps/web/api/data');
const outputDir = path.join(root, 'outputs/harness-analytics-demo');
const headers = ['mes', 'regiao', 'produto', 'unidades', 'receita', 'custo'];
const regions = ['Norte', 'Nordeste', 'Sudeste', 'Sul'];
const products = ['Software', 'Consultoria', 'Suporte'];
const priceCents = [24990, 79950, 11990];
const costBasisPoints = [3800, 6200, 4500];
const seasonality = [0, 3, 5, 8, 7, 10, 14, 12, 16, 20, 24, 30];
const rows = [];
for (let month = 1; month <= 12; month += 1) {
  for (const [regionIndex, regiao] of regions.entries()) {
    for (const [productIndex, produto] of products.entries()) {
      const unidades = 40 + month * 3 + regionIndex * 7 + productIndex * 11 + seasonality[month - 1];
      const receitaCents = unidades * priceCents[productIndex];
      const custoCents = Math.round(receitaCents * costBasisPoints[productIndex] / 10000);
      rows.push({ mes: `2025-${String(month).padStart(2, '0')}-01`, regiao, produto, unidades,
        receita: receitaCents / 100, custo: custoCents / 100 });
    }
  }
}

// Integer cents avoid floating-point drift in the checked-in expected values.
function summarize(records) {
  const receitaCents = records.reduce((sum, row) => sum + Math.round(row.receita * 100), 0);
  const custoCents = records.reduce((sum, row) => sum + Math.round(row.custo * 100), 0);
  return { row_count: records.length, unidades: records.reduce((sum, row) => sum + row.unidades, 0),
    receita: receitaCents / 100, custo: custoCents / 100, lucro: (receitaCents - custoCents) / 100 };
}
function groupBy(field) {
  return Object.fromEntries([...new Set(rows.map(row => row[field]))].map(value =>
    [value, summarize(rows.filter(row => row[field] === value))]));
}
const totals = { description: 'Dados inteiramente fictícios, gerados para testes do Harness Eng.',
  period: { start: '2025-01-01', end: '2025-12-01' }, currency: 'BRL',
  ...summarize(rows), by_month: groupBy('mes'), by_region: groupBy('regiao'), by_product: groupBy('produto') };
await fs.mkdir(dataDir, { recursive: true });
await fs.writeFile(path.join(dataDir, 'vendas_demo.csv'), [headers.join(','),
  ...rows.map(row => headers.map(header => ['receita', 'custo'].includes(header)
    ? row[header].toFixed(2) : row[header]).join(','))].join('\n') + '\n');
await fs.writeFile(path.join(dataDir, 'vendas_demo_totals.json'), JSON.stringify(totals, null, 2) + '\n');
console.log(JSON.stringify({ stage: 'fixtures', ...summarize(rows) }));
if (process.argv.includes('--data-only')) process.exit(0);

const dependencies = process.env.ARTIFACT_DEPENDENCIES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node');
const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'harness-workbook-'));
await fs.symlink(path.join(dependencies, 'node_modules'), path.join(scratch, 'node_modules'), 'dir');
const require = createRequire(path.join(scratch, 'package.json'));
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const workbook = Workbook.create();
const data = workbook.worksheets.add('Dados');
const about = workbook.worksheets.add('Sobre');
for (const sheet of [data, about]) sheet.showGridLines = false;
data.tabColor = '#467052';
about.tabColor = '#A2A69B';
const last = rows.length + 1;
data.getRange(`A1:F${last}`).values = [headers, ...rows.map(row => headers.map(header =>
  header === 'mes' ? new Date(`${row.mes}T00:00:00Z`) : row[header]))];
data.getRange(`A1:F${last}`).format.font = { name: 'Arial', size: 11, color: '#272925' };
data.getRange(`A1:F${last}`).format.verticalAlignment = 'center';
data.getRange(`A1:F${last}`).format.rowHeight = 23;
data.getRange(`A1:F${last}`).format.columnWidth = 19;
data.getRange(`A2:A${last}`).setNumberFormat('yyyy-mm-dd');
data.getRange(`D2:D${last}`).setNumberFormat('#,##0');
data.getRange(`E2:F${last}`).setNumberFormat('"R$ "#,##0.00');
data.tables.add(`A1:F${last}`, true, 'VendasDemonstrativas');
data.getRange('A1:F1').format = { fill: '#30332D', font: { name: 'Arial', size: 11, bold: true, color: '#FFFFFF' },
  rowHeight: 30, horizontalAlignment: 'center', verticalAlignment: 'center',
  borders: { insideVertical: { style: 'thin', color: '#FFFFFF' } } };
data.getRange(`A2:A${last}`).format.horizontalAlignment = 'center';
data.getRange(`A2:F${last}`).format.borders = { insideHorizontal: { style: 'thin', color: '#E2E4DD' } };
for (let row = 2; row <= last; row += 1) {
  data.getRange(`A${row}:F${row}`).format.fill = row % 2 === 0 ? '#FFFFFF' : '#F4F4F2';
}
data.freezePanes.freezeRows(1);
about.getRange('A1:D19').format.font = { name: 'Arial', size: 11, color: '#272925' };
about.getRange('A1:D19').format.rowHeight = 25;
about.getRange('A1:A19').format.columnWidth = 24;
about.getRange('B1:D19').format.columnWidth = 23;
about.getRange('A2').values = [['Vendas demonstrativas']];
about.getRange('A2').format.font = { name: 'Arial', size: 16, bold: true, color: '#272925' };
about.getRange('A3').values = [['Dados fictícios para desenvolver e testar análises e gráficos.']];
about.getRange('A3').format.font = { name: 'Arial', size: 11, italic: true, color: '#6B6E65' };
about.getRange('A5:B8').values = [
  ['Período', 'Janeiro a dezembro de 2025'],
  ['Unidade por registro', 'Mês, região e produto'],
  ['Moeda', 'Real brasileiro (BRL)'],
  ['Origem', 'Geração sintética e determinística'],
];
about.getRange('A10:B10').values = [['Campo', 'Significado']];
about.getRange('A10:B10').format = { fill: '#ECEEE8', font: { name: 'Arial', size: 11, bold: true, color: '#272925' } };
about.getRange('A11:B16').values = [
  ['mes', 'Primeiro dia do mês de referência.'],
  ['regiao', 'Norte, Nordeste, Sudeste ou Sul.'],
  ['produto', 'Software, Consultoria ou Suporte.'],
  ['unidades', 'Quantidade de unidades vendidas.'],
  ['receita', 'Receita do registro, em reais.'],
  ['custo', 'Custo do registro, em reais.'],
];
about.getRange('A18').values = [['Importação: use a aba Dados, com os cabeçalhos na primeira linha.']];
about.getRange('A19').values = [['A planilha e o CSV contêm os mesmos 144 registros.']];
workbook.recalculate();
console.log((await workbook.inspect({kind: 'table', range: 'Dados!A1:F4', include: 'values,formulas', tableMaxRows: 4, tableMaxCols: 6, maxChars: 1800})).ndjson);
console.log((await workbook.inspect({kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!', options: {useRegex: true, maxResults: 20}, summary: 'Final error scan', maxChars: 1200})).ndjson);
await fs.mkdir(outputDir, {recursive: true});
for (const [sheetName, range, filename] of [['Dados', 'A1:F15', 'dados-preview.png'], ['Sobre', 'A1:D20', 'sobre-preview.png']]) {
  const preview = await workbook.render({sheetName, range, scale: 1.5, format: 'png'});
  await fs.writeFile(path.join(outputDir, filename), new Uint8Array(await preview.arrayBuffer()));
}
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(path.join(outputDir, 'vendas_demo.xlsx'));
console.log(JSON.stringify({stage: 'workbook', path: path.join(outputDir, 'vendas_demo.xlsx')}));
