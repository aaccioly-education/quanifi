set shell := ["bash", "-cu"]

python := ".venv/bin/python"

test:
    {{python}} -m pytest --tb=short -q

test-file file:
    {{python}} -m pytest --tb=short -q "{{file}}"

format:
    {{python}} -m black nifi_extensions tests tools web

lint:
    {{python}} -m ruff check nifi_extensions tests tools web

check: lint test

qaoa-examples:
    {{python}} tools/build_qaoa_examples.py --run --nxm

docs:
    {{python}} tools/generate_docs_site.py

grover-examples:
    {{python}} tools/build_grover_examples.py --run

quickstart-smoke:
    {{python}} tools/quickstart_smoke.py

publish-reports:
    {{python}} manage.py publish_report_files

