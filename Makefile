.PHONY: run demo test eval samples

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
