.PHONY: run demo test eval samples dwg-setup dwg-samples dwg-eval

run:
	uv run python app.py

demo:
	uv run python app.py --demo

test:
	uv run python -m unittest discover -s tests -v

samples:
	uv run python scripts/generate_eval_samples.py

eval: samples
	uv run python -m eval.run_eval

dwg-setup:
	uv run python scripts/setup_libredwg.py

dwg-samples: dwg-setup
	uv run python scripts/generate_dwg_samples.py

dwg-eval: dwg-setup
	uv run python -m eval.run_dwg_eval
