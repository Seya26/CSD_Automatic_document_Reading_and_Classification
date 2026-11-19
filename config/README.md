# config/

- `municipalities.yaml`: notice boards to download from (placeholders to fill in).
- `fewshot.json`: few-shot examples for the prompt (task 39). A list of objects
  `{"text": "<document text>", "output": {<input of the record_extraction tool>}}`.
  Use documents from the **development** municipalities only, never from the held-out one (task 40).
  Changing it does not change `PROMPT_VERSION`: bump it in `src/albo/llm/prompts.py` so that the
  evaluation can tell the iterations apart.
- `output_record.schema.json`: generated with `python -m albo export-schema`.
