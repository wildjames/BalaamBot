.PHONY: install-dev lint run clean unpack

# Installs both normal and dev dependencies
# Not needed for running as uv run handles deps itself
install-dev:
	uv run pre-commit install --install-hooks
	uv sync

lint:
	uv run ruff check .

build:
	uv build

run:
	uv run balaambot

unpack:
	unzip -u sounds.zip

clean:
	@echo "Cleaning up..."
	# Remove Python bytecode and __pycache__ directories
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	# Remove Ruff cache
	rm -rf .ruff_cache
	# Remove build artifacts
	rm -rf build/ dist/ *.egg-info
	# Remove sounds directory if it exists
	rm -rf sounds/
	rm -rf persistent/
	rm .coverage

docker-build:
	docker build -t balaambot:latest .

docker-run:
	docker run --rm -it \
		--env-file .env \
		-v $(LOCAL_WORKSPACE_FOLDER)/persistent:/app/persistent \
		balaambot:latest

docker-brun: docker-build docker-run

test:
	uv run pytest -m "not integration"

test-integration:
	uv run pytest -m integration
