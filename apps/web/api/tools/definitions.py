"""Public tool descriptions generated from their argument contracts."""

from .handlers.analyses import AnalysisCreateArgs
from .handlers.charts import ChartToolArgs
from .handlers.datasets import AnalysisGetArgs, DatasetGetArgs, EmptyArgs, ListArgs


ANALYSIS_CREATE_TOOL_NAME = "analysis_create"


def analysis_create_definition() -> dict:
    return {
        "name": ANALYSIS_CREATE_TOOL_NAME,
        "description": (
            "Cria uma nova análise com o título informado "
            "e retorna o identificador da análise criada."
        ),
        "inputSchema": AnalysisCreateArgs.model_json_schema(),
    }


def analytics_definitions() -> list[dict]:
    contracts = (
        ("dataset_list", "Lista as fontes de dados importadas, seus IDs, colunas e tipos. Consulte antes de criar gráficos.", ListArgs),
        ("dataset_get", "Obtém as colunas, tipos e até 20 linhas de uma fonte de dados pelo ID.", DatasetGetArgs),
        ("dataset_import_demo", "Importa dados fictícios de vendas de 2025 para demonstração. Pode repetir sem duplicar a fonte.", EmptyArgs),
        ("analysis_list", "Lista as análises salvas e os links para abri-las.", ListArgs),
        ("analysis_get", "Obtém uma análise e os gráficos já criados para ela.", AnalysisGetArgs),
        ("chart_create", "Cria e salva um gráfico Vega-Lite usando uma análise e uma fonte existentes. Use nomes exatos das colunas; Y deve ser numérico, exceto para count. Linhas e áreas exigem X temporal ou numérico. A cor é uma categoria opcional. Retorna o link da análise.", ChartToolArgs),
    )
    return [{"name": name, "description": description, "inputSchema": args.model_json_schema()}
            for name, description, args in contracts]
