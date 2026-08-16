"""Entry point — calls Engine.run and compute."""

from sample_repo.engine import Engine, compute


def main():
    engine = Engine(scale=2.0)
    result = engine.run([1, 2, 3])
    other = compute(4.0)
    print(result, other)


if __name__ == "__main__":
    main()
