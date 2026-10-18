from .base import Workflow
from .summarize import SummarizeWorkflow
from .analyze import AnalyzeWorkflow
from .ask import AskWorkflow

WORKFLOWS: dict[str, Workflow] = {
    'summarize': SummarizeWorkflow(), 'analyze': AnalyzeWorkflow(), 'ask': AskWorkflow(),
}
