.DEFAULT_GOAL := help

ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif

.PHONY: help install validate-config format format-check lint type-check test test-unit test-integration check ci build clean

# Python owns the commands; Make is an optional convenience on every platform.
help install validate-config format format-check lint type-check test test-unit test-integration check ci build clean:
	$(PYTHON) scripts/dev.py $@
