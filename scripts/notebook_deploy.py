"""Deploy the Estate Command Center from a Databricks notebook, with no CLI, Git or laptop.

The deployment guide's notebook cell finds the repo you uploaded (GitHub's "Download ZIP" file,
unpacked by the cell, or a workspace folder) and runs this script:

    python scripts/notebook_deploy.py --source <repo folder> --target customer \
        --set DATABRICKS_WAREHOUSE_ID=... --set FINOPS_GOLD_SCHEMA=main.finops_gold [--gold-sync]

It expects ECC_HOST and ECC_TOKEN in the environment (the notebook passes its own workspace URL
and token, so the deploy runs as the person running the notebook). It then:
  1. copies the repo to a scratch folder on the compute (workspace notebooks are exported as
     source, because they can't be read as plain files),
  2. writes the --set values into app.yaml in that copy (your settings live in the notebook cell,
     so nothing in the uploaded repo needs editing),
  3. picks a serverless SQL warehouse if none was given,
  4. unpacks the official Databricks CLI release bundled in vendor/databricks-cli (checked against
     the release's SHA-256 sums), so Databricks never needs to reach GitHub,
  5. runs `bundle deploy` and `bundle run estate` for the chosen target,
  6. grants the app's service principal CAN USE on the warehouse,
  7. optionally runs the gold-sync job once and turns its nightly schedule on,
  8. prints the app's service principal (for the system-table grants) and the app URL.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

# A script started from a notebook cell inherits the notebook runtime's environment. Without
# this, the Databricks SDK thinks it IS the notebook and tries to start Spark, which fails.
os.environ.pop("DATABRICKS_RUNTIME_VERSION", None)

CLI_VERSION = "1.25.0"


def fail(msg):
    print(f"\n❌ {msg}", flush=True)
    sys.exit(1)


def copy_repo(w, src, dest):
    src = src.rstrip("/")
    if not src.startswith("/Workspace/"):
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", ".databricks", "__pycache__"))
        return dest
    from databricks.sdk.service.workspace import ExportFormat, ObjectType
    ext = {"PYTHON": ".py", "SQL": ".sql", "SCALA": ".scala", "R": ".r"}

    def walk(ws_dir, local_dir):
        os.makedirs(local_dir, exist_ok=True)
        for obj in w.workspace.list(ws_dir):
            name = os.path.basename(obj.path)
            if name in (".git", ".databricks", "__pycache__"):
                continue
            target = os.path.join(local_dir, name)
            if obj.object_type in (ObjectType.DIRECTORY, ObjectType.REPO):
                walk(obj.path, target)
            elif obj.object_type == ObjectType.NOTEBOOK:
                e = ext.get(obj.language.value if obj.language else "PYTHON", ".py")
                data = w.workspace.download(obj.path, format=ExportFormat.SOURCE).read()
                with open(target if target.endswith(e) else target + e, "wb") as f:
                    f.write(data)
            elif obj.object_type == ObjectType.FILE:
                shutil.copyfile("/Workspace" + obj.path, target)

    print(f"▶ Copying the repo from {src} …", flush=True)
    walk(src[len("/Workspace"):], dest)
    return dest


def set_env(app_yaml, name, value):
    """Set `- name: NAME / value: "..."` in app.yaml, adding the entry if it isn't there."""
    if not re.fullmatch(r"[A-Z0-9_]+", name):
        fail(f"'{name}' isn't a valid app.yaml setting name (use the UPPER_CASE names from app.yaml).")
    quoted = json.dumps(str(value))
    pat = re.compile(r"(- name: " + re.escape(name) + r"\s*\n\s*value: )(\"[^\"]*\"|'[^']*'|[^\n]*)")
    new, n = pat.subn(lambda m: m.group(1) + quoted, app_yaml)
    if n:
        return new
    return app_yaml.rstrip("\n") + f"\n  - name: {name}\n    value: {quoted}\n"


def run(cmd, cwd, env):
    print("▶ " + " ".join(c for c in cmd if not c.startswith("--var=")), flush=True)
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(line, end="", flush=True)
    return proc.wait()


def recover_failed_new_app(w, name):
    import time
    from databricks.sdk.service.apps import ComputeState
    try:
        app = w.apps.get(name=name)
    except Exception:  # noqa: BLE001
        return False
    if getattr(app.compute_status, "state", None) != ComputeState.ERROR or app.active_deployment:
        return False
    print(f"  the new app {name} failed to start ({(app.compute_status.message or '')[:120]})")
    print("  it has never been deployed, so deleting it and creating it again")
    w.apps.delete(name=name)
    for _ in range(60):
        try:
            w.apps.get(name=name)
            time.sleep(5)
        except Exception:  # noqa: BLE001
            break
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", default="customer")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--var", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--gold-sync", action="store_true")
    a = ap.parse_args()

    host = os.environ.get("ECC_HOST", "").rstrip("/")
    token = os.environ.get("ECC_TOKEN", "")
    if not host or not token:
        fail("ECC_HOST / ECC_TOKEN are not set. Run this from the notebook cell in the deployment guide.")
    if a.target not in ("customer", "customer-delta"):
        fail(f"TARGET must be \"customer\" or \"customer-delta\", not {a.target!r}.")

    # A current Databricks SDK, installed into a scratch folder so the notebook's own
    # environment is left alone (older serverless images ship an SDK without the Apps API).
    work = tempfile.mkdtemp(prefix="ecc_deploy_")
    pylib = os.path.join(work, "pylib")
    print("▶ Installing the Databricks SDK the deploy needs …", flush=True)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                        "--target", pylib, "databricks-sdk>=0.65"], capture_output=True, text=True)
    if r.returncode != 0:
        fail("Couldn't install databricks-sdk from PyPI:\n" + (r.stdout + r.stderr)[-1500:])
    sys.path.insert(0, pylib)
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(host=host, token=token, auth_type="pat")

    # 1. Copy the repo.
    repo = copy_repo(w, a.source, os.path.join(work, "repo"))

    # 2. Settings -> app.yaml (scratch copy only).
    app_yaml_path = os.path.join(repo, "app.yaml")
    app_yaml = open(app_yaml_path).read()
    settings = {}
    for item in a.set:
        k, _, v = item.partition("=")
        if k.strip() and v.strip():
            settings[k.strip()] = v.strip()

    # 3. A warehouse, if live mode is on and none was given.
    live = settings.get("USE_LIVE", "true").lower() != "false"
    if live and not settings.get("DATABRICKS_WAREHOUSE_ID"):
        whs = [x for x in w.warehouses.list() if x.enable_serverless_compute]
        if not whs:
            fail("No serverless SQL warehouse found that you can use. Set WAREHOUSE_ID in the cell.")
        whs.sort(key=lambda x: (str(x.state) != "State.RUNNING", x.name or ""))
        settings["DATABRICKS_WAREHOUSE_ID"] = whs[0].id
        print(f"▶ Using SQL warehouse '{whs[0].name}' ({whs[0].id}). Set WAREHOUSE_ID in the cell to use another.")
    for k, v in settings.items():
        app_yaml = set_env(app_yaml, k, v)
    open(app_yaml_path, "w").write(app_yaml)
    print("▶ Settings written to app.yaml:")
    for k, v in settings.items():
        print(f"    {k:<28} {v}")
    if live and not settings.get("FINOPS_GOLD_SCHEMA"):
        print("  ! GOLD_SCHEMA is blank, so the app will show demo data with a 'finish setup' banner "
              "until you set it and run the cell again.")

    # 4. The bundled Databricks CLI.
    arch = "arm64" if platform.machine() in ("aarch64", "arm64") else "amd64"
    name = f"databricks_cli_{CLI_VERSION}_linux_{arch}.zip"
    vend = os.path.join(repo, "vendor", "databricks-cli")
    zip_path = os.path.join(vend, name)
    if not os.path.exists(zip_path):
        fail(f"The repo has no bundled CLI ({name} in vendor/databricks-cli). Download the repo ZIP "
             "from GitHub again and re-upload it.")
    sums = dict(line.split()[::-1] for line in open(os.path.join(vend, "SHA256SUMS")) if line.strip())
    if sums.get(name) != hashlib.sha256(open(zip_path, "rb").read()).hexdigest():
        fail(f"The bundled CLI {name} doesn't match its published SHA-256 checksum. "
             "Download the repo ZIP again and re-upload it.")
    cli_dir = os.path.join(work, "cli")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(cli_dir)
    cli = os.path.join(cli_dir, "databricks")
    os.chmod(cli, 0o755)
    print(f"▶ Using the bundled Databricks CLI v{CLI_VERSION} ({arch}), checksum verified.")

    # 5. Deploy + start, signed in as the notebook user through a throwaway config file.
    cfg_file = os.path.join(work, "databrickscfg")
    with open(cfg_file, "w") as f:
        f.write(f"[notebook]\nhost = {host}\ntoken = {token}\n")
    os.chmod(cfg_file, 0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABRICKS_", "ECC_"))}
    env.update({"DATABRICKS_CONFIG_FILE": cfg_file, "HOME": work,
                "PATH": os.pathsep.join([cli_dir, env.get("PATH", "")])})
    # Every bundle command gets the SAME vars, so they all resolve to the same app and jobs.
    common = ["-t", a.target, "-p", "notebook"] + [f"--var={v}" for v in a.var if v.split("=", 1)[-1]]
    app_name = next((v.split("=", 1)[1] for v in a.var if v.startswith("app_name=")),
                    "estate-command-center")
    try:
        rc = run([cli, "bundle", "deploy", "--auto-approve"] + common, repo, env)
        # A brand-new app is sometimes created before its new Lakebase instance is ready to
        # bind it, and is then stuck in a fatal ERROR state. If it has never been deployed
        # (nothing to lose), delete it and deploy again.
        if recover_failed_new_app(w, app_name):
            rc = run([cli, "bundle", "deploy", "--auto-approve"] + common, repo, env)
        if rc != 0:
            fail("The bundle deploy stopped with an error. The lines just above say what went wrong.")
        if run([cli, "bundle", "run", "estate"] + common, repo, env) != 0:
            fail("Starting the app stopped with an error. The lines just above say what went wrong.")

        app = w.apps.get(name=app_name)
        sp = app.service_principal_client_id or app.service_principal_name

        # 6. Warehouse CAN USE for the app (best-effort).
        wh = settings.get("DATABRICKS_WAREHOUSE_ID")
        if live and wh and sp:
            try:
                from databricks.sdk.service import sql
                w.warehouses.update_permissions(warehouse_id=wh, access_control_list=[
                    sql.WarehouseAccessControlRequest(service_principal_name=sp,
                                                      permission_level=sql.WarehousePermissionLevel.CAN_USE)])
                print(f"▶ Granted the app CAN USE on warehouse {wh}.")
            except Exception as e:  # noqa: BLE001
                print(f"  ! Couldn't grant CAN USE on warehouse {wh} ({str(e)[:100]}). Someone with "
                      "CAN MANAGE on it runs the warehouse snippet in the deployment guide (Step ④).")

        # 7. Gold sync once + nightly schedule on.
        if a.gold_sync and live and settings.get("FINOPS_GOLD_SCHEMA"):
            if run([cli, "bundle", "run", "finops_gold_sync"] + common, repo, env) != 0:
                print("  ! The gold-sync run failed. Check the lines above (usually a missing system-table "
                      "grant for you), fix it and run the cell again.")
            else:
                job_name = f"{app_name}-gold-sync"
                from databricks.sdk.service.jobs import CronSchedule, JobSettings, PauseStatus
                for j in w.jobs.list(name=job_name):
                    s = j.settings
                    if s and s.schedule and s.schedule.pause_status == PauseStatus.PAUSED:
                        w.jobs.update(job_id=j.job_id, new_settings=JobSettings(schedule=CronSchedule(
                            quartz_cron_expression=s.schedule.quartz_cron_expression,
                            timezone_id=s.schedule.timezone_id, pause_status=PauseStatus.UNPAUSED)))
                        print(f"▶ Turned on the nightly schedule for {job_name}.")
    finally:
        os.remove(cfg_file)

    # 8. What's next.
    print(f"\n✅ Estate Command Center is deployed: {app.url}")
    print(f"   The app's service principal is {sp}. Use it in the system-table grants (Step ④).")


if __name__ == "__main__":
    main()
