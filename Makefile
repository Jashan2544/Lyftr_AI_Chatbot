.PHONY: up down logs test build restart clean

# Create .env from the example on first run so `make up` always works.
.env:
	cp .env.example .env
	@echo ">> Created .env from .env.example - put your real LLM_API_KEYS in it."

up: .env
	docker compose up -d --build
	@echo ">> Frontend: http://localhost:3000   Backend: http://localhost:8000/health"

down:
	docker compose down

restart: down up

logs:
	docker compose logs -f

build:
	docker compose build

test:
	cd backend && python -m pytest -q

clean:
	docker compose down -v
