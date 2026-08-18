# Maintainers

MSB v3 is maintained by a single operator. This file exists so the
foundation's authorship is unambiguous when someone else builds on it.

| Name | GitHub | Role |
|------|--------|------|
| [Lord Wilson](https://github.com/lordwilsonDev) | [@lordwilsonDev](https://github.com/lordwilsonDev) | Maintainer — architecture, governance, and the audit/evidence layer |

## Contact

- GitHub: [@lordwilsonDev](https://github.com/lordwilsonDev)
- Issues and PRs: open them on the [repository](https://github.com/lordwilsonDev/msb-v3).

## Becoming a maintainer

Maintainership is earned by demonstrated ownership of a subsystem, not
requested. To become a maintainer:

1. Contribute fixes or features that pass the full gate battery
   (`make lint`, `make policy-gate`, `make portability`).
2. Take sustained ownership of a subsystem (governed tools, the audit chain,
   MoIE policy, Vesta, the local-AI layer, the observability surface, …).
3. Be nominated by an existing maintainer.

All maintainers are expected to honor the project's core invariants: the
governed loop refuses safely, every privileged action produces evidence, and
the audit chain is tamper-evident. A change that silently weakens any of
those is not eligible for merge.
