"""llm — Stage 4. Local Qwen (Ollama) turns chatter + structured signals into a human report
AND a strict JSON object (pydantic-validated).

HARD RULE: the LLM never emits a trade instruction. It returns features only:
    {summary, narrative_strength, scam_language_flags[], community_authenticity, notable_mentions}

    classify.py   per-message sentiment/spam classification at volume (llama3.2:3b)
    synthesize.py final synthesis + structured output (qwen2.5); prompts live here
    schema.py     pydantic models for the structured output

If Ollama is down and config.llm.fail_open_to_rules: skip this stage, scoring drops the
narrative weight and renormalizes.
"""
