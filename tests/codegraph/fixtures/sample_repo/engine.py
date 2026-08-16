"""Engine module — fixtures for the Code Graph tests."""


from sample_repo.utils import helper, normalize


def compute(x: float, y: float = 1.0) -> float:
    """Multiply x by y, normalized."""
    z = normalize(x * y)
    return z


def total(values):
    """Sum a list of values."""
    acc = 0.0
    for v in values:
        acc = helper(acc, v)
    return acc


class Engine:
    """A small engine."""

    def __init__(self, scale: float = 1.0):
        self.scale = scale

    def run(self, values):
        """Run the engine over values."""
        return [compute(v, self.scale) for v in values]

    def reset(self):
        self.scale = 1.0
