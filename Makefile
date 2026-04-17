.PHONY: init dev prod migrate check test test-e2e inspect lint format clean deploy client-desktop client-package client-binary client-windows-installer

init:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	.venv/bin/python manage.py init-db
	mkdir -p data logs

dev:
	.venv/bin/flask --app app:create_app run --debug --host 0.0.0.0 --port 5000

prod:
	.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 'app:create_app()'

migrate:
	.venv/bin/python manage.py migrate

check:
	.venv/bin/python manage.py check

test:
	.venv/bin/pytest tests/ -v --ignore=tests/test_e2e.py

test-e2e:
	bash scripts/ab-e2e-test.sh

test-e2e-local:
	bash scripts/ab-e2e-test.sh http://localhost:5000

inspect:
	bash scripts/ab-inspect.sh

lint:
	.venv/bin/ruff check .

format:
	.venv/bin/ruff format .

clean:
	rm -rf __pycache__ .pytest_cache .venv

deploy:
	bash scripts/deploy.sh

client-desktop:
	python3 -m confidential_client.desktop

client-package:
	bash scripts/build-confidential-client.sh

client-binary:
	bash scripts/build-confidential-client-binary.sh

client-windows-installer:
	powershell -ExecutionPolicy Bypass -File scripts/build-windows-installer.ps1
