export PYTHONPATH := src
export IL_DATA_DIR := ./data
export PYTHONIOENCODING := utf-8

.PHONY: install bootstrap query eval compare test serve golden docker fmt

install:
	pip install -r requirements.txt

bootstrap:          ## build + index the sample corpus
	python -m insightledger.cli bootstrap

golden:             ## (re)generate the golden set jsonl
	python -c "from insightledger.eval.golden_set import write_golden; print(write_golden())"

query:              ## make Q="..." query
	python -m insightledger.cli query "$(Q)"

eval:               ## run the eval harness with the CI gate
	python -m insightledger.cli eval --gate

compare:            ## text-RAG baseline vs Recto
	python -m insightledger.cli compare

test:
	python -m pytest -q -p no:warnings

serve:              ## run the API + web UI on :8000
	python -m insightledger.cli serve --host 0.0.0.0 --port 8000

docker:
	docker compose up --build
