from .base import ScreenWorkflow


class AskWorkflow(ScreenWorkflow):
    instruction = ('Answer the user question using the screen and selected history where relevant. '
                   'Ask a concise clarification if needed.')
