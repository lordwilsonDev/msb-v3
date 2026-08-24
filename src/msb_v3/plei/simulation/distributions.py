"""Distributions — triangular, PERT, beta, and normal probability distributions.

Deterministic, stdlib-only. Every distribution implements ``sample()`` and
``describe()``. Used by the Monte Carlo engine to model uncertain variables.

Design: we use Python's ``random`` module seeded with a fixed seed for
reproducibility. Every simulation run is repeatable.

Distributions available:
    Triangular(min, mode, max)       — low / most-likely / high
    PERT(min, mode, max)             — Beta-approximated triangular (smoother)
    Normal(mean, stdev)              — Gaussian
    Fixed(value)                     — deterministic
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(slots=True)
class Dist:
    """Base for all distributions."""

    def sample(self, rng: random.Random | None = None) -> float:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


@dataclass(slots=True)
class Fixed(Dist):
    """Deterministic — always returns the same value."""

    value: float

    def sample(self, rng: random.Random | None = None) -> float:
        return self.value

    def describe(self) -> str:
        return f"Fixed({self.value})"


@dataclass(slots=True)
class Triangular(Dist):
    """Triangular distribution — low / mode / high.

    Good for expert estimates: "optimistic / likely / pessimistic."
    """

    low: float
    mode: float
    high: float

    def __post_init__(self) -> None:
        if not (self.low <= self.mode <= self.high):
            raise ValueError(
                f"Triangular requires low({self.low}) <= mode({self.mode}) <= high({self.high})"
            )

    def sample(self, rng: random.Random | None = None) -> float:
        r = rng or random
        u = r.random()
        c = (self.mode - self.low) / (self.high - self.low) if self.high > self.low else 0.5
        if u < c:
            return self.low + math.sqrt(u * (self.high - self.low) * (self.mode - self.low))
        return self.high - math.sqrt((1 - u) * (self.high - self.low) * (self.high - self.mode))

    def describe(self) -> str:
        return f"Triangular(low={self.low}, mode={self.mode}, high={self.high})"


@dataclass(slots=True)
class PERT(Dist):
    """PERT distribution — Beta-approximated triangular.

    Smoother than Triangular; same three-point parameterization.
    Commonly used in project estimation where tails matter.
    """

    low: float
    mode: float
    high: float

    def __post_init__(self) -> None:
        if not (self.low <= self.mode <= self.high):
            raise ValueError(f"PERT requires low({self.low}) <= mode({self.mode}) <= high({self.high})")

    def sample(self, rng: random.Random | None = None) -> float:
        r = rng or random
        # PERT mean = (low + 4*mode + high) / 6
        mean = (self.low + 4.0 * self.mode + self.high) / 6.0
        # Shape parameters from mean
        if self.high <= self.low:
            return self.low
        alpha = 1.0 + 4.0 * (mean - self.low) / (self.high - self.low)
        beta_val = 1.0 + 4.0 * (self.high - mean) / (self.high - self.low)
        # Use gamma-based beta sampling
        x = _beta_sample(alpha, beta_val, r)
        return self.low + x * (self.high - self.low)

    def describe(self) -> str:
        return f"PERT(low={self.low}, mode={self.mode}, high={self.high})"


@dataclass(slots=True)
class Normal(Dist):
    """Normal (Gaussian) distribution via Box-Muller."""

    mean: float
    stdev: float

    def __post_init__(self) -> None:
        if self.stdev < 0:
            raise ValueError(f"stdev must be non-negative: {self.stdev}")

    def sample(self, rng: random.Random | None = None) -> float:
        r = rng or random
        # Box-Muller
        u1 = r.random() or 1e-10
        u2 = r.random()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return self.mean + self.stdev * z

    def describe(self) -> str:
        return f"Normal(mean={self.mean}, stdev={self.stdev})"


# --- Helpers ---

def _beta_sample(alpha: float, beta_val: float, rng: random.Random) -> float:
    """Sample from Beta(alpha, beta) using the gamma method.

    Beta = X / (X + Y) where X ~ Gamma(alpha, 1), Y ~ Gamma(beta, 1).
    """
    x = _gamma_sample(alpha, rng)
    y = _gamma_sample(beta_val, rng)
    if x + y < 1e-15:
        return 0.5
    return x / (x + y)


def _gamma_sample(shape: float, rng: random.Random) -> float:
    """Sample from Gamma(shape, 1) using Marsaglia-Tsang for shape >= 1.

    Falls back to Johnk's generator for shape < 1.
    """
    if shape < 1.0:
        # Johnk's generator
        while True:
            u = rng.random()
            v = rng.random()
            if u + v <= 1.0:
                x = u ** (1.0 / shape)
                y = v ** (1.0 / shape)
                if x + y <= 1.0:
                    return -math.log(rng.random()) * x / (x + y)
    else:
        # Marsaglia-Tsang
        d = shape - 1.0 / 3.0
        c = 1.0 / math.sqrt(9.0 * d)
        while True:
            x = _standard_normal(rng)
            v = (1.0 + c * x) ** 3
            if v <= 0:
                continue
            u = rng.random()
            if u < 1.0 - 0.0331 * (x ** 4):
                return d * v
            if math.log(u) < 0.5 * x ** 2 + d * (1.0 - v + math.log(v)):
                return d * v


def _standard_normal(rng: random.Random) -> float:
    """Single standard normal via Box-Muller."""
    u1 = rng.random() or 1e-10
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def from_expert_triple(optimistic: float, likely: float, pessimistic: float) -> Triangular:
    """Convenience: three-point estimate → Triangular distribution."""
    return Triangular(optimistic, likely, pessimistic)


def from_pessimistic(value: float, uncertainty: float = 0.3) -> PERT:
    """Single estimate with uncertainty → PERT distribution.

    value × (1 - uncertainty) → low
    value → mode
    value × (1 + 2*uncertainty) → high (pessimism is asymmetric)
    """
    low = value * (1.0 - uncertainty)
    high = value * (1.0 + 2.0 * uncertainty)
    return PERT(max(0, low), value, high)