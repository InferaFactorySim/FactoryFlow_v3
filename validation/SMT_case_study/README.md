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
  - `kpi_comparison_plain_language_description.txt` and
    `kpi_comparison_dense_description.txt` — each candidate's deviation from the
    published throughput, maximum cycle time, energy per board and reflow-oven
    warm-up fraction (Table 17).
  - `structure_comparison_all_candidates.txt` — each candidate against the
    reference graph (9 nodes including the operator, 7 edges).
  - `paper_section_6_4.tex` — the section as written.

## Results in brief

| | Plain language | Dense specification |
|---|---|---|
| Candidates generated | 7 | 2 |
| Executed unrepaired | 6 | 1 |
| Structurally identical to the reference (of those that built) | 6 / 6 | 1 / 1, apart from naming the source `src` instead of `source_1` |
| Within 0.01% on all four KPIs | 4 | 1 |

Build failures were library-interface errors (an import of a non-existent
module; an unknown constructor argument), caught by the execution check.
`operator/generated_1` powers the oven down whenever it is idle — the recorded
assumption discussed in Section 6.4 — and so deviates on every KPI.
`operator/generated_2` matches throughput and warm-up but is 13.85% high on
maximum cycle time and 0.22% low on energy per board.

## Reproducing

From `factoryflow/`: `python evaluate_all.py generated/operator`,
`python evaluate_all.py generated/dense`, `python check_structure.py`.
FactorySimPy (`src/`) must be on the Python path; the scripts look for it two
directories up, as in the working tree (`journal_validation/smt_case`).
