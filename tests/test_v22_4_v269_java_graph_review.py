"""Python-produced graph hashes and Java authority review/revision round trip."""
import json
import shutil
import subprocess
from pathlib import Path
import pytest
from src.services import v269_semantic_graph_service as graphs
from src.services.v26_revision_acceptance_service import verify_revision_result
from tests.test_v22_4_v269_semantic_graph import decision_raw, plan_node


def test_java_plan_review_keeps_action_scope_and_python_revision_hash(tmp_path):
    java=shutil.which('java')
    if not java:pytest.skip('Java runtime unavailable')
    decision=graphs.compile_graph('DecisionGraph',decision_raw(),evidence_refs=['fact:1'])
    admission=graphs.admit_actions(decision,['DA1','DA2']);parts=graphs.partition_actions(decision,admission)
    results=[]
    for part in parts:
        nodes=[{**plan_node('P'+key,key),'guard':{},'reviewWindow':{'durationSeconds':43200 if key=='DA1' else 86400}} for key in part['actionKeys']]
        results.append({'partitionHash':part['receiptHash'],'plan':{'nodes':nodes}})
    facts={'fact:1':{'value':0,'unit':'ratio'}}
    plan=graphs.merge_plans(decision,admission,parts,results,evidence_refs=['fact:1'],fact_values=facts)
    operation=graphs.compile_graph('OperationGraph',{'nodes':[{'nodeKey':'O1','kind':'OperationStage',
        'planActionRefs':['PDA1','PDA2'],'instruction':'执行方案','owner':'运营','executionObject':'商品p',
        'sequence':0,'rollback':'恢复原值','stopConditionRefs':[],'acceptanceActions':['检查记录']}]},upstream=plan)
    input_path=tmp_path/'input.json';input_path.write_text(json.dumps({'decision':decision,'plan':plan,'operation':operation,'facts':facts},ensure_ascii=False))
    harness=tmp_path/'V269ReviewProbe.java'
    harness.write_text('''package com.zcentury.v24;
import java.nio.file.*;
import java.util.*;
public class V269ReviewProbe {
 static void reseal(Map<String,Object> graph) {
  for(var raw:Json.array(graph.get("nodes"))) { var node=Json.object(raw);node.remove("nodeHash");node.put("nodeHash",Hashing.canonicalHash(node)); }
  graph.remove("graphHash");graph.put("graphHash",Hashing.canonicalHash(graph));
 }
 public static void main(String[] args) throws Exception {
  var input=Json.object(Json.parse(Files.readString(Path.of(args[0]))));
  var d=Json.object(input.get("decision"));var p=Json.object(input.get("plan"));var o=Json.object(input.get("operation"));
  var hash=(String)p.get("contractHash");
  var contract=ReviewContractAuthority.freezePlanAction("s/p","task",p,hash,"PDA2",Json.object(input.get("facts")),0L);
  if(!contract.deterministicSpec())throw new IllegalStateException(contract.unsupportedFields().toString());
  var first=ReviewContractAuthority.freezePlanAction("s/p","task",p,hash,"PDA1",Json.object(input.get("facts")),0L);
  if(first.reviewDueAtMillis()!=43200000L || contract.reviewDueAtMillis()!=86400000L)throw new IllegalStateException("review_windows_collapsed");
  var guarded=Json.object(Json.parse(Json.canonical(p)));
  Json.object(Json.array(guarded.get("nodes")).get(0)).put("guard",Map.of("stop","uncompiled company rule"));reseal(guarded);
  if(ReviewContractAuthority.freezePlanAction("s/p","task",guarded,hash,"PDA1",Json.object(input.get("facts")),0L).deterministicSpec())throw new IllegalStateException("unknown_guard_ignored");
  var extended=Json.object(Json.parse(Json.canonical(p)));
  Json.object(Json.array(extended.get("nodes")).get(0)).put("acceptanceCriteria",List.of(Map.of("metric","roas","constraint","expectedRange","minimumOrders",100)));reseal(extended);
  if(ReviewContractAuthority.freezePlanAction("s/p","task",extended,hash,"PDA1",Json.object(input.get("facts")),0L).deterministicSpec())throw new IllegalStateException("unknown_criterion_ignored");
  if(!contract.lowerGuard().keySet().equals(Set.of("PDA2:roas")))throw new IllegalStateException("action_scope_lost");
  var review=SystemReviewAuthority.evaluate(contract,new SystemReviewAuthority.Observation(86400000L,Map.of("PDA2:roas",0.0),Map.of()));
  Set<String> successful=new HashSet<>();
  for(var g:List.of(d,p,o))for(var raw:Json.array(g.get("nodes")))successful.add((String)Json.object(raw).get("nodeHash"));
  var scope=LocalSubgraphRevisionAuthority.planSemantic(contract,review,d,p,o,hash,successful);
  System.out.println(Json.canonical(scope));
  var wrong=new HashMap<String,Object>(p);wrong.put("graphHash","sha256:bad");
  try { ReviewContractAuthority.freezePlanAction("s/p","task",wrong,hash,"PDA2",Json.object(input.get("facts")),0L);throw new AssertionError("tampered_graph_accepted"); }
  catch(IllegalArgumentException expected){}
 }
}''')
    root=Path(__file__).resolve().parents[1]
    sources=sorted((root/'java-control-plane/src/main/java/com/zcentury/v24').glob('*.java'))
    build=tmp_path/'classes';build.mkdir()
    compile_result=subprocess.run([java,'-m','jdk.compiler/com.sun.tools.javac.Main','--release','17','-d',str(build),*[str(p) for p in sources],str(harness)],capture_output=True,text=True,timeout=60)
    assert compile_result.returncode==0,compile_result.stderr
    result=subprocess.run([java,'-cp',str(build),'com.zcentury.v24.V269ReviewProbe',str(input_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    scope=json.loads(result.stdout)
    assert scope['scopeMode']=='LOCAL'
    assert len(scope['reopenPlanNodeHashes'])==1
    assert len(scope['preservedPlanNodeHashes'])==1
    assert scope['breachedMetrics']==['PDA2:roas']
    for kind,graph in [('Decision',decision),('Plan',plan),('Operation',operation)]:
        assert verify_revision_result(graph,graph,scope,graph_kind=kind)['verified']
