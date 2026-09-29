# Contributing to Quanifi

Contributions are welcome. Please read the licensing section below before opening
a pull request — it is not boilerplate, and a patch cannot be merged without it.

## Licensing of contributions

Quanifi is dual-licensed: AGPL-3.0 for everyone, and a commercial license for
organisations that cannot comply with the AGPL (see [COMMERCIAL.md](COMMERCIAL.md)).
Offering that second option requires that a single party holds the rights to the
whole codebase. For that reason, by submitting a contribution you agree that:

1. You are the sole author of the contribution, or you have the right to submit
   it under these terms.
2. You grant Neilson Ramalho a perpetual, worldwide, non-exclusive, royalty-free,
   irrevocable license to use, reproduce, modify, distribute, and sublicense your
   contribution, **including the right to relicense it under the commercial
   license and under any future license of the project**.
3. Your contribution is also released under the AGPL-3.0, so it remains free
   software for all other users.
4. You retain your own copyright in your contribution. This is a license, not an
   assignment.

If you are contributing in the course of employment, or as part of academic work
where your institution may claim ownership, confirm that you are permitted to
grant the above before submitting.

State your agreement in the pull request description with:

    I agree to the contribution terms in CONTRIBUTING.md.

## Development

```bash
uv sync --frozen --extra dev --extra iqm --python 3.12
just test
```

See `docs/README.md` for the documentation index and `docs/` for the component
reference and development guides.

## Before opening a pull request

- `just test` passes.
- New processors follow the shared FlowFile attribute contract used by the
  existing ones, and are documented in `docs/`.
- No credentials, API keys, or provider tokens appear in the diff, in test
  fixtures, or in flow definitions.
