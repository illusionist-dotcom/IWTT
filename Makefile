.PHONY: run test verify verify-v02 verify-all bench sample clean

run:
	python -m iwtt.server

test:
	python -m unittest discover -s tests -v

verify:
	python scripts/verify_release.py

verify-v02:
	python scripts/verify_release_v02.py

# Every gate, in the order the release checklist uses.
verify-all: test verify verify-v02

# Measured performance at 1,000 and 10,000 thoughts; fails if a budget is exceeded.
bench:
	python scripts/benchmark_recall.py --suite --queries 200

sample:
	python scripts/build_sample_vault.py

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
