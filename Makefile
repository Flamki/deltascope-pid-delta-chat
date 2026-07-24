.PHONY: run demo test eval samples

run:
	python app.py

demo:
	python app.py --demo

test:
	python -m unittest discover -s tests -v

samples:
	python scripts/generate_eval_samples.py

eval: samples
	python -m eval.run_eval
