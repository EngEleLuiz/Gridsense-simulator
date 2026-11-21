# Setting up a new machine

From a clean machine to a verified installation that can run the protocols.
Windows 11 commands are given for PowerShell; Linux and macOS equivalents follow
each step where they differ. Nothing here needs administrator rights except the
installers in step 1 and the GPU driver.

| Step | What | Needed for |
|---|---|---|
| 1 | Git, Python 3.12, (Docker) | everything (Docker only for the data platform) |
| 2 | Get the code | everything |
| 3 | Virtual environment | everything |
| 4 | PyTorch for your GPU (AMD, NVIDIA or none) | the batched solver; optional |
| 5 | The project and its extras | everything |
| 6 | Check the environment | before trusting any number |
| 7 | Run the test suite | before trusting any number |
| 8 | Input data | London runs; real-data series |
| 9 | Run the protocols and the report | results |
| 10 | Data platform (Kafka, TimescaleDB, Grafana, API) | dashboards; not needed for the studies |

## 1. Tools

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
winget install --id Docker.DockerDesktop -e      # only for step 10
```

Open a new terminal afterwards so the new commands are on the `PATH`.
`py -3.12 --version` must print 3.12.x. Python 3.12 is the version CI tests on
Windows and the one AMD's ROCm wheels for Windows require; 3.10 to 3.13 work
for a CPU-only installation.

`make` is optional: every `make` target in the README has a plain command, and
this guide uses only plain commands.

## 2. Code

```powershell
git clone https://github.com/EngEleLuiz/Gridsense-simulator C:\dev\Gridsense-simulator
cd C:\dev\Gridsense-simulator
```

Use a short path outside OneDrive-synced folders: the protocols write many small
files and synchronisation slows them down.

## 3. Virtual environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If PowerShell refuses to run the activation script:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again.
Linux/macOS: `python3.12 -m venv .venv && source .venv/bin/activate`.

Every command below assumes the environment is active (the prompt starts with
`(.venv)`).

## 4. PyTorch for your GPU

The batched solver (`--qsts-backend torch`) runs on the CPU as well, so this
step only decides **which build** of PyTorch to install. Install it **before**
step 5: step 5 then finds a suitable `torch` and leaves it alone.

Find the card first:

```powershell
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion
```

### AMD (ROCm)

On Windows, AMD publishes PyTorch wheels for a short list of cards (at the time
of writing, ROCm 7.2.1: Radeon RX 9070 / 9070 XT / 9060 XT, AI PRO R9700,
RX 7900 XTX, PRO W7900, RX 7700, and Ryzen AI APUs). Check the current list in
AMD's Windows support matrix ("ROCm on Radeon and Ryzen", rocm.docs.amd.com).
If the card is not supported (RX 6000 series and older, for instance), skip to
"No GPU" below; on Linux, ROCm supports more cards.

1. Install the AMD Software: Adrenalin Edition driver version that AMD's page
   names for the ROCm release (26.2.2 for ROCm 7.2.1). Restart.
2. Install the ROCm runtime and PyTorch (versions of ROCm 7.2.1; take the URLs
   from AMD's "PyTorch via PIP installation" page if a newer release exists):

```powershell
pip install --no-cache-dir `
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl `
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl `
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl `
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm-7.2.1.tar.gz
pip install --no-cache-dir `
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl
```

`torchvision` and `torchaudio` are not needed.

3. Check:

```powershell
python -c "import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expected: a version ending in `+rocm7.2.1`, a HIP version, `True` and the card's
name. The device is called `cuda` even on AMD: ROCm builds of PyTorch use the
same API.

### NVIDIA (CUDA)

Install the CUDA build with the command that the selector at
pytorch.org/get-started/locally gives for your system, then check with
`python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`.

### No GPU

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

## 5. The project

```powershell
pip install -e "simulator[dev,fast,data,plots,validation]" pytest-cov
```

| Extra | Brings | Used by |
|---|---|---|
| `dev` | pytest | the test suite |
| `fast` | numba | about 10x faster pandapower power flows |
| `data` | simbench, pyarrow | the SimBench networks and profiles (main case) |
| `plots` | matplotlib | `scripts/analyze_protocol.py --figures` |
| `validation` | opendssdirect.py | `scripts/validate_against_opendss.py` |
| `gpu` | torch (CPU build from PyPI) | **leave it out** when step 4 installed a GPU build |

Confirm that the GPU build survived:
`python -c "import torch; print(torch.__version__)"` must still show `+rocm…`
(or `+cu…`). If it shows a plain version, pip replaced it: repeat step 4.
Never run `pip install -U torch` or install the `gpu` extra afterwards.

## 6. Check the environment

```powershell
python scripts\check_environment.py --save
```

It lists the packages, says which PyTorch build is installed and whether it sees
a GPU, checks that the GPU reproduces the CPU in `float64` to 1e-10 pu, times
both, and recommends a device. It ends with `Ready.` or names what is missing.

**About the recommendation.** Consumer GPUs run `float64` at a small fraction of
their `float32` speed, and the feeders of the panel are small (15 to 300 buses),
so a good CPU is often as fast. If the script recommends the CPU, run the
protocols with `$env:GRIDSENSE_DEVICE = "cpu"` (Linux: `export
GRIDSENSE_DEVICE=cpu`); the protocol files stay untouched and every result
records the device that ran it. A detailed comparison:
`python scripts\benchmark_power_flow.py --networks simbench_lv_semiurb4 lv_schutterwald`.

## 7. Tests

```powershell
cd simulator
python -m pytest -q
cd ..
```

About 10 minutes. Expected: every test passes; about 5 are skipped because they
need real data files, and `test_cuda_matches_cpu` runs only when a GPU is
visible. Then the independent check of the power flow (about a minute):

```powershell
python scripts\validate_against_opendss.py
```

Expected: `12 agree, 0 differ, 2 not covered` (see `VALIDATION.md`).

## 8. Input data

| Data | Needed by | How |
|---|---|---|
| SimBench networks and profiles | groups A–E of protocol v2, most of v3 | nothing to do: shipped with the `simbench` package |
| Built-in networks (CIGRE, Kerber, Schutterwald, IEEE European LV) | groups A–C | nothing to do: shipped with pandapower |
| `data/series/london_2018_06.parquet` | 4 runs of protocol v2 (group F) | build from LCL + `uk_pv`, below |
| `data/series/london_2018_06_5min.parquet` | 4 runs of protocol v3 (group I) | same sources at 5 minutes, below |
| INMET, NASA POWER, PVGIS, Ausgrid | real-data examples (`make series-flo`) | downloaded on first use, or copy `data/raw/` |

`data/` is not in git. When moving from another machine, copy its `data/`
folder (raw downloads, series and earlier results) into the new clone.

London series. `uk_pv` is a gated Hugging Face dataset: accept its terms on
the dataset page, then:

```powershell
pip install huggingface_hub
hf auth login
hf download openclimatefix/uk_pv --repo-type dataset `
  --include "metadata.csv" "bad_data.csv" "5_minutely/year=2018/*" --local-dir data/raw/uk_pv
```

The Low Carbon London smart-meter file comes from the London Datastore
(`DATASETS.md`). With both in place:

```powershell
$LCL = "C:\path\to\the\LCL\file.csv"
foreach ($spd in 144, 288) {
  $suffix = if ($spd -eq 288) { "_5min" } else { "" }
  python -m gridsense_sim.datasets build-series --load lcl --load-file $LCL `
    --load-opt start=2013-01-01 --load-opt end=2014-01-01 `
    --pv uk_pv --pv-file data/raw/uk_pv --pv-opt start=2018-05-25 --pv-opt end=2018-07-10 `
    --site london --start 2018-06-01 --days 28 --steps-per-day $spd `
    --out "data/series/london_2018_06$suffix.parquet"
}
```

(Older versions of `huggingface_hub` call the command `huggingface-cli`.)
Runs whose input file is missing are listed as `needs data` and skipped; the
rest of the protocol runs without them.

## 9. Protocols and report

Read the checklist "Before the long runs" in `EXPERIMENT_PROTOCOL.md` first: the
numbers to cite must come from a clean, tagged commit, with `HYPOTHESES.md`
frozen before the runs.

```powershell
python -m pip freeze --exclude-editable > protocol\environment.lock.txt   # commit it with the tag
python scripts\run_protocol.py --protocol protocol\protocol-v2.json --list
python scripts\run_protocol.py --protocol protocol\protocol-v2.json
python scripts\run_protocol.py --protocol protocol\protocol-v3.json
python scripts\analyze_protocol.py data\protocol-v2 --sensitivity data\protocol-v3 --figures
```

The executor is resumable: stop it with Ctrl+C and start it again, finished runs
are skipped. The report goes to `reports\protocol-v2\` (`tables.md`,
`results.csv`, `analysis.json`, figures).

## 10. Data platform (optional)

Kafka, TimescaleDB, Grafana and the API, for the dashboards. With Docker
Desktop running:

```powershell
docker compose up -d
pip install -r ingestion/requirements-dev.txt -r api/requirements-dev.txt
py -3.12 -m venv .venv-dbt
.\.venv-dbt\Scripts\pip install -r transform/requirements.txt
```

dbt needs its own environment (it pins a `deepdiff` version incompatible with
pandapower). The README section "Run the telemetry pipeline" continues from here.
