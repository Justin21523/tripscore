## Notebooks

These notebooks are a notebook-first walkthrough of TripScore's "analysis" pipeline:
ingestion → features → scoring → recommender → explanations.

Note:
- `.ipynb` is JSON; if you view it as raw text you'll see `\\n` escapes. Open it in Jupyter/VSCode Notebook UI.

### Setup

1) Create/activate a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-notebooks.txt
```

2) Ensure your environment has TDX credentials (optional, only for live TDX cells):

```bash
export TDX_CLIENT_ID="..."
export TDX_CLIENT_SECRET="..."
```

3) Start Jupyter:

```bash
jupyter lab
```

### Notebook index

- `00_setup.ipynb` — environment sanity checks + load settings/catalog
- `01_pipeline_walkthrough.ipynb` — offline (stubbed) end-to-end recommendation + explainability
- `02_tdx_live_smoke.ipynb` — optional live TDX data smoke checks (requires credentials + network)
- `03_recommend_live.ipynb` — live end-to-end recommendation run (TDX + weather + explainability)
- `04_tdx_bulk_prefetch.ipynb` — stageable bulk prefetch (paged) full TDX datasets into `.cache/`
