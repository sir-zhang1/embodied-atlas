#!/usr/bin/env python3
"""Stage a new publishable repository using explicit public-file allowlists."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import build_public
import public_state

ROOT=Path(__file__).resolve().parents[1]
FILES=[*("app/"+x for x in build_public.ASSETS),
       "content/domains.json","content/reviews.json","content/abstract-briefs.json",
       "pipeline/cli.py","pipeline/sources.json","pipeline/test_pipeline.py","pipeline/test_company.py","pipeline/README.md",
       "pipeline/aihot-source-template.json","核查/check_public_sources.py",
       "scripts/build_public.py","scripts/public_state.py","scripts/stage_public_repo.py",
       "scripts/test_cloud.py","scripts/DEPLOYMENT.md",".github/workflows/daily-public.yml"]


def stage(root,out,db_path):
    out=out.resolve()
    if out==root.resolve() or root.resolve() in out.parents:
        raise ValueError("Stage into a new directory outside the private project")
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a new empty staging directory; existing files are not deleted")
    out.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        source=root/name
        if not source.is_file() or source.is_symlink():
            raise ValueError("Missing or symlink public source: "+name)
        dest=out/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest)
    framework=build_public.read_json(root/"方案/知识框架.json")
    safe_framework={key:framework[key] for key in ("schema_version","status","updated_on","rationale","facet_examples",*build_public.FRAMEWORK_KEYS) if key in framework}
    (out/"方案").mkdir(exist_ok=True)
    (out/"方案/知识框架.json").write_text(json.dumps(safe_framework,ensure_ascii=False,indent=2)+"\n")
    override=build_public.read_json(root/"content/domain-overrides.json",{"schema_version":1,"overrides":[]})
    build_public.apply_domain_overrides([],override,safe_framework["nodes"])
    (out/"content/domain-overrides.json").write_text(json.dumps(override,ensure_ascii=False,indent=2)+"\n")
    state=public_state.snapshot(db_path.resolve())
    (out/"pipeline/bootstrap-state.json").write_bytes(public_state.serialized(state))
    (out/"pipeline/editorial-overrides.json").write_text(json.dumps({"schema_version":1,"edits":state["tables"]["editorial"]},ensure_ascii=False,indent=2)+"\n")
    (out/".gitignore").write_text(".runtime/\n.data/\npipeline/.data/\n_site/\n__pycache__/\n*.pyc\n*.sqlite\n*.sqlite-*\n.env\n.env.*\n")
    (out/"README.md").write_text("# 具身图谱 · 公共阅读站\n\n知识地图、研究介绍、论文解读与官方来源记录。此仓库只包含公共内容和零费用采集/构建工具，不包含个人学习目标、笔记、登录后台或账号数据库。\n\n每日云端工作流和首次手动验收见 [部署说明](scripts/DEPLOYMENT.md)。采集只访问公开官方元数据，不调用模型服务。私人空间由维护者独立保存，公共站不接收密码或笔记。\n\n参考 AIHOT 的公开界面节奏与模块思路，使用本项目自己的名称与视觉；本仓库不代表或隶属于 AIHOT。\n",encoding="utf-8")
    manifest=[]
    for path in sorted(out.rglob("*")):
        if path.is_file():
            manifest.append({"path":str(path.relative_to(out)),"bytes":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    (out/"PUBLIC-MANIFEST.json").write_text(json.dumps({"public_only":True,"excluded":[".runtime","app/server.py","learning goals and private notes","credentials"],"files":manifest},ensure_ascii=False,indent=2)+"\n")
    return {"staged":str(out),"files":len(manifest)+1,"public_only":True,"bytes":sum(x["bytes"] for x in manifest)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--db",type=Path,default=public_state.cli.DEFAULT_DB)
    args=parser.parse_args()
    print(json.dumps(stage(ROOT,args.out,args.db),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
