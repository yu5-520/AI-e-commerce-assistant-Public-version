import importlib
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

fixture = importlib.import_module("competition_contract_fixture_provider_v269")


def test_agent2_graph_shape_routes_without_collection_after_exact_identity_injection():
    packages = [
        {
            "packageId": "pkg:traffic",
            "DecisionGraph": {"kind": "DecisionGraph", "nodes": [], "edges": []},
            "partition": {"domain": "traffic", "actionKeys": ["DA1"]},
            "factValues": {"fact:roi:current": {"value": 2.0, "unit": "ratio"}},
        }
    ]
    assert fixture._graph_stage(packages, None) == "agent2"


def test_agent3_graph_shape_routes_without_collection():
    packages = [
        {
            "packageId": "pkg",
            "PlanGraph": {"kind": "PlanGraph", "nodes": [], "edges": []},
        }
    ]
    assert fixture._graph_stage(packages, None) == "agent3"


def test_explicit_collection_remains_authoritative():
    packages = [
        {
            "packageId": "pkg",
            "DecisionGraph": {},
            "partition": {},
            "factValues": {},
        }
    ]
    assert fixture._graph_stage(packages, "plans") == "agent2"
    assert fixture._graph_stage(packages, "sops") == "agent3"
