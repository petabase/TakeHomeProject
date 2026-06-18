.PHONY: run setup clean help

help:
	@echo "TTB Label Verifier — available commands:"
	@echo "  make run     - install deps (if needed) and start the server"
	@echo "  make setup   - install deps only, don't start the server"
	@echo "  make clean   - remove the virtual environment and caches"

run:
	@./run.sh

setup:
	@./run.sh --setup-only

clean:
	@rm -rf venv
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete
	@echo "✅ Cleaned virtual environment and caches."

# Default target when just running `make` with no arguments
.DEFAULT_GOAL := run
