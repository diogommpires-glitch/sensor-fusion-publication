#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
python_executable=python3.12
environment_path=.venv
if [ "$#" -ge 1 ]; then python_executable="$1"; fi
if [ "$#" -ge 2 ]; then environment_path="$2"; fi
export PYTHONUTF8=1
"$python_executable" -c "import sys; assert sys.version_info[:2] == (3,12), 'Python 3.12 is required'"
mkdir -p tmp/pip
export TMPDIR="$(pwd)/tmp/pip"
if [ ! -x "$environment_path/bin/python" ]; then
    "$python_executable" -m venv --without-pip "$environment_path"
fi
"$python_executable" -m pip --python "$environment_path/bin/python" install -r requirements-lock.txt
"$python_executable" -m pip --python "$environment_path/bin/python" install --no-deps --no-build-isolation -e .
npm ci --prefix frontend
"$environment_path/bin/python" scripts/build_frontend.py
printf '%s\n' "Setup complete. Run $environment_path/bin/python -m sensorfusion serve."
