# Quanifi documentation

> **Docker users: only a subset of processors is installed by default.**
> The full catalogue includes processors that are not in the quickstart image.
> Add the processors you need to `docker/processors.txt`, then run
> `docker compose up -d --build nifi`. You can also select a custom list or all
> processors through `.env`. [Enable additional processors](guides/DOCKER_QUICKSTART.md#adding-processors-to-the-image).

This directory contains documentation for users and framework contributors.
Research plans, experiment protocols and generated study reports are maintained
in separate repositories.

## Beginner Roadmap

If you are new to Quanifi, follow this 4-step pathway:

1. **[Module 1: Introduction to Quanifi](tutorials/01_introduction_to_quanifi.html)** — Understand the visual quantum software engineering paradigm and FlowFile dataflow model.
2. **[Developer Guide](guides/DEVELOPER_GUIDE.md)** or **[Docker Quickstart](guides/DOCKER_QUICKSTART.md)** — Set up your Python 3.12 environment with `uv` or launch the pre-packaged NiFi container.
3. **[Configuring flows on the NiFi canvas](guides/NIFI_FLOW_CONFIGURATION_GUIDE.md)** — Import and execute your first quantum flow using interactive "Run Once".
4. **[Interactive Tutorials Curriculum](tutorials/index.html)** — Work through 6 step-by-step quantum algorithm modules (Deutsch-Jozsa, Bernstein-Vazirani, Grover, QAOA, Graph Decomposition).

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
- [Mutation testing on the canvas](guides/MUTATION_CANVAS_TESTING.md)

## Citation

If you use Quanifi in your research, please cite our preprint:

> Neilson Carlos Leite Ramalho, Higor Amario de Souza, Anthony Accioly, Valter Vieira de Camargo, and Marcos Lordello Chaim. (2026). *NxM-Version Programming for Quantum Software: High-Level Components across Frameworks and Engines*. arXiv:2609.33255 [quant-ph]. <https://arxiv.org/abs/2609.33255>

```bibtex
@misc{ramalho2026nxmversionprogrammingquantumsoftware,
      title={NxM-Version Programming for Quantum Software: High-Level Components across Frameworks and Engines}, 
      author={Neilson Carlos Leite Ramalho and Higor Amario de Souza and Anthony Accioly and Valter Vieira de Camargo and Marcos Lordello Chaim},
      year={2026},
      eprint={2609.33255},
      archivePrefix={arXiv},
      primaryClass={quant-ph},
      url={https://arxiv.org/abs/2609.33255}, 
}
```

The introductory HTML examples are under [Interactive Tutorials](tutorials/index.html).
