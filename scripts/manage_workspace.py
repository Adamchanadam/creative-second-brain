"""Transactional manager for Public runtime ownership in an existing workspace."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, sys, uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any
if os.name == "nt":
    import msvcrt
else:
    import fcntl
import stat
from package_paths import assert_plain_path
from package_inventory import load_inventory
from setup_workspace import entry_contract, plain_child, skill_entry_payloads, workspace_config_bytes
from verify_package import verify

PACKAGE=Path(__file__).resolve().parents[1]
INVENTORY_PATH=PACKAGE/"spec"/"package-inventory.json"
SCHEMA="creative_second_brain_workspace_manager_v2"
CONFIG="creative_second_brain_workspace_v3"
LEGACY_CONFIGS={"creative_second_brain_workspace_v2"}
PACKAGE_SCHEMA="creative_second_brain_portable_v1"
TX_SCHEMA="creative_second_brain_management_transaction_v2"
CP_SCHEMA="creative_second_brain_management_checkpoint_v2"
CONTROL=".brain-management"
GUARD_SCHEMA="creative_second_brain_manager_guard_v1"
ADMISSION_SCHEMA="creative_second_brain_management_admission_v1"
ADOPTION_RESOLUTION_SCHEMA="creative_second_brain_adoption_resolution_v1"
ADOPTION_RESOLUTION_CLASS="approved_public_core_ownership_takeover"
UPDATE_RESOLUTION_SCHEMA="creative_second_brain_update_resolution_v1"
UPDATE_RESOLUTION_CLASS="approved_public_managed_runtime_replacement"
UPDATE_RECONCILIATION_CLASS="approved_public_existing_runtime_reconciliation"
UPDATE_HOST_ADAPTATION_CLASS="approved_host_runtime_adaptation"
HOST_OVERLAY_SCHEMA="creative_second_brain_host_runtime_overrides_v1"
TERMINAL={"completed"}
JOB_SCHEMA="compiler_worker_v2_job_v1"
COMPILER_JOB_STATUSES={"accepted","queued","running","awaiting_imagegen","blocked","committing","completed"}
NON_JOB={"infographic_revision_plan_v1":"infographic_revision_plan"}
SEMVER=re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")

class ManagerError(ValueError): pass
class TransactionInterrupted(ManagerError):
    def __init__(self, transaction_id, reason):
        super().__init__(reason)
        self.transaction_id=transaction_id
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
def sha_b(v): return hashlib.sha256(v).hexdigest()
def sha(p): return sha_b(p.read_bytes())
def plain(p): assert_plain_path(p); return p
def bound_workspace(value,workspace): return isinstance(value,str) and bool(value.strip()) and Path(value).is_absolute() and Path(value).resolve()==workspace.resolve()
def obj(p,label):
    plain(p)
    try: v=json.loads(p.read_text(encoding="utf-8"))
    except (OSError,UnicodeError,json.JSONDecodeError) as e: raise ManagerError(f"{label} is unreadable or malformed") from e
    if not isinstance(v,dict): raise ManagerError(f"{label} is not an object")
    return v
def rel(s):
    p=PurePosixPath(s)
    if not s or p.is_absolute() or ".." in p.parts or "\\" in s or ":" in s or p.as_posix()!=s or s.startswith(CONTROL+"/"): raise ManagerError("unsafe managed relative path")
    return p
def child(root,s):
    p=root.joinpath(*rel(s).parts); plain(p)
    try: p.resolve(strict=False).relative_to(root.resolve())
    except ValueError as e: raise ManagerError("managed path escapes root") from e
    return p
def file(p,label):
    plain(p)
    if not p.is_file(): raise ManagerError(f"{label} is not a regular file")
def write_new(p,v):
    p.parent.mkdir(parents=True,exist_ok=True); plain(p.parent); data=canon(v)+b"\n"
    try:
        with p.open("xb") as h: h.write(data); h.flush(); os.fsync(h.fileno())
    except FileExistsError as e: raise ManagerError("immutable transaction artifact already exists") from e
    if sha(p)!=sha_b(data): raise ManagerError("transaction artifact hash mismatch")
    return sha_b(data)
def inventory():
    try: v=load_inventory(PACKAGE)
    except ValueError as error: raise ManagerError(str(error)) from error
    c=v.get("daily_entry_contract"); r=v.get("runtime")
    if not isinstance(c,dict) or not isinstance(c.get("managed_entries"),list) or not isinstance(r,dict) or r.get("exported_prefixes")!=["workspace/"] or not isinstance(v.get("private_exclusions"),list): raise ManagerError("package ownership inventory is invalid")
    entries=c["managed_entries"]
    registry=obj(PACKAGE/"spec"/"command-registry.json","command registry").get("commands")
    if not isinstance(registry,dict): raise ManagerError("command registry is invalid")
    expected={key:("compatibility_alias" if definition.get("alias_for") else "canonical") for key,definition in registry.items() if isinstance(definition,dict) and isinstance(definition.get("menu_order"),int)}
    actual={entry.get("command"):entry.get("role") for entry in entries if isinstance(entry,dict)}
    if (any(not isinstance(x,dict) or not isinstance(x.get("skill"),str) or not x["skill"].startswith("brain-")
            or x.get("role") not in {"canonical", "compatibility_alias"} for x in entries)
            or len(actual)!=len(entries) or len({x["skill"] for x in entries})!=len(entries)
            or actual!=expected): raise ManagerError("package daily entry inventory is invalid")
    return v
def version():
    v=obj(PACKAGE/"profiles"/"profiles.json","package profile").get("version")
    version_key(v)
    return v
def version_key(value):
    match=SEMVER.fullmatch(value) if isinstance(value,str) else None
    if not match: raise ManagerError("product version is invalid")
    return tuple(int(part) for part in match.groups())
def binding():
    return {"package_version":version(),"profile_build_sha256":sha(PACKAGE/"profile-build.json"),"inventory_sha256":sha(INVENTORY_PATH),"runtime_provenance_sha256":sha(PACKAGE/"runtime"/"snapshot-provenance.json")}
def files():
    verify(PACKAGE); inv=inventory(); prov=obj(PACKAGE/"runtime"/"snapshot-provenance.json","runtime provenance")
    if prov.get("schema")!="creative_second_brain_runtime_core_v1" or prov.get("ownership_inventory_sha256")!=sha(INVENTORY_PATH) or not isinstance(prov.get("runtime_files"),list): raise ManagerError("runtime provenance is not inventory-bound")
    out={}
    for item in prov["runtime_files"]:
        if isinstance(item,str) and item.startswith("workspace/"):
            p=child(PACKAGE/"runtime",item); file(p,"package runtime"); out[item[10:]]=p.read_bytes()
    p=PACKAGE/"scripts"/"brain.py"; file(p,"brain entry"); out["tools/brain.py"]=p.read_bytes()
    made=skill_entry_payloads("public-portable"); want={x["skill"] for x in inv["daily_entry_contract"]["managed_entries"]}
    actual={k.split("/")[2] for k in made}
    if actual!=want: raise ManagerError("generated entries do not match ownership inventory")
    out.update(made)
    return out,{"hashes":{k:sha_b(v) for k,v in out.items()},"binding":binding()}
def prefixes(vault):
    return sorted(set([*inventory()["private_exclusions"],vault.rstrip("/")+"/",vault.rstrip("/")+"/.obsidian/"]))
def private(path,scopes): return any(path.casefold()==x.rstrip("/").casefold() or path.casefold().startswith(x.casefold()) for x in scopes)
def vault(workspace,value):
    p=plain_child(workspace,value)
    if not p.is_dir(): raise ManagerError("existing configured vault is required")
    return p.relative_to(workspace).as_posix()
def config(workspace,need):
    p=workspace/"brain.json"
    if not p.exists():
        if need: raise ManagerError("workspace is not managed; use plan-adopt")
        return None
    v=obj(p,"brain.json")
    if v.get("schema") not in LEGACY_CONFIGS|{CONFIG} or v.get("package_schema")!=PACKAGE_SCHEMA:
        if need: raise ManagerError("brain.json is not managed by this release")
        return None
    if not isinstance(v.get("managed_files"),dict): raise ManagerError("brain.json lacks managed ownership")
    return v
def host_adaptation_eligible(path,vault_name):
    return (isinstance(path,str) and path not in {"brain.json","tools/brain.py"}
            and not path.startswith(".agents/skills/brain-")
            and not private(path,prefixes(vault_name)))
def carried_host_overrides(existing,hashes,vault_name):
    """Return only exact, current-candidate host adaptations already in brain.json."""
    overlays=existing.get("host_runtime_overrides") if isinstance(existing,dict) else None
    if overlays is None: return {}
    if not isinstance(overlays,dict) or overlays.get("schema")!=HOST_OVERLAY_SCHEMA or not isinstance(overlays.get("files"),dict) or not overlays["files"]:
        return None
    result={}
    for path,record in overlays["files"].items():
        base=record.get("base_sha256") if isinstance(record,dict) else None
        host=record.get("host_sha256") if isinstance(record,dict) else None
        if (not host_adaptation_eligible(path,vault_name) or hashes.get(path)!=base
            or not hexhash(host) or host==base):
            return None
        result[path]=host
    return dict(sorted(result.items()))
def make_config(existing,vault_name,name,hashes,host_overrides=None):
    result=dict(existing or {})
    result.pop("shared_surface",None)
    result.pop("host_runtime_overrides",None)
    host_overrides=dict(host_overrides or {})
    if host_overrides:
        files={}
        for path,host in sorted(host_overrides.items()):
            base=hashes.get(path)
            if (not host_adaptation_eligible(path,vault_name) or not hexhash(base)
                or not hexhash(host) or host==base):
                raise ManagerError("host runtime adaptation is not candidate-bound")
            files[path]={"base_sha256":base,"host_sha256":host}
        result["host_runtime_overrides"]={"schema":HOST_OVERLAY_SCHEMA,"files":files}
    result.update({"schema":CONFIG,"package_schema":PACKAGE_SCHEMA,"package_version":version(),"profile":"public-portable","display_name":name,"vault":vault_name,"vault_name":Path(vault_name).name,"runtime_python":str((existing or {}).get("runtime_python") or sys.executable),"managed_files":dict(sorted(hashes.items())),"entry_contract":entry_contract("public-portable"),"management":{"schema":SCHEMA,"control_directory":CONTROL,"package_binding":binding(),"private_exclusions":prefixes(vault_name)}})
    return result
def is_link(meta):
    # Kept here, not imported: an older installed updater has already loaded its own
    # package_paths before this candidate manager runs, so new helpers there are absent.
    return stat.S_ISLNK(meta.st_mode) or (bool(getattr(meta,"st_file_attributes",0)&0x400) and bool(getattr(meta,"st_reparse_tag",0)&0x20000000))   # name-surrogate: symlink, junction or third-party redirect
def tree(p):
    if not p.exists(): return {"exists":False,"files":0,"sha256":""}
    plain(p)
    if p.is_file(): return {"exists":True,"files":1,"sha256":sha(p)}
    # Private content is only proven unchanged, never written: a user's own link or
    # junction inside it is recorded by its target and not followed, so it neither
    # blocks an update nor pulls data from outside the workspace into the proof.
    found=[]; stack=[p]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for e in entries:
                x=Path(e.path)
                if is_link(x.lstat()): found.append((x,"link"))
                elif e.is_dir(follow_symlinks=False): found.append((x,"dir")); stack.append(x)
                else: found.append((x,"file"))
    rows=[]; files=0
    for x,kind in sorted(found,key=lambda r:(r[0].as_posix().casefold(),r[0].as_posix())):   # exact name breaks case-fold ties (ß/ss)
        if kind=="link":
            try: target=os.readlink(x)
            except OSError: target="reparse-tag:"+hex(getattr(x.lstat(),"st_reparse_tag",0))   # third-party redirect without a readable target
            rows.append(x.relative_to(p).as_posix()+":link:"+sha_b(target.encode("utf-8","surrogatepass")))
        elif kind=="file" and x.is_file(): rows.append(x.relative_to(p).as_posix()+":"+sha(x)); files+=1
    return {"exists":True,"files":files,"sha256":sha_b("\n".join(rows).encode())}
def research_records(workspace,vault_name):
    registry=child(workspace,vault_name)/"_registry"/"research-programs"; active=[]; invalid=[]; topics=[]; locks=[]
    if not registry.exists(): return {"topics":topics,"active":active,"uninspectable":invalid,"locks":locks}
    if not registry.is_dir(): return {"topics":topics,"active":active,"uninspectable":[{"path":registry.relative_to(workspace).as_posix(),"reason":"research_registry_is_not_a_directory"}],"locks":locks}
    plain(registry)
    for directory in sorted(registry.iterdir(),key=lambda p:p.name.casefold()):
        plain(directory); base=directory.relative_to(workspace).as_posix()
        if not directory.is_dir() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",directory.name):
            invalid.append({"path":base,"reason":"research_topic_path_is_unknown"}); continue
        lock=directory/".program.lock"
        if lock.exists(): plain(lock); locks.append(lock.relative_to(workspace).as_posix())
        topic_path,state_path=directory/"topic.json",directory/"state.json"
        try:
            topic,state=obj(topic_path,"research topic"),obj(state_path,"research state")
            topic_sha,state_sha=sha(topic_path),sha(state_path)
            if topic.get("schema_version")!="research_program_v1" or topic.get("topic_id")!=directory.name:
                raise ManagerError("research topic contract is unknown")
            if state.get("schema_version")!="research_program_v1": raise ManagerError("research state contract is unknown")
            required=("accepted_cases","withdrawn_cases","active_cases","discovery_slots","rejected_cases","rejected_discoveries","day_runs","day_run_history","manual_allowances","runs")
            if any(key not in state for key in required) or not isinstance(state["active_cases"],dict) or not isinstance(state["discovery_slots"],dict) or not isinstance(state["runs"],dict): raise ManagerError("research state has an unknown shape")
            runs=state["runs"]
            for run_id,run in runs.items():
                if not isinstance(run_id,str) or not isinstance(run,dict) or run.get("run_id")!=run_id or not isinstance(run.get("actions"),list): raise ManagerError("research run identity is invalid")
                for action in run["actions"]:
                    if not isinstance(action,dict) or not isinstance(action.get("action_id"),str) or not isinstance(action.get("status"),str): raise ManagerError("research action identity is invalid")
                    if action["status"]=="reserved": active.append({"kind":"run_action_reserved","topic_id":directory.name,"run_id":run_id,"action_id":action["action_id"],"topic_sha256":topic_sha,"state_sha256":state_sha})
                    elif action["status"]!="completed": raise ManagerError("research action status is unknown")
            for case_id,case in state["active_cases"].items():
                if not isinstance(case_id,str) or not isinstance(case,dict) or case.get("case_id")!=case_id or not isinstance(case.get("run_id"),str) or case["run_id"] not in runs or case.get("status") not in {"reserved","committing"}: raise ManagerError("research active case owner is invalid")
                active.append({"kind":"active_case_"+str(case["status"]),"topic_id":directory.name,"run_id":case["run_id"],"case_id":case_id,"topic_sha256":topic_sha,"state_sha256":state_sha})
            terminal_case_records = []
            for field in ("accepted_cases", "rejected_cases", "withdrawn_cases"):
                records = state.get(field)
                if not isinstance(records, list):
                    raise ManagerError("research terminal case history is invalid")
                terminal_case_records.extend(record for record in records if isinstance(record, dict))
            for discovery_id,slot in state["discovery_slots"].items():
                if not isinstance(discovery_id,str) or not isinstance(slot,dict) or slot.get("discovery_id")!=discovery_id or not isinstance(slot.get("run_id"),str) or slot["run_id"] not in runs:
                    raise ManagerError("research discovery owner is invalid")
                status = slot.get("status")
                if status == "reserved":
                    active.append({"kind":"discovery_reserved","topic_id":directory.name,"run_id":slot["run_id"],"discovery_id":discovery_id,"topic_sha256":topic_sha,"state_sha256":state_sha})
                    continue
                if status != "bound" or not isinstance(slot.get("case_id"),str) or not slot["case_id"].strip() or not isinstance(slot.get("source_identity"),str) or not slot["source_identity"].strip():
                    raise ManagerError("research discovery owner is invalid")
                case_records = [*state["active_cases"].values(), *terminal_case_records]
                if not any(
                    isinstance(case, dict)
                    and case.get("case_id") == slot["case_id"]
                    and case.get("run_id") == slot["run_id"]
                    and case.get("source_identity") == slot["source_identity"]
                    for case in case_records
                ):
                    raise ManagerError("research bound discovery has no matching case transfer")
            topics.append({"topic_id":directory.name,"topic_path":topic_path.relative_to(workspace).as_posix(),"state_path":state_path.relative_to(workspace).as_posix(),"topic_sha256":topic_sha,"state_sha256":state_sha})
        except ManagerError as error: invalid.append({"path":base,"reason":str(error)})
    return {"topics":topics,"active":active,"uninspectable":invalid,"locks":locks}
def writer(workspace,vault_name):
    root=workspace/"outputs"/"compiler_worker_v2"/"_jobs"; unfinished=[]; completed=0; invalid=[]; foreign=[]
    if root.is_dir():
        plain(root)
        for p in sorted(root.glob("*.json"),key=lambda q:q.name.casefold()):
            plain(p); path=p.relative_to(workspace).as_posix()
            try: row=json.loads(p.read_text(encoding="utf-8"))
            except (OSError,UnicodeError,json.JSONDecodeError): invalid.append({"path":path,"reason":"unreadable_or_invalid_json"}); continue
            if not isinstance(row,dict): invalid.append({"path":path,"reason":"job_record_is_not_an_object"}); continue
            sv=row.get("schema_version")
            if sv in NON_JOB: foreign.append({"path":path,"schema_version":sv,"owner":NON_JOB[sv]}); continue
            if sv!=JOB_SCHEMA: invalid.append({"path":path,"reason":"unknown_or_incompatible_job_schema"}); continue
            jid,status=row.get("job_id"),row.get("status")
            if not isinstance(jid,str) or not jid.strip() or not isinstance(status,str) or not status.strip(): invalid.append({"path":path,"reason":"job_record_missing_required_state"}); continue
            if status not in COMPILER_JOB_STATUSES: invalid.append({"path":path,"reason":"unknown_compiler_job_status"}); continue
            if status in TERMINAL: completed+=1
            else: unfinished.append({"path":path,"job_id":jid,"status":status})
    research=research_records(workspace,vault_name)
    facts={"unfinished":unfinished,"completed":completed,"recognized_non_job":foreign,"uninspectable_jobs":invalid,"research":research}
    blocked=bool(invalid or research["locks"] or research["uninspectable"] or research["active"])
    reasons=(["compiler_job_uninspectable"] if invalid else [])+(["research_program_lock_present"] if research["locks"] else [])+(["research_record_uninspectable"] if research["uninspectable"] else [])+(["research_active_hard_gate"] if research["active"] else [])
    return {"status":"blocked" if blocked else "compatible","reason_codes":reasons,"assessment_sha256":sha_b(canon(facts)),"active_lock_paths":research["locks"],"unfinished_jobs":unfinished,"completed_job_count":completed,"recognized_non_job_records":foreign,"uninspectable_jobs":invalid,"research_topics":research["topics"],"active_research":research["active"],"uninspectable_research":research["uninspectable"],"continuation":{"terminal_statuses":["completed"],"identity_preserving":not invalid,"unfinished_job_count":len(unfinished)},"lock_absence_is_not_quiescence_proof":True}
def proof(path,workspace,assessment):
    if path is None: return {"status":"required","reason":"first adoption needs explicit writer quiescence proof"}
    v=obj(path,"bootstrap quiescence proof")
    if v.get("schema")!="brain_bootstrap_quiescence_proof_v1" or not bound_workspace(v.get("workspace"),workspace) or v.get("writers_quiescent") is not True or v.get("writer_assessment_sha256")!=assessment["assessment_sha256"] or not isinstance(v.get("evidence"),list) or not v["evidence"] or not isinstance(v.get("checked_at"),str) or not v["checked_at"].strip(): return {"status":"invalid","reason":"proof does not bind current writer assessment"}
    return {"status":"proven","path":str(path.absolute()),"sha256":sha(path)}
def adoption_resolution(path,workspace,vault_name,package_binding,actions):
    """Authorize only the exact first-adoption conflicts that were reviewed.

    The resolution is intentionally an external, immutable review record.  It
    cannot add a target, bypass a private scope, or authorize a changed file:
    every entry must match the current conflict's path and both byte hashes.
    """
    conflicts=[row for row in actions if row["action"]=="conflict"]
    if not conflicts:
        if path is not None: raise ManagerError("adoption resolution was supplied but this plan has no conflicts")
        return {"status":"not_required"}
    expected={row["path"]:row for row in conflicts}
    required=[{"path":row["path"],"before_sha256":row["actual_sha256"],"after_sha256":row["expected_sha256"],"file_role":row["file_role"]} for row in conflicts]
    if path is None: return {"status":"required","schema":ADOPTION_RESOLUTION_SCHEMA,"required_entries":required}
    path=path.absolute(); plain(path); file(path,"adoption resolution")
    value=obj(path,"adoption resolution")
    resolution_workspace=value.get("workspace")
    if (value.get("schema")!=ADOPTION_RESOLUTION_SCHEMA or not bound_workspace(resolution_workspace,workspace)
        or value.get("vault")!=vault_name or value.get("package_binding")!=package_binding):
        raise ManagerError("adoption resolution does not bind this workspace, vault, or package")
    entries=value.get("entries")
    if not isinstance(entries,list) or len(entries)!=len(expected): raise ManagerError("adoption resolution does not cover exactly the current conflicts")
    reviewed={}
    for entry in entries:
        if not isinstance(entry,dict): raise ManagerError("adoption resolution entry is malformed")
        item_path=entry.get("path")
        if not isinstance(item_path,str) or item_path in reviewed or item_path not in expected: raise ManagerError("adoption resolution path is unknown or duplicated")
        current=expected[item_path]
        review=entry.get("review")
        if current["file_role"]!="release_runtime_or_entry": raise ManagerError("adoption resolution cannot replace root configuration or removed members")
        if (entry.get("before_sha256")!=current["actual_sha256"] or entry.get("after_sha256")!=current["expected_sha256"]
            or entry.get("classification")!=ADOPTION_RESOLUTION_CLASS or not isinstance(review,dict)
            or not isinstance(review.get("reviewed_at"),str) or not review["reviewed_at"].strip()
            or not isinstance(review.get("rationale"),str) or not review["rationale"].strip()):
            raise ManagerError("adoption resolution entry does not bind the current reviewed bytes")
        if not hexhash(current["actual_sha256"]) or not hexhash(current["expected_sha256"]): raise ManagerError("adoption resolution cannot approve a non-file conflict")
        reviewed[item_path]={"path":item_path,"before_sha256":current["actual_sha256"],"after_sha256":current["expected_sha256"],"classification":ADOPTION_RESOLUTION_CLASS,"review":review}
    if set(reviewed)!=set(expected): raise ManagerError("adoption resolution does not cover exactly the current conflicts")
    return {"status":"accepted","schema":ADOPTION_RESOLUTION_SCHEMA,"path":str(path),"sha256":sha(path),"entries":[reviewed[key] for key in sorted(reviewed)]}
def apply_adoption_resolution(actions,resolution):
    if resolution.get("status")!="accepted": return
    approved={entry["path"]:entry for entry in resolution["entries"]}
    for row in actions:
        if row["action"]!="conflict": continue
        entry=approved.get(row["path"])
        if entry is None or entry["before_sha256"]!=row["actual_sha256"] or entry["after_sha256"]!=row["expected_sha256"]: raise ManagerError("adoption resolution changed during plan assembly")
        row["action"]="replace"; row["disposition"]="adoption_resolution_exact_hash"; row["prior_sha256"]=row["actual_sha256"]
def resolution_snapshot(path,label):
    path=path.absolute(); plain(path); file(path,label)
    try:
        raw=path.read_bytes(); value=json.loads(raw.decode("utf-8"))
    except (OSError,UnicodeError,json.JSONDecodeError) as error: raise ManagerError(f"{label} is unreadable or malformed") from error
    if not isinstance(value,dict): raise ManagerError(f"{label} is not an object")
    return path,value,sha_b(raw)
def update_resolution(path,workspace,vault_name,package_binding,actions,previous,candidate_hashes):
    """Permit reviewed exact-byte replacement, reconciliation, or host adaptation."""
    conflicts=[row for row in actions if row["action"]=="conflict"]
    if not conflicts:
        if path is not None: raise ManagerError("update resolution was supplied but this plan has no conflicts")
        return {"status":"not_required"}
    expected={}; required=[]
    for row in conflicts:
        if row["file_role"]!="release_runtime_or_entry" or not hexhash(row.get("actual_sha256")) or not hexhash(row.get("expected_sha256")):
            raise ManagerError("update resolution cannot change root configuration, removed, missing, or malformed conflicts")
        prior=row.get("prior_sha256")
        if isinstance(prior,str):
            if not hexhash(prior) or previous.get(row["path"])!=prior:
                raise ManagerError("update resolution cannot change root configuration, removed, unowned, or malformed conflicts")
            allowed=[UPDATE_RESOLUTION_CLASS]
            if row["path"] in candidate_hashes and host_adaptation_eligible(row["path"],vault_name):
                allowed.append(UPDATE_HOST_ADAPTATION_CLASS)
            default=UPDATE_RESOLUTION_CLASS
        elif prior is None and row["path"] not in previous:
            allowed=[UPDATE_RECONCILIATION_CLASS]
            if row["path"] in candidate_hashes and host_adaptation_eligible(row["path"],vault_name):
                allowed.append(UPDATE_HOST_ADAPTATION_CLASS)
            default=UPDATE_RECONCILIATION_CLASS
        else:
            raise ManagerError("update resolution cannot change root configuration, removed, unowned, or malformed conflicts")
        expected[row["path"]]={"row":row,"allowed":set(allowed)}
        required.append({"path":row["path"],"recorded_prior_sha256":prior,"before_sha256":row["actual_sha256"],"after_sha256":row["expected_sha256"],"file_role":row["file_role"],"classification":default,"allowed_classifications":allowed})
    if path is None: return {"status":"required","schema":UPDATE_RESOLUTION_SCHEMA,"required_entries":required}
    path,value,digest=resolution_snapshot(path,"update resolution")
    if (value.get("schema")!=UPDATE_RESOLUTION_SCHEMA or value.get("mode")!="update"
        or not bound_workspace(value.get("workspace"),workspace) or value.get("vault")!=vault_name
        or value.get("package_binding")!=package_binding):
        raise ManagerError("update resolution does not bind this workspace, vault, mode, or package")
    entries=value.get("entries")
    if not isinstance(entries,list) or not entries: raise ManagerError("update resolution entries are missing")
    reviewed={}
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get("path"),str) or entry["path"] in reviewed:
            raise ManagerError("update resolution entries are malformed")
        current=expected.get(entry["path"]); review=entry.get("review")
        if current is None: raise ManagerError("update resolution does not cover exactly the current conflicts")
        row=current["row"]; classification=entry.get("classification")
        if (entry.get("recorded_prior_sha256")!=row.get("prior_sha256") or entry.get("before_sha256")!=row["actual_sha256"]
            or entry.get("after_sha256")!=row["expected_sha256"] or classification not in current["allowed"]
            or not isinstance(review,dict) or not isinstance(review.get("reviewed_at"),str) or not review["reviewed_at"].strip()
            or not isinstance(review.get("rationale"),str) or not review["rationale"].strip()):
            raise ManagerError("update resolution entry does not bind the current reviewed bytes")
        reviewed[entry["path"]]={"path":entry["path"],"recorded_prior_sha256":row.get("prior_sha256"),"before_sha256":row["actual_sha256"],"after_sha256":row["expected_sha256"],"classification":classification,"review":review}
    if set(reviewed)!=set(expected): raise ManagerError("update resolution does not cover exactly the current conflicts")
    return {"status":"accepted","schema":UPDATE_RESOLUTION_SCHEMA,"path":str(path),"sha256":digest,"entries":[reviewed[key] for key in sorted(reviewed)]}
def apply_update_resolution(actions,resolution):
    if resolution.get("status")!="accepted": return
    approved={entry["path"]:entry for entry in resolution["entries"]}
    for row in actions:
        if row["action"]!="conflict": continue
        entry=approved.get(row["path"])
        if (entry is None or entry.get("recorded_prior_sha256")!=row.get("prior_sha256")
            or entry.get("before_sha256")!=row["actual_sha256"] or entry.get("after_sha256")!=row["expected_sha256"]
            or entry.get("classification") not in {UPDATE_RESOLUTION_CLASS,UPDATE_RECONCILIATION_CLASS,UPDATE_HOST_ADAPTATION_CLASS}):
            raise ManagerError("update resolution changed during plan assembly")
        if entry["classification"]==UPDATE_HOST_ADAPTATION_CLASS:
            row["action"]="keep_host"; row["disposition"]="approved_host_runtime_adaptation_exact_hash"
        else:
            row["action"]="replace"; row["disposition"]="update_resolution_exact_hash"; row["prior_sha256"]=row["actual_sha256"]
def resolved_host_overrides(actions,resolution,carried):
    result=dict(carried or {})
    if resolution.get("status")!="accepted": return result
    rows={row["path"]:row for row in actions}
    for entry in resolution["entries"]:
        if entry.get("classification")!=UPDATE_HOST_ADAPTATION_CLASS: continue
        row=rows.get(entry.get("path"))
        # A later reviewed host edit may rebind an existing adaptation.  The
        # resolution still binds the old base, current host bytes and current
        # candidate bytes; replacing this mapping is not an unreviewed write.
        if (not isinstance(row,dict) or row.get("action")!="keep_host"
            or not hexhash(row.get("actual_sha256"))):
            raise ManagerError("host runtime adaptation changed during plan assembly")
        result[entry["path"]]=row["actual_sha256"]
    return dict(sorted(result.items()))
def ctl(workspace): return workspace/CONTROL
def manager_lock_owner(workspace):
    p=ctl(workspace)/"manager.lock"
    if not p.exists(): return None
    v=obj(p,"management exclusion lock")
    tid=v.get("transaction_id")
    if v.get("schema")!="brain_management_writer_exclusion_v1" or not isinstance(tid,str) or re.fullmatch(r"txn-[0-9a-f]{32}",tid) is None or not bound_workspace(v.get("workspace"),workspace): raise ManagerError("management exclusion lock is foreign or malformed")
    return tid
def guard_bytes(workspace):
    return canon({"schema":GUARD_SCHEMA,"workspace":str(workspace.resolve())})+b"\n"
def control_snapshot(root,locked_guard_data=None):
    rows=[]
    for item in sorted(root.rglob("*"),key=lambda p:p.as_posix().casefold()):
        plain(item); relative=item.relative_to(root).as_posix()
        # The owner record is deliberately outside a plan's admission ticket:
        # its only valid transition is none -> this executor -> none.
        if relative=="manager.lock": continue
        if item.is_file():
            # Windows refuses a second read handle while the guard byte is
            # locked.  The lease therefore supplies the bytes it has locked;
            # every other control file is still read from the filesystem.
            digest=sha_b(locked_guard_data) if relative=="manager.guard" and locked_guard_data is not None else sha(item)
            rows.append(relative+":"+digest)
        elif not item.is_dir(): raise ManagerError("management control state contains an unknown node")
    return sha_b("\n".join(rows).encode())
def control_admission(workspace,held_owner=None,ticket=None,locked_guard_data=None):
    """Read and authenticate management state without creating or repairing it."""
    root=ctl(workspace); plain(root)
    if not root.exists():
        state={"schema":ADMISSION_SCHEMA,"mode":"bootstrap","workspace":str(workspace.resolve()),"guard_sha256":None,"control_snapshot_sha256":sha_b(b"control-absent")}
        if ticket is not None and ticket!=state: raise ManagerError("management admission changed before mutation")
        return state
    if not root.is_dir(): raise ManagerError("management control path is not a directory")
    guard=root/"manager.guard"; plain(guard)
    if not guard.is_file(): raise ManagerError("existing management control lacks an authenticated guard")
    guard_data=locked_guard_data if locked_guard_data is not None else guard.read_bytes()
    if guard_data!=guard_bytes(workspace): raise ManagerError("management guard is foreign or malformed")
    owner=manager_lock_owner(workspace)
    if held_owner is None and owner is not None: raise ManagerError("workspace management exclusion lock belongs to another transaction")
    if held_owner is not None and owner!=held_owner: raise ManagerError("manager lock ownership changed; preserve it")
    state={"schema":ADMISSION_SCHEMA,"mode":"existing","workspace":str(workspace.resolve()),"guard_sha256":sha_b(guard_data),"control_snapshot_sha256":control_snapshot(root,locked_guard_data)}
    # A bootstrap ticket remains valid only for the executor which has just
    # materialized the authenticated guard and its own owner record.
    if ticket is not None and ticket.get("mode")=="bootstrap" and held_owner is not None: return ticket
    if ticket is not None and ticket!=state: raise ManagerError("management admission changed before mutation")
    return state
def bootstrap_guard(workspace):
    root=ctl(workspace); plain(root)
    try: root.mkdir()
    except FileExistsError as error: raise ManagerError("management control appeared during bootstrap") from error
    guard=root/"manager.guard"; data=guard_bytes(workspace)
    try:
        with guard.open("xb") as handle: handle.write(data); handle.flush(); os.fsync(handle.fileno())
    except FileExistsError as error: raise ManagerError("management guard appeared during bootstrap") from error
    if sha(guard)!=sha_b(data): raise ManagerError("management guard bootstrap hash mismatch")
def lock_guard(handle):
    if os.name=="nt": msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    else: fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
def unlock_guard(handle):
    if os.name=="nt": msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    else: fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
@contextmanager
def manager_lease(workspace,transaction_id,ticket):
    """Acquire only an already-admitted mutex; this function never repairs state."""
    # An interrupted transaction deliberately retains its own owner record so
    # that only its rollback may resume.  That record is distinct from a
    # foreign owner: it is authenticated and rechecked under the same guard.
    existing_owner=manager_lock_owner(workspace)
    recovery_owner=transaction_id if existing_owner==transaction_id else None
    admission=control_admission(workspace,held_owner=recovery_owner,ticket=ticket)
    if admission["mode"]=="bootstrap":
        bootstrap_guard(workspace)
        admission=control_admission(workspace)
    root=ctl(workspace); guard=root/"manager.guard"; plain(guard)
    handle=guard.open("r+b")
    try:
        handle.seek(0)
        try: lock_guard(handle)
        except OSError as error: raise ManagerError("workspace management exclusion lock is active") from error
        # A competing change after the read-only admission cannot be repaired
        # under the lease; it must fail before this executor writes an owner.
        handle.seek(0); locked_guard_data=handle.read(); handle.seek(0)
        control_admission(workspace,held_owner=recovery_owner,ticket=admission,locked_guard_data=locked_guard_data)
        if recovery_owner is None:
            write_new(root/"manager.lock",{"schema":"brain_management_writer_exclusion_v1","transaction_id":transaction_id,"workspace":str(workspace.resolve())})
        handle.seek(0); locked_guard_data=handle.read(); handle.seek(0)
        control_admission(workspace,held_owner=transaction_id,ticket=admission,locked_guard_data=locked_guard_data)
        yield root/"manager.lock",locked_guard_data
    finally:
        try:
            handle.seek(0); unlock_guard(handle)
        except OSError: pass
        handle.close()
def release_manager_lock(workspace,transaction_id):
    if manager_lock_owner(workspace)!=transaction_id: raise ManagerError("manager lock ownership changed; preserve it")
    p=ctl(workspace)/"manager.lock"; plain(p); p.unlink()
def txs(workspace):
    root=ctl(workspace)/"transactions"
    if not root.exists(): return []
    if not root.is_dir(): raise ManagerError("transaction root is not a directory")
    plain(root); result=[]
    for p in root.iterdir():
        plain(p)
        if not p.is_dir() or not p.name.startswith("txn-"): raise ManagerError("foreign management transaction artifact")
        result.append(p)
    return sorted(result,key=lambda p:p.name)
def hexhash(value): return isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value) is not None
def intent(tx,workspace):
    v=obj(tx/"intent.json","transaction intent")
    if v.get("schema")!=TX_SCHEMA or v.get("transaction_id")!=tx.name or not bound_workspace(v.get("workspace"),workspace) or not isinstance(v.get("actions"),list): raise ManagerError("transaction intent is foreign or malformed")
    adoption=v.get("adoption_resolution")
    update=v.get("update_resolution")
    # Historical journals without either resolution remain readable evidence.
    if adoption is None and "adoption_resolution" not in v and update is None and "update_resolution" not in v: return v,sha(tx/"intent.json")
    if v.get("mode") not in {"adopt","update","shared_surface"}: raise ManagerError("transaction intent mode is malformed")
    if adoption is None and "adoption_resolution" not in v: adoption={"status":"not_applicable"}
    if update is None and "update_resolution" not in v: update={"status":"not_applicable"}
    if not isinstance(adoption,dict) or adoption.get("status") not in {"accepted","not_required","not_applicable"}: raise ManagerError("transaction adoption resolution is malformed")
    if adoption.get("status")=="accepted":
        if v.get("mode")!="adopt" or adoption.get("schema")!=ADOPTION_RESOLUTION_SCHEMA or not isinstance(adoption.get("path"),str) or not hexhash(adoption.get("sha256")) or not isinstance(adoption.get("entries"),list): raise ManagerError("transaction adoption resolution is malformed")
    if not isinstance(update,dict) or update.get("status") not in {"accepted","not_required","not_applicable"}: raise ManagerError("transaction update resolution is malformed")
    if update.get("status")=="accepted":
        if v.get("mode")!="update" or update.get("schema")!=UPDATE_RESOLUTION_SCHEMA or not isinstance(update.get("path"),str) or not hexhash(update.get("sha256")) or not isinstance(update.get("entries"),list): raise ManagerError("transaction update resolution is malformed")
    return v,sha(tx/"intent.json")
def action_index(i):
    binding=i.get("package_binding")
    if not isinstance(binding,dict) or not isinstance(binding.get("package_version"),str) or not all(hexhash(binding.get(k)) for k in ("profile_build_sha256","inventory_sha256","runtime_provenance_sha256")) or not hexhash(i.get("plan_hash")):
        raise ManagerError("transaction intent package binding is malformed")
    out={}
    for r in i["actions"]:
        if not isinstance(r,dict) or not isinstance(r.get("path"),str) or r["path"] in out or r.get("action") not in {"add","replace","remove","keep","keep_host"} or not isinstance(r.get("prior_exists"),bool) or not isinstance(r.get("file_role"),str): raise ManagerError("transaction intent action is malformed")
        rel(r["path"]); prior=r.get("prior_sha256"); expected=r.get("expected_sha256")
        if prior is not None and not hexhash(prior): raise ManagerError("transaction intent prior hash is malformed")
        if expected is not None and not hexhash(expected): raise ManagerError("transaction intent expected hash is malformed")
        if r["prior_exists"] != (prior is not None): raise ManagerError("transaction intent prior existence is malformed")
        if r["action"]=="remove":
            if prior is None or expected is not None: raise ManagerError("transaction removal intent is malformed")
        elif expected is None: raise ManagerError("transaction target intent is malformed")
        out[r["path"]]=r
    return out
def config_owns_path(value,path,prior):
    managed=value.get("managed_files") if isinstance(value,dict) else None
    return isinstance(managed,dict) and managed.get(path)==prior
def transaction_scope(i,actions,workspace,current_package=True):
    """Authenticate transaction targets against current or frozen package scope."""
    shared=i.get("mode")=="shared_surface"
    frozen=i.get("candidate_runtime_hashes")
    if frozen is not None:
        if not isinstance(frozen,dict) or not frozen or any(not isinstance(path,str) or path=="brain.json" or not hexhash(value) for path,value in frozen.items()):
            raise ManagerError("transaction candidate runtime inventory is malformed")
        for path in frozen: rel(path)
    if current_package or frozen is None:
        if shared:
            from sync_shared_surface import shared_payloads
            data,shared_meta=shared_payloads(); meta={"binding":shared_meta["package_binding"]}
        else:
            data,meta=files()
        candidate_hashes={path:sha_b(payload) for path,payload in data.items()}
        if i.get("package_binding")!=meta["binding"]: raise ManagerError("transaction intent package binding does not match this runtime")
        if frozen is not None and frozen!=candidate_hashes: raise ManagerError("transaction candidate runtime inventory does not match this runtime")
    else:
        candidate_hashes=dict(frozen)
    vault_name=i.get("vault")
    if not isinstance(vault_name,str) or not vault_name: raise ManagerError("transaction intent vault is malformed")
    vault_name=vault(workspace,vault_name)
    existing=config(workspace,False)
    if existing is not None and existing.get("vault")!=vault_name: raise ManagerError("transaction intent vault does not match workspace ownership")
    roots={PurePosixPath(path).parts[0] for path in candidate_hashes}; removals=[]
    for path,row in actions.items():
        if private(path,prefixes(vault_name)): raise ManagerError("transaction action overlaps a private workspace scope")
        if path=="brain.json":
            if row["file_role"]!="root_configuration": raise ManagerError("transaction brain configuration role is malformed")
            continue
        if row["action"]=="remove":
            if shared or row["file_role"]!="removed_release_member": raise ManagerError("transaction removed release role is malformed")
            if PurePosixPath(path).parts[0] not in roots: raise ManagerError("transaction removal is outside the public managed scope")
            removals.append((path,row)); continue
        expected_role="shared_entry_surface" if shared else "release_runtime_or_entry"
        if row["file_role"]!=expected_role or candidate_hashes.get(path)!=row["expected_sha256"]:
            raise ManagerError("transaction target does not match this runtime")
    resolution=i.get("adoption_resolution")
    if isinstance(resolution,dict) and resolution.get("status")=="accepted":
        entries=resolution.get("entries")
        if not isinstance(entries,list) or not entries: raise ManagerError("transaction adoption resolution is malformed")
        seen=set()
        for entry in entries:
            if not isinstance(entry,dict) or not isinstance(entry.get("path"),str) or entry["path"] in seen: raise ManagerError("transaction adoption resolution is malformed")
            seen.add(entry["path"]); row=actions.get(entry["path"]); review=entry.get("review")
            if (row is None or row.get("action")!="replace" or row.get("file_role")!="release_runtime_or_entry" or row.get("prior_sha256")!=entry.get("before_sha256")
                or row.get("expected_sha256")!=entry.get("after_sha256") or entry.get("classification")!=ADOPTION_RESOLUTION_CLASS
                or not isinstance(review,dict) or not isinstance(review.get("reviewed_at"),str) or not review["reviewed_at"].strip()
                or not isinstance(review.get("rationale"),str) or not review["rationale"].strip()):
                raise ManagerError("transaction adoption resolution no longer matches its actions")
    resolution=i.get("update_resolution"); approved_host=set()
    if isinstance(resolution,dict) and resolution.get("status")=="accepted":
        entries=resolution.get("entries")
        if not isinstance(entries,list) or not entries: raise ManagerError("transaction update resolution is malformed")
        seen=set()
        for entry in entries:
            if not isinstance(entry,dict) or not isinstance(entry.get("path"),str) or entry["path"] in seen: raise ManagerError("transaction update resolution is malformed")
            seen.add(entry["path"]); row=actions.get(entry["path"]); review=entry.get("review")
            classification=entry.get("classification"); recorded=entry.get("recorded_prior_sha256")
            valid_recorded=((classification==UPDATE_RESOLUTION_CLASS and hexhash(recorded))
                            or (classification==UPDATE_RECONCILIATION_CLASS and recorded is None)
                            or (classification==UPDATE_HOST_ADAPTATION_CLASS and (hexhash(recorded) or recorded is None)))
            expected_action="keep_host" if classification==UPDATE_HOST_ADAPTATION_CLASS else "replace"
            if (row is None or row.get("action")!=expected_action or row.get("file_role")!="release_runtime_or_entry" or row.get("prior_sha256")!=entry.get("before_sha256")
                or row.get("expected_sha256")!=entry.get("after_sha256") or not valid_recorded
                or (classification==UPDATE_HOST_ADAPTATION_CLASS and not host_adaptation_eligible(entry["path"],vault_name))
                or not isinstance(review,dict) or not isinstance(review.get("reviewed_at"),str) or not review["reviewed_at"].strip()
                or not isinstance(review.get("rationale"),str) or not review["rationale"].strip()):
                raise ManagerError("transaction update resolution no longer matches its actions")
            if classification==UPDATE_HOST_ADAPTATION_CLASS: approved_host.add(entry["path"])
    carried=carried_host_overrides(existing,candidate_hashes,vault_name) if existing is not None else {}
    for path,row in actions.items():
        if row["action"]=="keep_host" and path not in approved_host:
            if not isinstance(carried,dict) or carried.get(path)!=row.get("prior_sha256"):
                raise ManagerError("transaction host runtime adaptation is not a retained candidate-bound approval")
    return {"existing":existing,"removals":removals,"vault":vault_name,"update_resolution":i.get("update_resolution")}
def validate_removal_ownership(scope,actions,tx):
    if not scope["removals"]: return
    witnesses=[scope["existing"]]
    brain=actions.get("brain.json"); backup=tx/"backups"/"brain.json"
    if brain and brain.get("prior_sha256") and backup.is_file() and sha(backup)==brain["prior_sha256"]:
        witnesses.append(obj(backup,"prior managed brain configuration"))
    for path,row in scope["removals"]:
        if not any(config_owns_path(value,path,row["prior_sha256"]) for value in witnesses): raise ManagerError("transaction removal is not owned by workspace configuration")
def validate_update_resolution_ownership(scope,actions,tx,workspace):
    resolution=scope.get("update_resolution")
    if not isinstance(resolution,dict) or resolution.get("status")!="accepted": return
    brain=actions.get("brain.json")
    if not isinstance(brain,dict) or not hexhash(brain.get("prior_sha256")):
        raise ManagerError("update resolution lacks a prior managed brain configuration")
    backup=tx/"backups"/"brain.json"
    if backup.exists():
        file(backup,"prior managed brain configuration")
        if sha(backup)!=brain["prior_sha256"]: raise ManagerError("prior managed brain configuration backup changed")
        witness=obj(backup,"prior managed brain configuration")
    else:
        current=workspace/"brain.json"
        if not current.is_file() or sha(current)!=brain["prior_sha256"]:
            raise ManagerError("prior managed brain configuration is unavailable for update resolution recovery")
        witness=scope["existing"]
    managed=witness.get("managed_files") if isinstance(witness,dict) else None
    if witness.get("schema") not in LEGACY_CONFIGS|{CONFIG} or witness.get("vault")!=scope["vault"] or not isinstance(managed,dict):
        raise ManagerError("prior managed brain configuration is invalid for update resolution recovery")
    entries=resolution.get("entries")
    if not isinstance(entries,list) or not entries: raise ManagerError("transaction update resolution is malformed")
    for entry in entries:
        if not isinstance(entry,dict): raise ManagerError("transaction update resolution is malformed")
        path=entry.get("path"); classification=entry.get("classification"); row=actions.get(path)
        if classification==UPDATE_RESOLUTION_CLASS:
            if not config_owns_path(witness,path,entry.get("recorded_prior_sha256")):
                raise ManagerError("update resolution target was not owned by the prior brain configuration")
        elif classification==UPDATE_HOST_ADAPTATION_CLASS and entry.get("recorded_prior_sha256") is not None:
            if not config_owns_path(witness,path,entry.get("recorded_prior_sha256")):
                raise ManagerError("host runtime adaptation target was not owned by the prior brain configuration")
        elif classification in {UPDATE_RECONCILIATION_CLASS,UPDATE_HOST_ADAPTATION_CLASS}:
            if path in managed or entry.get("recorded_prior_sha256") is not None or not isinstance(row,dict) or row.get("prior_sha256")!=entry.get("before_sha256"):
                raise ManagerError("update reconciliation target was not an existing unowned runtime file")
            target_backup=child(tx/"backups",path)
            if target_backup.exists():
                file(target_backup,"update reconciliation prior runtime")
                if sha(target_backup)!=entry.get("before_sha256"):
                    raise ManagerError("update reconciliation prior runtime backup changed")
            else:
                current=child(workspace,path)
                if not current.is_file() or sha(current)!=entry.get("before_sha256"):
                    raise ManagerError("update reconciliation prior runtime is unavailable for recovery")
        else:
            raise ManagerError("transaction update resolution classification is malformed")
def cps(tx,workspace,ih,action_paths=None):
    d=tx/"checkpoints"
    if not d.is_dir(): raise ManagerError("transaction checkpoints missing")
    out=[]; prior=""; n=1
    prior_record=None
    graph={"intent":{"preparing","rolling_back"},"preparing":{"prepared","rolling_back"},"prepared":{"applying","rolling_back"},"applying":{"applying","verified","rolling_back"},"verified":{"committed","rolling_back"},"committed":{"rolling_back"},"rolling_back":{"rolling_back","rolled_back"},"rolled_back":set()}
    for p in sorted(d.glob("*.json"),key=lambda q:q.name):
        if p.name!=f"{n:06d}.json": raise ManagerError("transaction checkpoint sequence is forked")
        x=obj(p,"transaction checkpoint"); h=sha(p)
        if x.get("schema")!=CP_SCHEMA or x.get("transaction_id")!=tx.name or x.get("intent_sha256")!=ih or x.get("sequence")!=n or x.get("previous_checkpoint_sha256")!=prior or x.get("state") not in {"intent","preparing","prepared","applying","verified","committed","rolling_back","rolled_back"} or not isinstance(x.get("actions"),dict): raise ManagerError("transaction checkpoint is foreign or malformed")
        if action_paths is not None and set(x["actions"])!=action_paths: raise ManagerError("transaction checkpoint action set is foreign or malformed")
        if any(not isinstance(value,str) or value not in {"pending","prepared","applied","verified","restoring","restored"} for value in x["actions"].values()): raise ManagerError("transaction checkpoint action state is malformed")
        if prior_record is None:
            if x["state"]!="intent" or any(value!="pending" for value in x["actions"].values()): raise ManagerError("transaction initial checkpoint is malformed")
        elif x["state"] not in graph[prior_record["state"]]: raise ManagerError("transaction checkpoint state graph is malformed")
        out.append((x,h)); prior=h; n+=1
        prior_record=x
    if not out: raise ManagerError("transaction has no checkpoint")
    return out
def authenticate_transaction(tx,workspace,current_package=True):
    i,ih=intent(tx,workspace); actions=action_index(i); scope=transaction_scope(i,actions,workspace,current_package); records=cps(tx,workspace,ih,set(actions))
    backups=tx/"backups"
    if not backups.is_dir(): raise ManagerError("transaction backups missing")
    wanted={pathlib_path for pathlib_path,row in actions.items() if row["action"] in {"replace","remove"} and row["prior_sha256"]}
    allowed_dirs={""}
    for path in wanted:
        pieces=PurePosixPath(path).parts
        allowed_dirs.update("/".join(pieces[:n]) for n in range(1,len(pieces)))
    for item in backups.rglob("*"):
        plain(item); name=item.relative_to(backups).as_posix()
        if item.is_dir():
            if name not in allowed_dirs: raise ManagerError("foreign backup artifact preserved and blocks recovery")
            continue
        if not item.is_file() or name not in wanted or sha(item)!=actions[name]["prior_sha256"]: raise ManagerError("foreign or changed backup artifact preserved and blocks recovery")
    validate_removal_ownership(scope,actions,tx)
    validate_update_resolution_ownership(scope,actions,tx,workspace)
    known_stages={}
    parents=set()
    for path,row in actions.items():
        target=child(workspace,path); parents.add(target.parent)
        if row.get("expected_sha256"): known_stages[stage_path(target,tx.name,row["expected_sha256"],"target")]=row["expected_sha256"]
        if row.get("prior_sha256"): known_stages[stage_path(target,tx.name,row["prior_sha256"],"restore")]=row["prior_sha256"]
    for parent in parents:
        for item in parent.glob(".*.brain-mgmt-*"):
            plain(item)
            if item not in known_stages or not item.is_file() or sha(item)!=known_stages[item]: raise ManagerError("foreign or changed staging artifact preserved and blocks recovery")
    return i,ih,records
def recorded_txstate(tx,workspace):
    """Read historical terminal state without imposing a newer package binding."""
    i,ih=intent(tx,workspace); actions=action_index(i)
    return cps(tx,workspace,ih,set(actions))[-1][0]["state"]
def txstate(tx,workspace):
    return authenticate_transaction(tx,workspace)[2][-1][0]["state"]
def cp(tx,workspace,ih,state,actions,evidence=None):
    current,_=intent(tx,workspace); records=cps(tx,workspace,ih,set(action_index(current))); old,h=records[-1]
    allowed={"intent":{"preparing","rolling_back"},"preparing":{"prepared","rolling_back"},"prepared":{"applying","rolling_back"},"applying":{"applying","verified","rolling_back"},"verified":{"committed","rolling_back"},"committed":{"rolling_back"},"rolling_back":{"rolling_back","rolled_back"},"rolled_back":set()}
    if state not in allowed[old["state"]]: raise ManagerError("non-monotonic transaction state")
    v={"schema":CP_SCHEMA,"transaction_id":tx.name,"intent_sha256":ih,"sequence":old["sequence"]+1,"previous_checkpoint_sha256":h,"state":state,"actions":dict(sorted(actions.items()))}
    if evidence: v["evidence"]=evidence
    write_new(tx/"checkpoints"/f"{v['sequence']:06d}.json",v)
def stage(target,tid,data,role,fail=False):
    expected=sha_b(data); p=target.with_name(f".{target.name}.brain-mgmt-{role}-{tid}-{expected[:20]}"); plain(p)
    if p.exists():
        file(p,"transaction stage")
        if sha(p)!=expected: raise ManagerError("foreign transaction stage bytes")
    else:
        p.parent.mkdir(parents=True,exist_ok=True)
        with p.open("xb") as h: h.write(data); h.flush(); os.fsync(h.fileno())
    if fail: raise ManagerError("test interruption after stage")
    os.replace(p,target)
    if sha(target)!=expected: raise ManagerError("target replacement hash mismatch")
def stage_path(target,tid,digest,role):
    p=target.with_name(f".{target.name}.brain-mgmt-{role}-{tid}-{digest[:20]}")
    plain(p); return p
def remove_owned_stage(target,tid,digest,role):
    p=stage_path(target,tid,digest,role)
    if not p.exists(): return
    file(p,"transaction stage")
    if sha(p)!=digest: raise ManagerError("owned transaction stage hash mismatch")
    p.unlink()
def check_stages(workspace,tx,i):
    for r in i["actions"]:
        target=child(workspace,r["path"])
        for p in target.parent.glob(f".{target.name}.brain-mgmt-*"):
            if tx.name not in p.name: raise ManagerError("foreign stage artifact preserved and blocks recovery")
def retired(workspace):
    inv=inventory(); c=inv["daily_entry_contract"]; policy=inv.get("adoption_policy",{}).get("legacy_members",{}); out=[]
    for name in [*c.get("retired_or_unmanaged_members",[]),c.get("package_only_skill","")]:
        if isinstance(name,str) and name:
            p=child(workspace,f".agents/skills/{name}"); out.append({"path":f".agents/skills/{name}/","present":p.exists(),"disposition":policy.get(name,"preserve_unmanaged"),"tree":tree(p)})
    return out
def plan(mode,workspace,vault_arg,name_arg,proof_path=None,held_lock_owner=None,admission_ticket=None,locked_guard_data=None,adoption_resolution_path=None,update_resolution_path=None):
    workspace=workspace.absolute(); plain(workspace)
    if not workspace.is_dir(): raise ManagerError("existing workspace directory required")
    if mode!="adopt" and adoption_resolution_path is not None: raise ManagerError("adoption resolution is valid only for first adoption")
    if mode!="update" and update_resolution_path is not None: raise ManagerError("update resolution is valid only for managed updates")
    existing=config(workspace,mode=="update")
    if mode=="adopt" and existing: raise ManagerError("already managed; use plan-update")
    if existing:
        candidate_version=version_key(version())
        if candidate_version<version_key(existing.get("package_version")):
            raise ManagerError("candidate product version predates installed product version")
        shared=existing.get("shared_surface")
        if shared is not None:
            binding=shared.get("package_binding") if isinstance(shared,dict) else None
            if not isinstance(binding,dict) or candidate_version<version_key(binding.get("package_version")):
                raise ManagerError("candidate product version predates existing shared surface source")
    admission=control_admission(workspace,held_lock_owner,admission_ticket,locked_guard_data)
    v=vault(workspace,str(existing["vault"]) if existing else str(vault_arg or ""))
    name=str(existing["display_name"]) if existing else str(name_arg or "").strip()
    if not name: raise ManagerError("display name required for adoption")
    data,meta=files(); runtime_hashes=dict(meta["hashes"]); scopes=prefixes(v)
    carried=carried_host_overrides(existing,runtime_hashes,v) if existing else {}
    host_overrides=dict(carried or {})
    next_cfg=make_config(existing,v,name,runtime_hashes,host_overrides); data["brain.json"]=workspace_config_bytes(next_cfg)
    hashes=dict(runtime_hashes); hashes["brain.json"]=sha_b(data["brain.json"])
    previous=dict(existing.get("managed_files") or {}) if existing else {}; actions=[]
    for path in sorted(hashes):
        if private(path,scopes): raise ManagerError("managed target overlaps private scope")
        target=child(workspace,path); actual=sha(target) if target.is_file() else None; prior=previous.get(path)
        # brain.json cannot record its own byte hash without a recursive
        # configuration.  Its ownership is therefore the shared stable
        # serializer applied to the currently verified v2 object.
        if path=="brain.json" and existing:
            prior=sha_b(workspace_config_bytes(existing))
        accepted_host=host_overrides.get(path) if path!="brain.json" else None
        if not target.exists(): action,disp="add","absent"
        elif not target.is_file(): action,disp="conflict","existing_path_not_regular_file"
        elif accepted_host is not None:
            if actual==accepted_host: action,disp="keep_host","already_matches_accepted_host_adaptation"
            else: action,disp="conflict","registered_host_runtime_changed"
        elif actual==hashes[path]: action,disp="keep","already_matches_candidate"
        elif mode=="update" and prior==actual: action,disp="replace","matches_recorded_prior_hash"
        else: action,disp="conflict","preserve_unowned_or_modified_file"
        actions.append({"path":path,"action":action,"disposition":disp,"actual_sha256":actual,"prior_sha256":prior,"expected_sha256":hashes[path],"file_role":"root_configuration" if path=="brain.json" else "release_runtime_or_entry"})
    if mode=="update":
        for path,prior in sorted(previous.items()):
            if path in hashes: continue
            if not isinstance(path,str) or not isinstance(prior,str): raise ManagerError("invalid previous ownership record")
            if private(path,scopes): raise ManagerError("removed member overlaps private scope")
            target=child(workspace,path); actual=sha(target) if target.is_file() else None
            action,disp=("remove","removed_from_candidate") if actual==prior else ("conflict","removed_member_changed_or_missing")
            actions.append({"path":path,"action":action,"disposition":disp,"actual_sha256":actual,"prior_sha256":prior,"expected_sha256":None,"file_role":"removed_release_member"})
    adoption=adoption_resolution(adoption_resolution_path,workspace,v,meta["binding"],actions) if mode=="adopt" else {"status":"not_applicable"}
    update=update_resolution(update_resolution_path,workspace,v,meta["binding"],actions,previous,runtime_hashes) if mode=="update" else {"status":"not_applicable"}
    apply_adoption_resolution(actions,adoption)
    apply_update_resolution(actions,update)
    host_overrides=resolved_host_overrides(actions,update,host_overrides)
    next_cfg=make_config(existing,v,name,runtime_hashes,host_overrides); data["brain.json"]=workspace_config_bytes(next_cfg); hashes["brain.json"]=sha_b(data["brain.json"])
    brain_row=next(row for row in actions if row["path"]=="brain.json"); brain_row["expected_sha256"]=hashes["brain.json"]
    if mode=="update":
        if brain_row["actual_sha256"]==hashes["brain.json"]:
            brain_row["action"]="keep"; brain_row["disposition"]="already_matches_candidate"
        elif brain_row["prior_sha256"]==brain_row["actual_sha256"]:
            brain_row["action"]="replace"; brain_row["disposition"]="matches_recorded_prior_hash"
        else:
            brain_row["action"]="conflict"; brain_row["disposition"]="preserve_unowned_or_modified_file"
    incomplete=[]
    for t in txs(workspace):
        s=recorded_txstate(t,workspace)
        if s not in {"committed","rolled_back"}: incomplete.append({"transaction_id":t.name,"state":s})
    w=writer(workspace,v); q=proof(proof_path,workspace,w) if mode=="adopt" else {"status":"not_required"}
    active_manager_lock=held_lock_owner
    reasons=[]
    if any(x["action"]=="conflict" for x in actions): reasons.append("managed_file_conflicts")
    if incomplete: reasons.append("incomplete_management_transaction")
    if active_manager_lock and active_manager_lock!=held_lock_owner: reasons.append("workspace_management_exclusion_locked")
    if w["status"]=="blocked": reasons.append("writer_record_uninspectable_or_locked")
    if q["status"]!="proven" and mode=="adopt": reasons.append("bootstrap_writer_quiescence_not_proven")
    priv=[{"path":x,"tree":tree(child(workspace,x.rstrip("/")))} for x in scopes]
    out={"schema":SCHEMA,"status":"blocked" if reasons else "planned","mode":mode,"workspace":str(workspace),"workspace_identity":str(workspace.resolve()),"vault":v,"display_name":name,"package_binding":meta["binding"],"candidate_runtime_hashes":dict(sorted(runtime_hashes.items())),"adoption_resolution":adoption,"update_resolution":update,"actions":actions,"writes":sum(x["action"] in {"add","replace","remove"} for x in actions),"conflicts":[x for x in actions if x["action"]=="conflict"],"retired_member_dispositions":retired(workspace),"private_preservation":priv,"writer_compatibility":w,"bootstrap_quiescence":q,"incomplete_transactions":incomplete,"manager_exclusion_lock":active_manager_lock,"control_admission":admission,"configuration":next_cfg,"blocking_reasons":reasons}
    # The ephemeral manager-lock owner is an observation used for admission,
    # not plan intent.  Apply rechecks it under its own lock, so including the
    # new transaction id here would make every valid plan self-conflict.
    signed=dict(out); signed.pop("manager_exclusion_lock",None)
    out["plan_hash"]=sha_b(canon(signed)); return out
def begin(workspace,p,transaction_id):
    root=ctl(workspace)
    if root.exists() and not root.is_dir(): raise ManagerError("management control path is not directory")
    root.mkdir(exist_ok=True); (root/"transactions").mkdir(exist_ok=True); tx=root/"transactions"/transaction_id; tx.mkdir(); (tx/"backups").mkdir(); (tx/"checkpoints").mkdir()
    runtime_hashes=p.get("candidate_runtime_hashes")
    if not isinstance(runtime_hashes,dict): raise ManagerError("plan lacks candidate runtime inventory")
    acts=[{"path":x["path"],"action":x["action"],"prior_sha256":x["actual_sha256"],"expected_sha256":x["expected_sha256"],"prior_exists":x["actual_sha256"] is not None,"file_role":x["file_role"]} for x in p["actions"]]
    i={"schema":TX_SCHEMA,"transaction_id":tx.name,"mode":p["mode"],"workspace":p["workspace_identity"],"plan_hash":p["plan_hash"],"package_binding":p["package_binding"],"candidate_runtime_hashes":dict(sorted(runtime_hashes.items())),"vault":p["vault"],"adoption_resolution":p.get("adoption_resolution"),"update_resolution":p.get("update_resolution"),"actions":acts}; ih=write_new(tx/"intent.json",i); states={x["path"]:"pending" for x in acts}; write_new(tx/"checkpoints"/"000001.json",{"schema":CP_SCHEMA,"transaction_id":tx.name,"intent_sha256":ih,"sequence":1,"previous_checkpoint_sha256":"","state":"intent","actions":states}); return tx,i,ih,states
def apply(p,expected,proof_path=None,interrupt="",adoption_resolution_path=None,update_resolution_path=None):
    if p.get("status")!="planned" or p.get("plan_hash")!=expected: raise ManagerError("blocked or changed plan cannot apply")
    workspace=Path(p["workspace"]).absolute(); fresh=plan(p["mode"],workspace,p["vault"],p["display_name"],proof_path,adoption_resolution_path=adoption_resolution_path,update_resolution_path=update_resolution_path)
    if fresh.get("status")!="planned" or fresh.get("plan_hash")!=expected: raise ManagerError("package, tree, configuration or writer facts changed")
    ticket=p.get("control_admission")
    if not isinstance(ticket,dict): raise ManagerError("plan lacks management admission")
    transaction_id="txn-"+uuid.uuid4().hex; begun=False; release=True
    with manager_lease(workspace,transaction_id,ticket) as (_,locked_guard_data):
      try:
        # Recheck after acquiring the shared exclusion lock.  The first plan
        # may have been true when it was read but stale by the time apply got
        # an exclusive turn.
        fresh=plan(p["mode"],workspace,p["vault"],p["display_name"],proof_path,transaction_id,ticket,locked_guard_data,adoption_resolution_path,update_resolution_path)
        if fresh.get("status")!="planned" or fresh.get("plan_hash")!=expected: raise ManagerError("package, tree, configuration or writer facts changed")
        tx,i,ih,states=begin(workspace,fresh,transaction_id); begun=True
        if interrupt=="after_intent": raise ManagerError("test interruption after intent")
        cp(tx,workspace,ih,"preparing",states)
        for r in i["actions"]:
            if r["action"] in {"replace","remove"}:
                src=child(workspace,r["path"]); b=child(tx/"backups",r["path"]); b.parent.mkdir(parents=True,exist_ok=True)
                if not src.is_file() or sha(src)!=r["prior_sha256"]: raise ManagerError("target changed before backup")
                with src.open("rb") as a,b.open("xb") as z: shutil.copyfileobj(a,z); z.flush(); os.fsync(z.fileno())
                if sha(b)!=r["prior_sha256"]: raise ManagerError("backup mismatch")
                states[r["path"]]="prepared"
        cp(tx,workspace,ih,"prepared",states)
        # A no-op update is still a real transaction.  Preserve the same
        # monotonic checkpoint path as a mutating update before verification.
        if all(r["action"] in {"keep","keep_host"} for r in i["actions"]): cp(tx,workspace,ih,"applying",states)
        data,_=files()
        for r in i["actions"]:
            if r["action"] in {"keep","keep_host"}: states[r["path"]]="verified"; continue
            target=child(workspace,r["path"]); states[r["path"]]="prepared"; cp(tx,workspace,ih,"applying",states,{"path":r["path"]})
            if r["action"]=="remove": target.unlink()
            else:
                payload=workspace_config_bytes(fresh["configuration"]) if r["path"]=="brain.json" else data[r["path"]]
                if sha_b(payload)!=r["expected_sha256"]: raise ManagerError("candidate payload drift")
                stage(target,tx.name,payload,"target",interrupt=="after_stage")
            if interrupt=="after_target": raise ManagerError("test interruption after target mutation")
            states[r["path"]]="applied"; cp(tx,workspace,ih,"applying",states,{"path":r["path"]})
        for r in i["actions"]:
            pth=child(workspace,r["path"])
            if r["action"]=="remove":
                if pth.exists(): raise ManagerError("removed target remained")
            elif not pth.is_file() or sha(pth)!=(r["prior_sha256"] if r["action"]=="keep_host" else r["expected_sha256"]): raise ManagerError("post-action readback failed")
            states[r["path"]]="verified"
        check_stages(workspace,tx,i); cp(tx,workspace,ih,"verified",states)
        if interrupt=="after_verified": raise ManagerError("test interruption before commit")
        cp(tx,workspace,ih,"committed",states); return {"status":"applied","transaction_id":tx.name,"control_directory":str(ctl(workspace)),"writes":fresh["writes"]}
      except BaseException as error:
        release=not begun
        if begun and isinstance(error,Exception):
            raise TransactionInterrupted(transaction_id,str(error)) from error
        raise
      finally:
        if release: release_manager_lock(workspace,transaction_id)
def rollback(workspace,tid,interrupt=""):
    workspace=workspace.absolute(); tx=ctl(workspace)/"transactions"/tid
    if not tx.is_dir(): raise ManagerError("transaction not found")
    existing_owner=manager_lock_owner(workspace)
    ticket=control_admission(workspace,held_owner=tid if existing_owner==tid else None)
    # A completed rollback is a read-only, idempotent observation after its
    # owner record has been released.  A foreign owner was rejected by the
    # admission above and can never be bypassed by this fast path.
    if existing_owner is None and txstate(tx,workspace)=="rolled_back": return {"status":"already_rolled_back","transaction_id":tid}
    release=True
    with manager_lease(workspace,tid,ticket):
      try:
        i,ih,records=authenticate_transaction(tx,workspace,current_package=False); latest=records[-1][0]; states=dict(latest["actions"])
        gate=writer(workspace,i["vault"])
        if gate["status"]=="blocked": raise ManagerError("writer or research assessment blocks rollback")
        if latest["state"]=="rolled_back": return {"status":"already_rolled_back","transaction_id":tid}
        if latest["state"]!="rolling_back": cp(tx,workspace,ih,"rolling_back",states)
        restored=already=0
        for r in reversed(i["actions"]):
          if r["action"] in {"keep","keep_host"}: continue
          p=child(workspace,r["path"]); now=sha(p) if p.is_file() else None; prior=r["prior_sha256"]; expected=r["expected_sha256"]
          prior_ok=(now==prior) if prior else not p.exists(); expected_ok=(now==expected) if expected else not p.exists()
          if prior_ok:
            if expected: remove_owned_stage(p,tid,expected,"target")
            if prior: remove_owned_stage(p,tid,prior,"restore")
            states[r["path"]]="restored"; cp(tx,workspace,ih,"rolling_back",states,{"path":r["path"],"outcome":"already_restored"}); already+=1; continue
          if not expected_ok: raise ManagerError("third-party target change blocks rollback")
          states[r["path"]]="restoring"; cp(tx,workspace,ih,"rolling_back",states,{"path":r["path"]})
          if prior:
            b=child(tx/"backups",r["path"]); file(b,"backup")
            if sha(b)!=prior: raise ManagerError("backup changed")
            stage(p,tid,b.read_bytes(),"restore",interrupt=="rollback_after_stage")
          elif p.exists(): p.unlink()
          if interrupt=="rollback_after_target": raise ManagerError("test interruption after rollback target")
          states[r["path"]]="restored"; cp(tx,workspace,ih,"rolling_back",states,{"path":r["path"],"outcome":"restored"}); restored+=1
          if expected: remove_owned_stage(p,tid,expected,"target")
          if prior: remove_owned_stage(p,tid,prior,"restore")
        cp(tx,workspace,ih,"rolled_back",states); return {"status":"rolled_back","transaction_id":tid,"restored":restored,"already_restored":already}
      except BaseException:
        release=False
        raise
      finally:
        if release: release_manager_lock(workspace,tid)
def status(workspace):
    workspace=workspace.absolute(); plain(workspace); admission=control_admission(workspace); bad=[]
    for t in txs(workspace):
        s=recorded_txstate(t,workspace)
        if s not in {"committed","rolled_back"}: bad.append({"transaction_id":t.name,"state":s})
    lock_owner=manager_lock_owner(workspace)
    c=config(workspace,False)
    if not c: return {"status":"blocked" if lock_owner else "unmanaged","incomplete_transactions":bad,"manager_exclusion_lock":lock_owner,"control_admission":admission}
    w=writer(workspace,c["vault"])
    data,m=files(); expected=dict(m["hashes"]); overlays=carried_host_overrides(c,expected,str(c["vault"]))
    if isinstance(overlays,dict): expected.update(overlays)
    candidate=make_config(c,str(c["vault"]),str(c["display_name"]),m["hashes"],overlays or {}); expected["brain.json"]=sha_b(workspace_config_bytes(candidate)); missing=[]; changed=[]
    for path,h in expected.items():
        p=child(workspace,path)
        if not p.is_file(): missing.append(path)
        elif sha(p)!=h: changed.append(path)
    state="blocked" if lock_owner or w["status"]=="blocked" else ("incomplete" if bad else ("ready" if not missing and not changed and c.get("managed_files")==m["hashes"] and c.get("management",{}).get("package_binding")==m["binding"] else "drift"))
    return {"status":state,"package_version":version(),"vault":c["vault"],"missing":missing,"modified":changed,"incomplete_transactions":bad,"manager_exclusion_lock":lock_owner,"control_admission":admission,"writer_compatibility":w}
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    for cmd in ("plan-adopt","plan-update"):
        x=sub.add_parser(cmd); x.add_argument("--workspace",type=Path,required=True); x.add_argument("--vault"); x.add_argument("--display-name"); x.add_argument("--bootstrap-quiescence-proof",type=Path)
        if cmd=="plan-adopt": x.add_argument("--adoption-resolution",type=Path)
        else: x.add_argument("--update-resolution",type=Path)
    x=sub.add_parser("apply"); x.add_argument("--mode",choices=("adopt","update"),required=True); x.add_argument("--workspace",type=Path,required=True); x.add_argument("--vault"); x.add_argument("--display-name"); x.add_argument("--bootstrap-quiescence-proof",type=Path); x.add_argument("--adoption-resolution",type=Path); x.add_argument("--update-resolution",type=Path); x.add_argument("--plan-hash",required=True); x.add_argument("--test-interrupt",choices=("after_intent","after_stage","after_target","after_verified"),default="")
    x=sub.add_parser("rollback"); x.add_argument("--workspace",type=Path,required=True); x.add_argument("--transaction-id",required=True); x.add_argument("--test-interrupt",choices=("rollback_after_stage","rollback_after_target"),default="")
    x=sub.add_parser("status"); x.add_argument("--workspace",type=Path,required=True)
    a=p.parse_args()
    try:
        if a.cmd.startswith("plan-"): out=plan("adopt" if a.cmd=="plan-adopt" else "update",a.workspace,a.vault,a.display_name,a.bootstrap_quiescence_proof,adoption_resolution_path=getattr(a,"adoption_resolution",None),update_resolution_path=getattr(a,"update_resolution",None))
        elif a.cmd=="apply": out=apply(plan(a.mode,a.workspace,a.vault,a.display_name,a.bootstrap_quiescence_proof,adoption_resolution_path=a.adoption_resolution,update_resolution_path=a.update_resolution),a.plan_hash,a.bootstrap_quiescence_proof,a.test_interrupt,a.adoption_resolution,a.update_resolution)
        elif a.cmd=="rollback": out=rollback(a.workspace,a.transaction_id,a.test_interrupt)
        else: out=status(a.workspace)
        print(json.dumps(out,ensure_ascii=False,indent=2)); return 0 if out["status"] not in {"blocked","drift","incomplete"} else 2
    except (ManagerError,OSError,ValueError,TypeError,json.JSONDecodeError) as e: print(json.dumps({"status":"blocked","reason":str(e)},ensure_ascii=False)); return 2
if __name__=="__main__": raise SystemExit(main())
