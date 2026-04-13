# Local development automation for ha-grocy-helper

VENV_DIR := .venv

ifeq ($(OS),Windows_NT)
	VENV_PYTHON := $(VENV_DIR)/Scripts/python.exe
else
	VENV_PYTHON := $(VENV_DIR)/bin/python
endif

.PHONY: init-local-env test pytest

init-local-env:
	python -m venv $(VENV_DIR)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt
	$(VENV_PYTHON) -m pre_commit install

pytest:
	$(VENV_PYTHON) -m pytest tests/ -v

test: pytest
