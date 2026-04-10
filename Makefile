.PHONY: init dev prod migrate check test lint format clean deploy

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
	.venv/bin/pytest tests/ -v

lint:
	.venv/bin/ruff check .

format:
	.venv/bin/ruff format .

clean:
	rm -rf __pycache__ .pytest_cache .venv

deploy:
	bash scripts/deploy.sh
