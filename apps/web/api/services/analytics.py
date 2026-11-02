"""Import bounded tabular data and build validated Vega-Lite charts."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field

from ..adapter.sqlite.analytics_repository import AnalyticsRepository
from ..domain.analysis import AnalysisNotFound, AnalysisStorageError, AnalysisValidationError
from .analysis import AnalysisService

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 50
MAX_CELL_CHARS = 2000
MAX_XLSX_EXPANDED_BYTES = 30 * 1024 * 1024


class ChartCreateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    dataset_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=120)
    mark: Literal["bar", "line", "point", "area"] = "bar"
    x: str = Field(min_length=1, max_length=80)
    y: str = Field(min_length=1, max_length=80)
    aggregate: Literal["sum", "mean", "count", "min", "max"] = "sum"
    color: str | None = Field(default=None, min_length=1, max_length=80)


def _id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise AnalysisValidationError("Informe um identificador UUID válido.") from None


def _text(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise AnalysisValidationError(f"{label} deve ter entre 1 e {maximum} caracteres.")
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise AnalysisValidationError(f"{label} contém texto inválido.") from None
    if any(ord(character) < 32 for character in value):
        raise AnalysisValidationError(f"{label} contém caracteres de controle.")
    return value


def _cell(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or abs(value) > 1e15:
            raise AnalysisValidationError("Os números devem ser finitos e estar entre -10¹⁵ e 10¹⁵.")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if not isinstance(value, str):
        raise AnalysisValidationError("A planilha contém um tipo de célula não suportado.")
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_CELL_CHARS:
        raise AnalysisValidationError(f"Cada célula deve ter no máximo {MAX_CELL_CHARS} caracteres.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise AnalysisValidationError("A planilha contém texto inválido.") from None
    if value.lower() in {"nan", "inf", "infinity", "+inf", "-inf", "+infinity", "-infinity"}:
        raise AnalysisValidationError("A planilha contém um número não finito.")
    return value


def _numeric(value):
    if isinstance(value, (int, float)):
        return value
    # Preserve identifiers such as 00123. CSV decimal commas are supported too.
    if isinstance(value, str) and re.fullmatch(r"[+-]?(?:0|[1-9]\d*)(?:[.,]\d+)?", value):
        return _cell(float(value.replace(",", ".")))
    return None


def _temporal(value) -> bool:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:T|$)", value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _utc_temporal(value: str) -> str:
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Spreadsheets have no timezone. Interpret their calendar values in UTC,
    # consistently with the ISO date-only values imported from CSV.
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if value.hour == value.minute == value.second == value.microsecond == 0:
        return value.date().isoformat()
    return value.isoformat().replace("+00:00", "Z")


def _normalize(table) -> tuple[list[dict], list[dict]]:
    iterator = iter(table)
    try:
        header = list(next(iterator))
    except StopIteration:
        raise AnalysisValidationError("O arquivo está vazio.") from None
    if not 1 <= len(header) <= MAX_COLUMNS:
        raise AnalysisValidationError(f"Use entre 1 e {MAX_COLUMNS} colunas.")
    headers = [_text(value, "O nome de cada coluna", 80) for value in header]
    if len(set(headers)) != len(headers):
        raise AnalysisValidationError("Os nomes das colunas não podem se repetir.")
    if any(re.search(r"[.\[\]\\]", key) or key in {"__proto__", "constructor", "prototype"} for key in headers):
        raise AnalysisValidationError("Use nomes de colunas sem pontos, colchetes ou barras invertidas.")
    values = []
    for index, raw in enumerate(iterator, start=2):
        if index > MAX_ROWS + 1:
            raise AnalysisValidationError(f"Importe no máximo {MAX_ROWS} linhas de dados.")
        row = list(raw)
        if len(row) != len(headers):
            raise AnalysisValidationError(f"A linha {index} não tem a mesma quantidade de colunas do cabeçalho.")
        normalized = [_cell(value) for value in row]
        if any(value is not None for value in normalized):
            values.append(normalized)
    if not values:
        raise AnalysisValidationError("Inclua pelo menos uma linha de dados além do cabeçalho.")
    columns = []
    for index, key in enumerate(headers):
        present = [row[index] for row in values if row[index] is not None]
        if present and all(_numeric(value) is not None for value in present):
            kind = "quantitative"
            for row in values:
                if row[index] is not None:
                    row[index] = _numeric(row[index])
        elif present and all(_temporal(value) for value in present):
            kind = "temporal"
            for row in values:
                if row[index] is not None:
                    row[index] = _utc_temporal(row[index])
        else:
            kind = "nominal"
            for row in values:
                if row[index] is not None:
                    row[index] = str(row[index])
        columns.append({"name": key, "type": kind})
    return columns, [dict(zip(headers, row)) for row in values]


def _read_csv(content: bytes):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AnalysisValidationError("Salve o CSV com codificação UTF-8.") from None
    try:
        first_line = text.splitlines()[0] if text else ""
        dialect = csv.Sniffer().sniff(first_line, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    try:
        return _normalize(csv.reader(io.StringIO(text), dialect, strict=True))
    except csv.Error:
        raise AnalysisValidationError("O CSV está malformado. Confira as linhas e os delimitadores.") from None


def _read_xlsx(content: bytes):
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    source = cached = None
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_XLSX_EXPANDED_BYTES:
                raise AnalysisValidationError("A planilha descompactada excede o limite de 30 MiB.")
        source = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        cached = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
        sheet = next((item for item in source.worksheets if item.sheet_state == "visible"), None)
        if sheet is None:
            raise AnalysisValidationError("A planilha precisa ter uma aba visível com dados.")
        if (sheet.max_row or 0) > MAX_ROWS + 1 or (sheet.max_column or 0) > MAX_COLUMNS:
            raise AnalysisValidationError(f"Use no máximo {MAX_ROWS} linhas e {MAX_COLUMNS} colunas.")
        values = cached[sheet.title]

        def table():
            for row, cached_row in zip(sheet.iter_rows(), values.iter_rows()):
                result = []
                for cell, cached_cell in zip(row, cached_row):
                    if cell.data_type == "e" or cached_cell.data_type == "e":
                        raise AnalysisValidationError("Corrija as células com erro antes de importar.")
                    if cell.data_type == "f" and cached_cell.value is None:
                        raise AnalysisValidationError("Recalcule e salve as fórmulas no Excel antes de importar.")
                    result.append(cached_cell.value if cell.data_type == "f" else cell.value)
                yield result

        return _normalize(table())
    except (BadZipFile, InvalidFileException, KeyError, ValueError, OSError, ParseError, IndexError, TypeError) as error:
        if isinstance(error, AnalysisValidationError):
            raise
        raise AnalysisValidationError("O arquivo XLSX está inválido ou corrompido.") from None
    finally:
        if source is not None:
            source.close()
        if cached is not None:
            cached.close()


class AnalyticsService:
    def __init__(self, analysis_service: AnalysisService, repository: AnalyticsRepository):
        self.analysis_service = analysis_service
        self.repository = repository

    @staticmethod
    def dataset_public(dataset: dict) -> dict:
        return {**{key: value for key, value in dataset.items() if key != "rows"}, "preview": dataset["rows"][:20]}

    def import_dataset(self, content: bytes, filename: str, name: str | None = None) -> dict:
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_FILE_BYTES:
            raise AnalysisValidationError("Envie um arquivo não vazio de até 5 MiB.")
        filename = _text(filename, "O nome do arquivo", 200).replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(filename).suffix.lower()
        if suffix not in {".csv", ".xlsx"}:
            raise AnalysisValidationError("Envie uma planilha CSV ou XLSX.")
        title = _text(name if name is not None else Path(filename).stem, "O nome dos dados", 120)
        columns, rows = _read_csv(content) if suffix == ".csv" else _read_xlsx(content)
        dataset = {
            "id": str(uuid4()), "name": title, "filename": filename, "columns": columns, "rows": rows,
            "row_count": len(rows), "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        stored = self.repository.insert_dataset(dataset, hashlib.sha256(content).hexdigest())
        return self.dataset_public(stored)

    def import_demo(self) -> dict:
        try:
            content = (Path(__file__).resolve().parent.parent / "data" / "vendas_demo.csv").read_bytes()
        except OSError:
            raise AnalysisStorageError("Os dados de exemplo não estão disponíveis.") from None
        return self.import_dataset(content, "vendas_demo.csv", "Vendas de exemplo — dados fictícios")

    def list_datasets(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 2147483647:
            raise AnalysisValidationError("Informe limite entre 1 e 100 e offset não negativo.")
        return [self.dataset_public(dataset) for dataset in self.repository.list_datasets(limit=limit, offset=offset)]

    def _dataset(self, dataset_id: str) -> dict:
        dataset = self.repository.get_dataset(_id(dataset_id))
        if dataset is None:
            raise AnalysisNotFound("Esta fonte de dados não foi encontrada.")
        return dataset

    def get_dataset(self, dataset_id: str) -> dict:
        return self.dataset_public(self._dataset(dataset_id))

    def create_chart(self, analysis_id: str, arguments: dict | ChartCreateArgs) -> dict:
        args = arguments if isinstance(arguments, ChartCreateArgs) else ChartCreateArgs.model_validate(arguments)
        analysis = self.analysis_service.get(analysis_id)
        dataset = self._dataset(args.dataset_id)
        title = _text(args.title, "O título do gráfico", 120)
        columns = {column["name"]: column["type"] for column in dataset["columns"]}
        for field in (args.x, args.y, args.color):
            if field is not None and field not in columns:
                raise AnalysisValidationError(f"A coluna {field} não existe nesta fonte de dados.")
        if columns[args.y] != "quantitative" and args.aggregate != "count":
            raise AnalysisValidationError("O eixo Y precisa usar uma coluna numérica para esta agregação.")
        if args.color and columns[args.color] != "nominal":
            raise AnalysisValidationError("A cor precisa usar uma coluna de categorias.")
        if args.color in {args.x, args.y}:
            raise AnalysisValidationError("Escolha para cor uma coluna diferente dos eixos X e Y.")
        if not any(row[args.x] is not None and row[args.y] is not None for row in dataset["rows"]):
            raise AnalysisValidationError("Os eixos escolhidos não têm dados para exibir.")
        if args.mark in {"line", "area"} and columns[args.x] == "nominal":
            raise AnalysisValidationError("Gráficos de linha e área precisam de datas ou números no eixo X.")
        # Missing axis values are omitted, never replaced by zero. Count counts valid Y cells.
        rows = [row for row in dataset["rows"] if row[args.x] is not None and row[args.y] is not None]
        aggregate_label = {"sum": "Soma", "mean": "Média", "count": "Contagem", "min": "Mínimo", "max": "Máximo"}[args.aggregate]
        metric = {"field": args.y, "type": "quantitative", "aggregate": args.aggregate, "title": f"{aggregate_label} de {args.y}"}
        encoding = {
            "x": {"field": args.x, "type": columns[args.x], "title": args.x},
            "y": metric,
        }
        if columns[args.x] == "temporal":
            # Date-only ISO strings represent UTC midnight. Use UTC ticks so a
            # browser in Brazil does not label January 1 as December 31.
            encoding["x"]["scale"] = {"type": "utc"}
        if args.color:
            encoding["color"] = {"field": args.color, "type": "nominal", "title": args.color}
        spec = {
            "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
            "description": title, "width": "container", "height": 320,
            "data": {"values": rows},
            # Generated encoding tooltips inherit the X scale's UTC timezone.
            # A separate temporal tooltip channel would default to local time.
            "mark": {"type": args.mark, "tooltip": True},
            "encoding": encoding,
            "config": {"view": {"stroke": None}, "axis": {"labelFontSize": 11, "titleFontSize": 12}},
        }
        chart = {
            "id": str(uuid4()), "analysis_id": analysis.id, "dataset_id": dataset["id"],
            "title": title, "spec": spec, "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        self.repository.insert_chart(chart)
        return chart

    def list_charts(self, analysis_id: str) -> list[dict]:
        analysis = self.analysis_service.get(analysis_id)
        return self.repository.list_charts(analysis.id)

    def get_chart(self, chart_id: str) -> dict:
        chart = self.repository.get_chart(_id(chart_id))
        if chart is None:
            raise AnalysisNotFound("Este gráfico não foi encontrado.")
        self.analysis_service.get(chart["analysis_id"])
        return chart

    def get_analysis(self, analysis_id: str) -> dict:
        analysis = self.analysis_service.get(analysis_id)
        return {**analysis.public(), "charts": self.repository.list_charts(analysis.id)}
