"""Run existing regression scripts against consistent, isolated database snapshots.

External HTTP requests are disabled; no collector or external submission is started.
Logs and a machine-readable summary are saved under the requested output directory.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))



def test_paths():
    import config
    return (ROOT / "tools", Path(config.ENTERPRISE_DIR) / "tools")


def resolve_test(script):
    if Path(script).name != script:
        raise ValueError("Test script must be a filename")
    for directory in test_paths():
        candidate = directory / script
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(script)


def child(script, serve=False):
    import config
    import requests

    def offline(session, method, url, *args, **kwargs):
        # Deterministic formula-service fixture, never a live credit operation.
        if method.upper() == "GET" and str(url).endswith("/cn/api/air_compressor_params"):
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps({"status": "成功", "output": {
                "carbon_emission": 8.947, "power_consumption": .15487}}).encode("utf-8")
            response.encoding = "utf-8"
            return response
        raise requests.ConnectionError("Delivery test: external HTTP is disabled")

    requests.sessions.Session.request = offline
    source = Path(config.DATABASE_PATH)
    with tempfile.TemporaryDirectory(prefix="carbon-delivery-", dir=os.getenv("CARBON_TEST_TEMP_DIR"), ignore_cleanup_errors=True) as folder:
        sandbox = Path(folder)
        (sandbox / "data").mkdir()
        (sandbox / "database").mkdir()
        shutil.copy2(ROOT / "database/schema_sqlite.sql", sandbox / "database/schema_sqlite.sql")
        snapshot = sandbox / "data/source.db"
        if not source.is_file():
            raise FileNotFoundError(source)
        # Native paths support Windows UNC shares; file://host URIs do not.
        with closing(sqlite3.connect(str(source))) as src:
            src.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst)
        for filename in ("project_presets_shared.json", "project_presets_global.json"):
            original = source.parent / filename
            if original.is_file():
                shutil.copy2(original, sandbox / "data" / filename)
        images = source.parent / "preset_images"
        if images.is_dir():
            shutil.copytree(images, sandbox / "data/preset_images")
        config.BASE_DIR = str(sandbox)
        config.DATABASE_PATH = str(snapshot)
        config.REPORT_DIR = str(sandbox / "reports")
        pdd_templates = sorted(Path(config.REPORT_DOCX_TEMPLATE_DIR).glob("*/pdd.docx"))
        if pdd_templates:
            os.environ.setdefault("TEST_PDD_TEMPLATE", pdd_templates[0].relative_to(
                Path(config.REPORT_DOCX_TEMPLATE_DIR)).as_posix())
        os.environ["CARBON_ACCOUNT_SANDBOX"] = str(snapshot)
        if serve:
            shutil.copytree(ROOT / "static", sandbox / "static")
            import app
            from core.project_manager import init_database
            from core.carbon_calculator import CarbonCalculator
            init_database()
            CarbonCalculator.discover_formulas(config.HOOKS_DIR)
            app.app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
            return
        sys.argv = [str(resolve_test(script))]
        runpy.run_path(sys.argv[0], run_name="__main__")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    parser.add_argument("--serve", action="store_true", help="Isolated browser test server on localhost:5001")
    parser.add_argument("--output", default=None)
    parser.add_argument("--only", nargs="*", help="Run the named test scripts only")
    args = parser.parse_args()
    if args.serve:
        child(None, serve=True)
        return
    if args.child:
        child(args.child)
        return
    import config
    output = Path(args.output or config.data_path("delivery_acceptance"))
    output.mkdir(parents=True, exist_ok=True)
    scripts = sorted({p.name for directory in test_paths() for p in directory.glob("_test_*.py")})
    if args.only:
        unknown = set(args.only) - set(scripts)
        if unknown:
            parser.error("Unknown test scripts: " + ", ".join(sorted(unknown)))
        scripts = [script for script in scripts if script in args.only]
    results = []
    for script in scripts:
        started = time.monotonic()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        with (output / (script + ".log")).open("w", encoding="utf-8") as log:
            try:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                         "--child", script], stdout=log,
                                        stderr=subprocess.STDOUT, env=env, timeout=300)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        item = {"script": script, "exit_code": code,
                "seconds": round(time.monotonic() - started, 2)}
        results.append(item)
        print(json.dumps(item), flush=True)
        (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    raise SystemExit(int(any(r["exit_code"] != 0 for r in results)))


if __name__ == "__main__":
    main()
