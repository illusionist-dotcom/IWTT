.PHONY: run test verify

run:
	python -m iwtt.server

test:
	python -m unittest discover -s tests -v

verify:
	python scripts/verify_release.py
