# Quanifi documentation

> **Docker users: only a subset of processors is installed by default.**
> The full catalogue includes processors that are not in the quickstart image.
> Add the processors you need to `docker/processors.txt`, then run
> `docker compose up -d --build nifi`. You can also select a custom list or all
> processors through `.env`. [Enable additional processors](guides/DOCKER_QUICKSTART.md#adding-processors-to-the-image).

This directory contains documentation for users and framework contributors.
Research plans, experiment protocols and generated study reports are maintained
in separate repositories.

## Reference

- [Component catalogue](COMPONENTS.md)

## Framework guides

- [Docker quick start](guides/DOCKER_QUICKSTART.md)
- [Developer guide](guides/DEVELOPER_GUIDE.md)
- [Creating processors](guides/CREATING_PROCESSORS.md)
- [Configuring flows on the NiFi canvas](guides/NIFI_FLOW_CONFIGURATION_GUIDE.md)
- [pyQuil components and example canvases](guides/PYQUIL_COMPONENTS.md)
- [QAOA components, N×M flow and example canvases](guides/QAOA_COMPONENTS.md)
- [Interchangeable Grover components](guides/INTERCHANGEABLE_GROVER_FLOW.md)
- [Data-driven testing](guides/DATA_DRIVEN_TESTING.md)
- [Mutation testing](guides/MUTATION_TESTING.md)
- [Mutation testing on the canvas](guides/MUTATION_CANVAS_TESTING.md)
- [Demo runbook](guides/DEMO_RUNBOOK.md)

The introductory HTML examples are under [`../guides/`](../guides/).
