# Ingestion gold fixtures

These six versioned cases exercise the MVP source modes without requiring provider credentials in ordinary CI.
`gold.json` contains reviewer-owned business annotations. Text transcripts and frame descriptions are deterministic
surrogates consumed by mocked preprocessing/generation tests; the live media run remains an explicit manual eval.

Reviewer matching is recorded as step IDs and scored by `automation_foundry.evals.ingestion`. A generated phrase counts
only after one reviewer proposes the semantic match and the other confirms it, as required by `docs/EVALS.md`.
