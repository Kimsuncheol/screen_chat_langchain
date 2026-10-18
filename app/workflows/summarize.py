from .base import ScreenWorkflow


class SummarizeWorkflow(ScreenWorkflow):
    instruction = ('Summarize the supplied screen briefly. Preserve visible names, dates, '
                   'quantities and warnings. Do not invent missing facts.')
