from .base import ScreenWorkflow


class AnalyzeWorkflow(ScreenWorkflow):
    instruction = ('Analyze the supplied screen using the optional focus. Separate observations '
                   'from hypotheses and suggest checks. Do not claim an unsupported root cause.')
