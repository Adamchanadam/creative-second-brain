from __future__ import annotations
import argparse
import hashlib, json, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--work-root", type=Path, required=True)
parser.add_argument("--package", type=Path)
args = parser.parse_args()
root = args.work_root.absolute()
root.mkdir(parents=True, exist_ok=False)
if args.package:
    package = args.package.resolve()
else:
    build = root / "candidate"
    subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "build_profiles.py"), "--output", str(build), "--profile", "public-portable"], cwd=ROOT, check=True)
    package = build / "public-portable"

def run(*args: str, cwd: Path, expected: int = 0) -> dict:
    result = subprocess.run([sys.executable, "-B", *args], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise AssertionError(f"expected {expected}, got {result.returncode}: {result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def setup(name: str) -> Path:
    work = root / name
    run(str(package / "scripts" / "setup_workspace.py"), "--profile", "public-portable", "--workspace", str(work), "--vault", "Vault", "--display-name", "Synthetic", cwd=package)
    return work

def sync_plan(work: Path, overrides: list[str], expected: int = 0, package_path: Path | None = None) -> dict:
    active_package = package if package_path is None else package_path
    args = [str(active_package / "scripts" / "sync_shared_surface.py"), "plan", "--workspace", str(work)]
    for path in overrides: args += ["--host-runtime-override", path]
    return run(*args, cwd=work, expected=expected)

def sync_apply(work: Path, plan: dict, overrides: list[str], expected: int = 0, interrupt: str = "", package_path: Path | None = None) -> dict:
    active_package = package if package_path is None else package_path
    args = [str(active_package / "scripts" / "sync_shared_surface.py"), "apply", "--workspace", str(work), "--plan-hash", plan["plan_hash"]]
    for path in overrides: args += ["--host-runtime-override", path]
    if interrupt: args += ["--test-interrupt", interrupt]
    return run(*args, cwd=work, expected=expected)

def future_package(version: str) -> Path:
    target = root / ("future-package-" + version)
    shutil.copytree(package, target)
    profiles = target / "profiles" / "profiles.json"
    profile_data = json.loads(profiles.read_text(encoding="utf-8")); profile_data["version"] = version
    profiles.write_text(json.dumps(profile_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = target / "profile-build.json"
    manifest_data = json.loads(manifest.read_text(encoding="utf-8")); manifest_data["version"] = version; manifest_data["files"]["profiles/profiles.json"] = digest(profiles)
    manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target

work = setup("first")
# A host may preserve the same managed configuration with a different JSON key
# order.  Shared-surface sync must bind the exact on-disk bytes after parsing,
# not mistake formatting for an unrelated configuration change.
config_path = work / "brain.json"; config_value = json.loads(config_path.read_text(encoding="utf-8"))
config_value["schema"] = "creative_second_brain_workspace_v2"
config_value["package_version"] = "1.9.4"
config_value["management"]["package_binding"]["package_version"] = "1.9.4"
config_path.write_text(json.dumps(dict(reversed(list(config_value.items()))), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
private = work / "Vault" / "private.md"; private.write_text("private evidence\n", encoding="utf-8"); private_before = digest(private)
host = work / "tools" / "ask_second_brain.py"; host.write_bytes(host.read_bytes() + b"\n# host-only overlay\n"); host_before = digest(host)
blocked = sync_plan(work, [], expected=2)
assert "unregistered_or_changed_host_runtime" in blocked["blocking_reasons"] and blocked["unregistered_runtime_drift"][0]["path"] == "tools/ask_second_brain.py"
plan = sync_plan(work, ["tools/ask_second_brain.py"])
assert plan["status"] == "planned" and "tools/brain.py" in [row["path"] for row in plan["actions"]]
assert plan["installed_product_version"] == {"before":"1.9.4", "after":"1.9.4", "change":"unchanged_by_shared_entry_sync"}
assert plan["workspace_schema"] == {"before":"creative_second_brain_workspace_v2", "after":"creative_second_brain_workspace_v3"}
applied = sync_apply(work, plan, ["tools/ask_second_brain.py"])
assert applied["status"] == "applied" and digest(host) == host_before and digest(private) == private_before
ready = run(str(work / "tools" / "brain.py"), "doctor", cwd=work)
assert ready["status"] == "ready" and ready["host_runtime_overrides"]["files"] == ["tools/ask_second_brain.py"] and ready["shared_surface"]["status"] == "active"
assert ready["shared_surface"]["source_package_version"] == "1.9.10"
assert json.loads(config_path.read_text(encoding="utf-8"))["schema"] == "creative_second_brain_workspace_v3"
run(str(work / "tools" / "brain.py"), "guide", cwd=work)
assert "目前使用：創造型第二大腦 v1.9.4" in (work / "index.html").read_text(encoding="utf-8")
rogue = work / "tools" / "ask_second_brain.py"; rogue.write_bytes(rogue.read_bytes() + b"# altered after exact host registration\n")
blocked_doctor = run(str(work / "tools" / "brain.py"), "doctor", cwd=work, expected=1)
assert blocked_doctor["status"] == "blocked" and "tools/ask_second_brain.py" in blocked_doctor["modified_runtime_files"]
# A later, approved host edit must be re-registered, not restored or silently
# admitted. Exercise the real daily entry before and after the transaction.
input_file = work / "host-repair-input.txt"
input_file.write_text("A complete synthetic source retained by the text intake.\n", encoding="utf-8")
blocked_entry = run(str(work / "tools" / "brain.py"), "rail", "start", "--text-file", input_file.name, cwd=work, expected=2)
assert blocked_entry["status"] == "blocked"
assert not (work / "outputs" / "compiler_worker_v2" / "_jobs").exists()
blocked_again = sync_plan(work, [], expected=2)
assert blocked_again["unregistered_runtime_drift"][0]["reason"] == "registered_host_runtime_changed"
renewal = sync_plan(work, ["tools/ask_second_brain.py"])
assert [row["path"] for row in renewal["actions"] if row["action"] == "replace"] == ["brain.json"]
config_before = digest(config_path)
rogue.write_bytes(rogue.read_bytes() + b"# change after reviewed plan\n")
stale = sync_apply(work, renewal, ["tools/ask_second_brain.py"], expected=2)
assert stale["status"] == "blocked" and digest(config_path) == config_before
renewal = sync_plan(work, ["tools/ask_second_brain.py"])
reviewed_host = digest(rogue)
sync_apply(work, renewal, ["tools/ask_second_brain.py"])
assert digest(rogue) == reviewed_host and digest(private) == private_before
assert run(str(work / "tools" / "brain.py"), "doctor", cwd=work)["status"] == "ready"
assert run(str(work / "tools" / "brain.py"), "context", cwd=work)["status"] == "ready"
started = run(str(work / "tools" / "brain.py"), "rail", "start", "--text-file", input_file.name, cwd=work)
assert started["action"] == "prepare_semantic_plan"
assert sync_plan(work, ["tools/ask_second_brain.py"])["writes"] == 0
shared = work / ".agents" / "skills" / "brain-ask" / "SKILL.md"; shared.write_bytes(shared.read_bytes() + b"\n<!-- foreign shared edit -->\n")
conflict = sync_plan(work, ["tools/ask_second_brain.py"], expected=2)
assert "shared_surface_conflicts" in conflict["blocking_reasons"]

recovery = setup("recovery")
recovery_host = recovery / "tools" / "ask_second_brain.py"; recovery_host.write_bytes(recovery_host.read_bytes() + b"\n# host-only overlay\n")
recovery_plan = sync_plan(recovery, ["tools/ask_second_brain.py"])
sync_apply(recovery, recovery_plan, ["tools/ask_second_brain.py"], expected=2, interrupt="after_target")
transactions = sorted((recovery / ".brain-management" / "transactions").iterdir())
assert len(transactions) == 1
run(str(package / "scripts" / "manage_workspace.py"), "rollback", "--workspace", str(recovery), "--transaction-id", transactions[0].name, cwd=recovery)
retry = sync_plan(recovery, ["tools/ask_second_brain.py"])
sync_apply(recovery, retry, ["tools/ask_second_brain.py"])
assert run(str(recovery / "tools" / "brain.py"), "doctor", cwd=recovery)["status"] == "ready"

# A persisted binding older than the installed product is a blocked
# configuration, not a valid partial synchronization state.
inverted = setup("inverted")
inverted_config = json.loads((inverted / "brain.json").read_text(encoding="utf-8"))
shared_paths = ["tools/brain.py"] + sorted(path for path in inverted_config["managed_files"] if path.startswith(".agents/skills/brain-") and path.endswith("/SKILL.md"))
inverted_config["shared_surface"] = {"schema":"creative_second_brain_shared_surface_v1", "package_binding":{"package_version":"1.9.9"}, "files":{path:digest(inverted / path) for path in shared_paths}}
(inverted / "brain.json").write_text(json.dumps(inverted_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
inverted_doctor = run(str(inverted / "tools" / "brain.py"), "doctor", cwd=inverted, expected=1)
assert inverted_doctor["status"] == "blocked" and inverted_doctor["configuration_error"] == "shared surface source version predates installed product version"

# A root version cannot be hand-raised beyond the version that installed the
# managed runtime, and neither doctor nor a supported home regeneration may
# treat that forged claim as a valid product upgrade.
false_root = setup("false-root-version")
false_config = json.loads((false_root / "brain.json").read_text(encoding="utf-8")); false_config["package_version"] = "1.9.11"; (false_root / "brain.json").write_text(json.dumps(false_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
false_home_before = digest(false_root / "index.html")
false_doctor = run(str(false_root / "tools" / "brain.py"), "doctor", cwd=false_root, expected=1)
assert false_doctor["configuration_error"] == "workspace product version disagrees with installed management binding"
run(str(false_root / "tools" / "brain.py"), "guide", cwd=false_root, expected=2)
assert digest(false_root / "index.html") == false_home_before

# A bounded shared sync may advance entry provenance while leaving the
# installed product at 1.9.10, but an older entry package cannot replay and
# replace that newer shared binding.
future = future_package("1.9.11")
future_shared = setup("future-shared-source")
future_plan = sync_plan(future_shared, [], package_path=future)
assert future_plan["installed_product_version"]["after"] == "1.9.10"
sync_apply(future_shared, future_plan, [], package_path=future)
future_config = json.loads((future_shared / "brain.json").read_text(encoding="utf-8"))
assert future_config["package_version"] == "1.9.10" and future_config["shared_surface"]["package_binding"]["package_version"] == "1.9.11"
future_before = digest(future_shared / "brain.json")
future_blocked = sync_plan(future_shared, [], expected=2)
assert future_blocked["reason"] == "shared entry source version predates existing shared surface" and digest(future_shared / "brain.json") == future_before
print(json.dumps({"status":"passed","checks":["unregistered host drift blocks","exact host overlay survives","private vault untouched","foreign shared edit blocks","interrupted transaction rolls back and retries"]}, ensure_ascii=False))




