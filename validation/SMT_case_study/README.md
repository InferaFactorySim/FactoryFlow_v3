# SMT case study — a published assembly line

Material behind Section 6.4, *Case Study: A Published SMT Assembly Line*: a
Surface Mount Technology printed-circuit-board line whose structure, parameters
and reported KPIs all come from a published study [Karanjkar et al., 2018],
not from this work.

## Folders

- **`published_reference_model/`** — the published SimPy model of the line and
  its result files, copied unchanged from the authors' repository (MIT licence,
  © 2017 Neha Karanjkar; see `LICENSE`). `model/` is the line studied in
  Section 6.4; `model_2/` is the variant with a PCB buffering module, and
  `model_2/Results/` holds the published result files.
- **`factoryflow/`** — the FactoryFlow side of the case study.
  - `smt_description.txt` — the system descriptions given to the generator, as
    named sections (`=== DESCRIPTION: <name> ===`): the dense specification and
    the plain-language account of the line.
  - `smt_line_factorysimpy.py` — the FactorySimPy reference implementation of
    the published line, used as the structural reference and the KPI baseline.
  - `generate_smt_model.py` — runs the FactoryFlow generator (extended schema:
    ConveyorBelt, BatchSource, HumanOperator, ServicedMachine,
    PowerCycledMachine) on one description section.
  - `generated/dense/`, `generated/operator/` — every generated candidate with
    the assumptions the generator recorded; `operator/` is the plain-language
    description. `summary.json` lists each generation run.
  - `evaluate_all.py`, `evaluate_generated.py`, `check_structure.py` — KPI and
    structural evaluation of the candidates; `table1_sweep.py`,
    `diagnose_cycle_time.py`, `repair_generated.py` and `test_*.py` are the
    supporting analyses and component tests.
- **`results/`** — evaluation outputs (regenerated from the files above):
  - `kpi_comparison_rerun_plain_language_description.txt` (the set in the paper,
    Table 12) and `kpi_comparison_rerun_dense_description.txt` — each final-set
    candidate's deviation from the published throughput, maximum cycle time,
    energy per board and reflow-oven warm-up fraction;
    `kpi_comparison_plain_language_description.txt` and
    `kpi_comparison_dense_description.txt` — the same for the earlier set.
  - `structure_comparison_rerun_candidates.txt` and
    `structure_comparison_all_candidates.txt` — each candidate against the
    reference graph (9 nodes including the operator, 7 edges), final and earlier
    sets.
  - `paper_section_6_4.tex` — the section as written.

## Results in brief

Two sets of candidates are kept.

**Final set — reported in Section 6.4.** `generated/rerun_operator/` and
`generated/rerun_dense/`, ten candidates each, every one generated from the same,
final text of its description (saved as `description_used_1.txt` in each folder).
Section 6.4 reports only `rerun_operator/`, the plain-language description,
which the paper calls "the system description".

| | Plain language (`rerun_operator`, in the paper) | Dense (`rerun_dense`) |
|---|---|---|
| Candidates generated | 10 | 10 |
| Executed unrepaired | 6 | 6 |
| Structurally identical to the reference (of those that built) | 6 / 6 | 6 / 6 |
| Within 0.01% on all four KPIs | 6 / 6 | 5 / 6 |

Failures were all library-interface errors: imports of non-existent modules, and
(plain language, candidates 3 and 7) power ratings declared for a machine state,
`SERVICE_STATE`, that FactorySimPy does not define. Every executing plain-language
candidate recorded the oven assumption explicitly (assumption 5: one warm-up, then
held at temperature). `rerun_dense/generated_9` instead assumed the oven powers
down whenever idle (its assumption 1) and deviates on every KPI.

**Earlier set — kept as evidence.** `generated/operator/` (7) and
`generated/dense/` (2) were generated while the descriptions were still being
revised, so they do not share one description text:

- dense 1–2 (28 Sep, 15:54) used the dense text when the source was still named
  `src`; it was renamed `source_1` at 16:28, which is why dense `generated_2`
  differs from the reference only in that name;
- plain-language 1–5 (28 Sep) predate the sentence stating that the printer's
  cleaning cycle needs no operator, added 29 Sep 16:17; candidates 6–7 were
  generated after it.

Of these, `operator/generated_1` powers the oven down whenever idle, and
`operator/generated_2` modelled the cleaning as an operator task (its assumption
6): cleanings queued for the operator (565 tasks instead of 50, 1,180 s of
waiting), raising maximum cycle time by 13.85%. Moving only the cleaning into
the printer's processing delay restores agreement within 0.01%.

## Other folders

- **`src/factorysimpy/`** — the FactorySimPy version used for the paper,
  including the components this line needs (`ConveyorBelt`, `BatchSource`,
  `HumanOperator`, `ServicedMachine`, `PowerCycledMachine`).
- **`generator/IM/`** — the FactoryFlow model generator as used for the paper
  (`AMG_with_langgraph.py`, the extended schema `amg_extensions.py`, and the
  base examples it is prompted with). Generation used Gemini 2.5 Pro.

## Reproducing

Requires Python 3.9 or later and the packages in `requirements.txt`. The
evaluation needs nothing outside this folder. From `factoryflow/`:

```
pip install -r ../requirements.txt
python evaluate_all.py generated/rerun_operator     # Table 12: the set in the paper
python evaluate_all.py generated/rerun_dense
python evaluate_all.py generated/operator           # earlier set
python evaluate_all.py generated/dense
python check_structure.py generated/rerun_dense/generated_*.py generated/rerun_operator/generated_*.py
python check_structure.py generated/dense/generated_*.py generated/operator/generated_*.py
python smt_line_factorysimpy.py                     # the reference implementation against the published results
```

The simulations are deterministic. Run from a clean copy of this folder on
12 October 2026, the two `evaluate_all.py` runs on the final set and the two
`check_structure.py` runs printed the files in `results/` exactly. The one
exception is wording: under Python 3.9 a `TypeError` reads `__init__() got…`
where the stored output, produced under a later Python, reads
`Machine.__init__() got…`. (On Windows, pass the file lists explicitly or let the
shell expand them, e.g. in Git Bash.)

Generating new candidates calls the LLM, so it needs a Google API key, in the
environment or in `generator/IM/.env`, as `GOOGLE_API_KEY_3_FACTORYFLOW_ERROR_ANALYSIS`:

```
python generate_smt_model.py 10 operator my_batch    # writes generated/my_batch/
```

LLM output varies between calls, so new candidates will not be identical to the
archived ones; the archived candidates in `generated/` are the ones evaluated in
the paper. Set `FACTORYFLOW_DIR` to use the generator from another FactoryFlow
checkout instead of `generator/`.
