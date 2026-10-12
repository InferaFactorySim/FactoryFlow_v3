"""In-memory telemetry sink, replacing RabbitMQSender for in-process experiments.

Mirrors the MockSender pattern already used in tests/experiment2/3, centralised
here so every experiment captures events the same way.
"""


class MockSender:
    """Captures published telemetry messages in ``self.events`` (list of dicts)."""

    def __init__(self):
        self.events = []

    def send(self, msg):
        self.events.append(msg)

    def close(self):
        pass
