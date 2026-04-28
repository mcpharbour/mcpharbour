.PHONY: all build test test-unit test-integration test-e2e install clean

all: build

# Install dependencies
install:
	pip install -e ".[dev]"

# Build standalone binaries
build:
	python -m PyInstaller --onefile --name harbour entry_harbour.py
	python -m PyInstaller --onefile --name harbour-bridge mcp_harbour/bridge.py
	python -m PyInstaller --onefile --name harbour-service entry_service.py

# Run all tests
test:
	pytest tests/

# Run only fast tests (no network)
test-unit:
	pytest tests/unit

# Run integration tests
test-integration:
	pytest tests/integration

# Run e2e tests (needs npx)
test-e2e:
	pytest tests/e2e

# Clean build artifacts
clean:
	-rm -r build/
	-rm -r dist/
	-rm -r *.spec
